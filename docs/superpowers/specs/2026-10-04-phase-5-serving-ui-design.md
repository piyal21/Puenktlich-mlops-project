# Phase 5 — Local serving & UI: design

**Status:** approved in conversation 2026-10-04 (owner). Branch `phase-5/serving-ui`.
**Authority:** `docs/architecture.md` §2, §3.2, §6, §7, §13, §14; `docs/prd.md` §6.3, §7; `docs/rules.md` §4, §6, §7, §9;
`docs/design.md`; `docs/phases.md` Phase 5.

## 1. Goal and success criteria

The full user experience works on the laptop against MinIO: a FastAPI service scores a (seeded) live board with
the champion model, and a React app shows it.

Done when:
- `make seed` writes `live/boards/latest.json.gz` (real departures replayed onto today) into MinIO.
- `make app-up` (compose profile `app`) → `http://localhost:5173` → search a station → board with risk badges
  (icon + word + %) → open a departure → probability bar and the top 3 reasons.
- Without a champion (no pointer, or a bundle that fails verification) the API stays up: the board returns live
  departures with `prediction: null`, `POST /api/v1/predict` and `GET /api/v1/model` return 503 problem+json, and the
  UI shows "? No forecast" plus the "Forecasts are temporarily unavailable" banner.
- `services/api/Dockerfile` builds a Lambda image (`lambda` target) and a local image (`local` target, uvicorn).
- Python unit + integration tests and frontend component tests pass; accessibility review done.

Out of scope: AWS/SSM pointer, CloudFront, rate limiting (Phase 6); real live ingestion (Phase 7); CI (Phase 8);
monitoring data, Recharts and the health JSON files (Phase 9); an API check inside the training DAG's smoke test
(the API runs as its own service; the integration test covers API + real champion instead).

## 2. Owner decisions (2026-10-04)

| Decision | Choice |
|---|---|
| Seed source | A real silver day from MinIO (same weekday as today, plus the next day), shifted onto today with wall-clock times kept. |
| Slim API image | Small refactor so `dbdelay.serving` and `dbdelay.registry.artifacts` never import scikit-learn; enforced by a test. |
| Local frontend | Compose profile `app`: `api` (uvicorn) + `frontend` (Node container running Vite on :5173, proxy `/api`). |
| Health page | Real champion metrics from `/api/v1/model`; charts show an empty state until Phase 9. No placeholder numbers. |
| API contract (additive) | Board `prediction` carries `top_factors`; board response carries `data_source` (`sample`/`live`) and `replayed_from`. |
| Board file contract | New `live/boards/latest.json.gz` schema version 1 (§4); Phase 7 ingestion writes the same format. |
| New dependencies | Python: `fastapi`, `mangum` (new `api` extra, + `lightgbm`); `uvicorn`, `httpx` (dev group + local image). npm: §9. |

## 3. Components

```
src/dbdelay/serving/
  model_loader.py   ModelProvider: pointer (TTL 5 min) → load_bundle (verified) → cache per version
  board.py          LiveBoard contract, BoardSource (TTL 60 s), select_departures, score_departures
  explain.py        top_factors(bundle, features, k=3) → [{feature, direction, text}]
  seed.py           replay_board(silver_rows, now, …) → LiveBoard   (pure)
src/dbdelay/training/report.py   report models moved out of evaluate.py (no sklearn)
services/api/
  app/main.py       create_app(deps) — middleware, error handlers, routers, /api/docs
  app/deps.py       ServingDeps (stores, stations, provider, board source, clock, settings)
  app/schemas.py    request/response models (mirror of frontend/src/api/types.ts)
  app/errors.py     problem+json + exception mapping
  app/routers/      health.py  stations.py  departures.py  predict.py  model.py
  lambda_handler.py Mangum(create_app())
  Dockerfile        targets `lambda` (default) and `local`
scripts/seed_sample_data.py      thin CLI → dbdelay.serving.seed
frontend/                        Vite + React 19 + TS strict + Tailwind v4 (§8)
```

### Data flow
```
make seed: silver (MinIO) ─replay_board─▶ live/boards/latest.json.gz
request ─▶ router ─▶ BoardSource.get() ─▶ select_departures(eva, now, hours)
                  └▶ ModelProvider.get() ─▶ bundle.predict + top_factors ─▶ risk level ─▶ response
React (TanStack Query, refetch 60 s) ─▶ Vite proxy /api ─▶ api :8000
```

## 4. Live board file — `live/boards/latest.json.gz` (schema version 1)

Gzipped UTF-8 JSON, one object for all stations, validated with Pydantic (`LiveBoard`, `extra="forbid"`):

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `1` | |
| `generated_at` | datetime (UTC) | becomes `data_as_of` in the API |
| `source` | `"sample"` \| `"live"` | `sample` = written by `make seed` |
| `replayed_from` | date \| null | source day of a sample board; null for live |
| `departures` | list | silver-shaped rows (below) |

