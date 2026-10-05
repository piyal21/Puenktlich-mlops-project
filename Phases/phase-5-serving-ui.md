# Phase 5 — Local serving & UI

**Goal:** the whole user experience runs on a laptop. You open `http://localhost:5173`, search a station, see the
next departures with a delay-risk badge each, and tap one to see the probability and the top 3 reasons, all served
by the Phase 4 champion model from MinIO.

**Status:** ✅ done on branch `phase-5/serving-ui` (2026-10-05). The branch is kept.

---

## 1. What was built

| Deliverable | File(s) | What it is |
|---|---|---|
| Champion loader | [`src/dbdelay/serving/model_loader.py`](../src/dbdelay/serving/model_loader.py) | Reads `models/_pointer.json` at most every 5 min, loads the bundle through the checksum-checking loader, keeps one loaded model per version. A tampered or missing model means "no forecasts" (fail closed); a short MinIO outage keeps the last good model. |
| Live board | [`src/dbdelay/serving/board.py`](../src/dbdelay/serving/board.py) | The file format of `live/boards/latest.json.gz` (schema v1), a 60 s cache, and "which departures of station X leave in the next N hours". Phase 7's real ingestion will write the same format. |
| Scoring | [`src/dbdelay/serving/scoring.py`](../src/dbdelay/serving/scoring.py) | Turns departures into `p_late`, Low / Medium / High (thresholds 20 % / 45 % from the model's `feature_spec.json`) and reasons; one JSON log line per scored departure. Features only through `build_features` (same code as training). |
| Reasons | [`src/dbdelay/serving/explain.py`](../src/dbdelay/serving/explain.py) | LightGBM's per-feature contributions → the 3 biggest → short plain sentences ("IC trains are often late here", "Late stop in a long journey…"). |
| Station search | [`src/dbdelay/serving/stations.py`](../src/dbdelay/serving/stations.py) | Ignores case, accents and umlaut spelling: `munchen`, `MUENCHEN` and `München` all find München Hbf. |
| Sample data | [`src/dbdelay/serving/seed.py`](../src/dbdelay/serving/seed.py), [`scripts/seed_sample_data.py`](../scripts/seed_sample_data.py), `make seed` | There is no live data until Phase 7, so `make seed` takes a real day from the Phase 2 history (same weekday as today), moves it onto today (keeping the local clock times, so summer/winter time is handled) and writes it as the board, labelled "sample". |
| API | [`services/api/app/`](../services/api/app/) | FastAPI: `/api/v1/health`, `/stations`, `/stations/{eva}/departures`, `/predict`, `/model`; docs at `/api/docs`. Errors are `application/problem+json` with a request id; bodies over 4 KB are refused. Routers are thin; logic lives in `dbdelay.serving`. |
| API image | [`services/api/Dockerfile`](../services/api/Dockerfile), [`lambda_handler.py`](../services/api/lambda_handler.py), `make api-requirements` | One image, two targets: `lambda` (AWS Lambda Python 3.12 base + Mangum, ready for Phase 6) and `local` (+ uvicorn on :8000). Dependencies installed from hash-pinned files exported from `uv.lock`. |
| Slim serving path | [`src/dbdelay/training/report.py`](../src/dbdelay/training/report.py), [`tests/unit/test_serving_imports.py`](../tests/unit/test_serving_imports.py) | The model loader no longer pulls in scikit-learn (only training does); a test imports every serving module with scikit-learn blocked. |
| Web app | [`frontend/`](../frontend/) | React 19 + Vite + TypeScript (strict) + Tailwind v4 with the `design.md` tokens. Board (search, recent stations, 1/3/6 h, every data state), detail sheet (probability bar with the 20 %/45 % marks, "Why?" list), Model health (real test metrics), About. Light/dark theme. |
| Local stack | [`docker-compose.yml`](../docker-compose.yml) profile `app`, `make app-up` / `app-down` / `api-dev` / `web-check` | `api` (uvicorn) + `frontend` (Node 24 running Vite on :5173, forwarding `/api` to the API). |
| MLflow telemetry | `tracking.py`, `docker-compose.yml`, `Makefile` | Turned off (owner decision 2026-10-05). |

## 2. How it works

```text
make seed:  silver day (MinIO) ──replay onto today──► live/boards/latest.json.gz   (source = "sample")

browser :5173 ──/api──► Vite proxy ──► FastAPI :8000
                                        │  GET /stations/{eva}/departures?hours=3
                                        ├─ BoardSource (60 s cache) ──► board file
                                        ├─ select this station, next 3 h
                                        └─ ModelProvider (pointer re-read every 5 min)
                                              └─ load_bundle: SHA-256 of every file checked
                                        build_features → LightGBM → isotonic calibration → p_late
                                        pred_contrib → top 3 reasons → risk level → JSON
```

Without a champion (no pointer, or a model that fails its checksums) the board still lists departures, each with
"No forecast", and the UI shows "Forecasts are temporarily unavailable"; `/predict` and `/model` answer 503.

## 3. Key decisions and why

| Decision | Why |
|---|---|
| Sample board = a real silver day replayed onto today (owner) | Realistic boards for all 30 stations; the UI says "Sample data: real departures from <date> replayed onto today". |
| Keep scikit-learn out of the serving import path (owner) | The Lambda image should stay slim; LightGBM still brings SciPy. |
| Compose profile `app` with a Node container for Vite (owner) | Matches the exit criterion (`localhost:5173`) with one command. |
| Health page shows real test metrics only (owner) | Daily monitoring arrives in Phase 9; no placeholder numbers. |
| Board `prediction` carries `top_factors`; board carries `data_source` + `replayed_from` (owner, additive API change) | The detail sheet needs no second request; sample data is always labelled honestly. |
| Cancelled departures get no forecast | The label excludes cancelled trains (ADR 0001), so a probability would mean nothing. |
| Reasons from raw LightGBM contributions | Calibration is monotone, so "raises / lowers the risk" stays true for the calibrated probability. |
| Times in the API use the Berlin offset (`+02:00` / `+01:00`) | Matches architecture §7 and what travellers read on the platform. |
| On phones the risk badge sits under the destination | Long station names ("Berlin Gesundbrunnen") were cut off at 360 px. |
| Storage errors keep the current model and retry after 15 s; checksum failures turn forecasts off | A network blip should not hide forecasts; a tampered model must never be used. |
| Light Low-risk colour `#117B58` instead of `#12805C` | The original failed WCAG AA (4.3:1) on its badge background. |

## 4. How it was tested (real results, 2026-10-05)

| Check | Result |
|---|---|
| `make check` (ruff, mypy strict incl. `services/api`, unit tests) | **402 passed**, coverage **97 %** (≈ 4 min) |
| `make web-check` (ESLint, Prettier, `tsc`, Vitest) | **47 passed** (incl. a contrast test for every badge colour pair) |
| `make test-integration` (real MinIO + MLflow) | **8 passed** (≈ 71 s), incl. seed + API on the real champion v1 |
| `make test-dags` | DAG check passed |
| `make seed` (real silver) | 3,376 departures at 30 stations, replayed from 2026-08-24 |
| Lambda image in the Lambda runtime emulator | `statusCode` 200 for `/api/v1/health` |
| Compose `api` (local image) | champion v1 loaded; Frankfurt board scored, e.g. NJ 402 → 39.6 % Medium with 3 reasons |
| API image size | 1.58 GB (both targets) |
| No champion (API pointed at an empty models bucket) | board `model_version: null`, departures listed; `/model` → 503 problem+json with request id |
| Browser (headless Chromium, 360 / 390 / 1280 px) | board, detail sheet (focus on Close, 3 reasons, 20 %/45 % marks), health and about pages render; no horizontal scroll at 360 px |
| Accessibility audit (WCAG 2.1 AA) | one failure fixed: the Low badge colour was 4.3:1, now 4.6:1 (`#117B58`) |
| Independent final review | 0 Critical; 4 Important fixed test-first (model kept on storage blips, far `/predict` dates → 422, detail sheet follows the current board, Lambda is the default image target); minors listed in CHANGELOG |

Unit tests cover every rule on its own: pointer TTL and version switch, tampered bundles, MinIO outages, board
contract violations, window edges, DST replay, risk thresholds, reason order and sign, every route and error type,
request-id handling, body limits, CORS, and the Lambda handler. Frontend tests cover the risk badge, departure row,
detail dialog (focus, Escape, Tab trap, scroll lock), combobox keyboard flow, all board states and the health page.

## 5. How to run it

```bash
make up                         # MinIO, Postgres, MLflow
make seed                       # sample live board (needs Phase 2 silver in MinIO)
make app-up                     # API :8000 (docs /api/docs) + web app :5173
# open http://localhost:5173 and search e.g. "erfurt"
make app-down                   # stop (data kept)
```

Development: `make api-dev` (API on the host with reload), `npm --prefix frontend ci` once, then
`npm --prefix frontend run dev` (Vite on the host) and `make web-check`. The sample board counts as stale after
20 minutes (the UI says so); run `make seed` again to refresh it.

## 6. Known gaps

See the Phase 5 entry in [`CHANGELOG.md`](../CHANGELOG.md) for the full list (including review minors). The main ones:

- Sample data only; real live boards arrive with Phase 7 ingestion. Silver has no platform, so "Platform not known yet".
- The model was trained without autumn months, so October–November forecasts may be less accurate (About page says so).
- The API image is 1.58 GB (pandas, PyArrow, DuckDB, LightGBM + SciPy).
- Starlette's test client warns that it wants `httpx2`; not added (would be a new dependency).

## 7. What's next

Phase 6: Terraform for AWS (S3, Lambda from the `lambda` image target, API Gateway, CloudFront, SSM pointer).