Departure row: `event_id`, `eva`, `station_name`, `ride_id`, `stop_index` (≥ 1), `train_type`, `train_number`?,
`line_number`?, `final_destination`?, `planned_departure_utc`, `changed_departure_utc`?, `delay_min`?,
`is_cancelled`, `platform`? (`?` = nullable). Times tz-aware UTC; naive times are rejected.
A board that is missing or fails validation → `BoardSource` raises `ExternalServiceError` → 503
`/errors/board-unavailable` (§6), never a crash.

Seed (`replay_board`): pick the most recent silver date with the same weekday as today in Europe/Berlin
(`--date` overrides) and the day after it; move every row onto today/tomorrow keeping the **local wall-clock time**
(DST-safe calendar shift, not a fixed timedelta); recompute `event_id` from the shifted planned time; keep rows with
planned departure in `[now − 30 min, now + 6 h]`; `platform = null` (silver has none). Delay/cancel values are the
real ones of the source day. Writes with `source="sample"`, `replayed_from=<date>`.

## 5. Model loading and scoring (`serving/`)

- `ModelProvider(store, pointer, ttl_s=300, clock)`: re-reads the pointer at most every 5 min; loads a new version
  with `load_bundle` (all checksums verified, fail closed) and keeps the last good bundle per version. No pointer or
  a failed load → `ModelNotAvailableError` (the failure is logged; the next pointer read retries). Thread-safe (lock).
  `current_version()` returns the loaded version or `None` without raising (for `/health`).
- `BoardSource(store, key, ttl_s=60, clock)`: cached `LiveBoard`.
- `select_departures(board, eva, now, hours)`: rows of that station with
  `max(planned, changed) ≥ now` and `planned ≤ now + hours`, sorted by planned time.
- Scoring: one batch per request — `bundle.predict(rows)` (features only via `build_features`) and
  `booster.predict(X, pred_contrib=True)` for factors. Risk level from `spec.risk_thresholds`
  (`p < medium` → low, `< high` → medium, else high). Cancelled departures get `prediction: null`.
- Explanations: top 3 features by |contribution| (raw log-odds space; isotonic calibration is monotone so the
  direction holds). `direction = "up"` if > 0. Plain-English text per (feature, direction) from a fixed table in
  `explain.py`, written with the `design:ux-copy` skill; feature names are the `FEATURE_COLUMNS` names.
- Prediction log: one Powertools JSON line per scored departure (`event_id`, `eva`, `model_version`, `p_late`,
  `risk_level`, `latency_ms`, `request_id`).
- Slim import closure: `dbdelay.training.report` holds `Metrics`, `CalibrationBin`, `SplitReport`,
  `EvaluationReport`, `TrainingReport`; calibrator `apply`/JSON stay importable without sklearn and only fitting
  imports it. A unit test imports `dbdelay.serving.*` and `services/api` in a subprocess with `sklearn` blocked.

## 6. API (architecture §7 + the additive changes)

| Route | Success | Errors |
|---|---|---|
| `GET /api/v1/health` | 200 `{status: "ok", model_version \| null, board_generated_at \| null}` | never fails |
| `GET /api/v1/stations?q=` | 200 list `{eva, name, state}`; `q` matched case- and umlaut-insensitively (`munchen` → München); no `q` → all | 422 `q` > 50 chars |
| `GET /api/v1/stations/{eva}/departures?hours=3` | 200 §7 body + `data_source`, `replayed_from`, `prediction.top_factors` | 422 eva not 7 digits / hours ∉ 1–6; 404 station not supported; 503 board unavailable |
| `POST /api/v1/predict` | 200 §7 body | 422 invalid body or naive datetime; 404 station; 413 body > 4 KB; 503 no model |
| `GET /api/v1/model` | 200 `{model_name, version, previous_version, trained_at, data_snapshot_id, git_sha, train_window, metrics{test_brier, test_auc, test_pr_auc, test_log_loss, test_ece, baseline_brier}, risk_thresholds}` | 503 no model |

- Times in responses: ISO 8601 with the Europe/Berlin offset (`17:42:00+02:00`); `live_departure` = changed time,
  `live_delay_min` = `delay_min`. `stale = now − generated_at > 20 min`.
- Errors: `application/problem+json` `{type, title, status, detail, instance, request_id}`; `type` values
  `/errors/station-not-supported`, `/errors/validation`, `/errors/payload-too-large`, `/errors/model-unavailable`,
  `/errors/board-unavailable`, `/errors/internal`. Unexpected exceptions → 500 without stack trace (logged with it).
- Request id: incoming `X-Request-ID` reused if it matches `^[A-Za-z0-9-]{1,64}$`, else a new uuid4; echoed in the
  response header, problem bodies and logs.
- CORS only for origins in `CORS_ORIGINS` (compose: `http://localhost:5173`); empty in prod.
- New settings: `model_pointer_ttl_s=300`, `board_ttl_s=60`, `board_key="live/boards/latest.json.gz"`,
  `board_stale_after_s=1200`, `cors_origins=[]`. The pointer stays `ObjectStorePointer` on `MODELS_BUCKET`.
- Routers stay thin: parse → call `dbdelay.serving` → map to schema. `create_app(deps)` makes tests inject fakes and
  a fixed clock.

## 7. Docker and compose

- `services/api/Dockerfile`: base `public.ecr.aws/lambda/python:3.12` (pinned tag), deps from
  `uv export --frozen --no-dev --extra api` installed with `--require-hashes`, then the `dbdelay` package and
  `services/api`. Target `lambda` → CMD `lambda_handler.handler`. Target `local` → + uvicorn (hash-pinned),
  entrypoint `uvicorn app.main:app --host 0.0.0.0 --port 8000`, non-root user. `.dockerignore` excludes `.env`,
  `.git`, data, notebooks, `frontend/node_modules`.
- Compose profile `app`: `api` (build target `local`, `127.0.0.1:8000`, env like Airflow's storage settings,
  `CORS_ORIGINS`) and `frontend` (pinned Node LTS image, bind mount `./frontend`, named volume for `node_modules`,
  `npm ci && npm run dev -- --host 0.0.0.0`, `127.0.0.1:5173`, `VITE_API_PROXY=http://api:8000`, polling file watch
  for Windows bind mounts).
- Make: `seed`, `app-up`, `app-down`, `api-dev` (host uvicorn with reload), `web-check` (lint + typecheck + test).

## 8. Frontend (`design.md`)

- Stack: React 19, Vite, TS strict, Tailwind v4 with the `design.md` §11 tokens, TanStack Query, React Router,
  lucide-react, self-hosted Inter + JetBrains Mono. ESLint + Prettier. `package-lock.json` committed.
- Layout: `src/api/{client.ts,types.ts}` (types mirror `schemas.py`), `src/hooks/` (`useStations`,
  `useDepartures` with 60 s refetch and last-good data kept, `useModel`), `src/components/`, `src/pages/`,
  `src/lib/` (Berlin time formatting, recent stations in `localStorage` with try/catch), `src/styles/tokens.css`.
- Pages: Board (`/`, `/station/:eva`, hours 1/3/6, "Updated 15:30" + live dot, sample-data banner), Health
  (`/health`: MetricCards from `/api/v1/model`, empty state "Daily monitoring starts in a later phase"), About.
- Components: RiskBadge, DepartureRow, DepartureDetail (bottom sheet < 1024 px, side panel ≥ 1024 px, focus trap,
  Esc), ProbabilityBar (ticks at the thresholds from `/api/v1/model`), StationSearch (WAI-ARIA combobox, ↑ ↓ Enter
  Esc, ≤ 5 recent chips), Banner (info/stale/error), TopBar, Footer (attribution, not-affiliated note, theme toggle).
- States on every data view: loading (skeleton rows), empty (+ "Show 6 hours"), error (+ "Try again", last good
  data kept), stale (> 20 min), no model.

## 9. New npm dependencies

Runtime: `react`, `react-dom`, `react-router`, `@tanstack/react-query`, `lucide-react`,
`@fontsource-variable/inter`, `@fontsource/jetbrains-mono`, `tailwindcss`, `@tailwindcss/vite`.
Dev: `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `jsdom`, `@testing-library/react`,
`@testing-library/user-event`, `@testing-library/jest-dom`, `eslint`, `@eslint/js`, `typescript-eslint`,
`eslint-plugin-react-hooks`, `prettier`, `@types/react`, `@types/react-dom`. Recharts waits for Phase 9.

## 10. Testing

- Unit (moto, fake clock, `tests/bundles.py` bundle): ModelProvider (TTL, version switch, fail closed on tampered
  bundle, no pointer, recovery after a fix); LiveBoard contract (naive time, extra field, bad version);
  select_departures (window edges, delayed train still shown, other station); risk levels at the thresholds;
  top_factors (k, direction, every feature has text for both directions); replay_board (DST weekend shift,
  window, event_id recomputed, wall clock kept); every route with TestClient incl. problem+json shape, request id
  echo, 404/413/422/503, board with no model → `prediction: null`, cancelled → `prediction: null`, stale flag;
  sklearn-free import check.
- Integration (`@pytest.mark.integration`): seed against MinIO, then the app in-process against the real champion
  (v1) → departures with predictions and factors.
- Frontend (Vitest + Testing Library): RiskBadge (all variants + aria-label), DepartureRow (delayed, cancelled),
  board states (loading/empty/error/stale/no model), StationSearch keyboard flow.
- Manual exit check (`run` skill): compose `app` profile up, browser at :5173; `design:accessibility-review` and
  `design:design-critique` on the running UI; real observations recorded in the phase doc.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Vite file watching on Windows bind mounts | polling watch; `make` target to run Vite on the host as fallback (`npm run dev`) |
| Sample board goes stale after 20 min | intended — exercises the stale state; `make seed` refreshes |
| `month` feature unseen for Sep–Nov (Phase 3 note) | numeric feature → trees treat Oct like the nearest trained months; stated as a limitation on About |
| Lambda base image + uv export drift | hash-pinned requirements generated from `uv.lock`; image build in the plan's checks |
| Prediction log volume (one line per departure) | fine locally; CloudWatch retention 14 d in Phase 6 |
