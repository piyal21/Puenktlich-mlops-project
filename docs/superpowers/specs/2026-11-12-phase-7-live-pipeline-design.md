# Phase 7 — Live data pipeline: design

Status: **approved by owner 2026-11-12** (incl. the four §4 items) · Date: 2026-11-12 · Branch: `phase-7/live-pipeline`

## 1. Intent

Replace the sample board with **real departures**: every 15 minutes the 30 stations' live data comes
from the DB Timetables API, and every night yesterday becomes **labelled silver** with the exact
contract the history uses. A check against Hugging Face's own scrape proves the live data is complete.

Why: the public site should show what is really leaving now, and live silver is the input for the
monitoring and retraining loop (Phase 9). The biggest risk (CLAUDE.md, risk #1): the rule "no change
reported ⇒ on time" turns missed data into fake punctual trains. This phase must make gaps visible.

## 2. Goals, non-goals, anti-goals

**Goals**
- Website board shows real departures, labelled "Live", `data_as_of` < 20 min.
- Nightly silver `source=live` for the UTC day before, validated by the same Pandera contract as HF.
- A daily quality report per live day that records ingestion gaps (missed runs, missing plan hours,
  stations without departures) so training can exclude them, like the HF gap flags in Phase 3.
- Reconciliation against Hugging Face data for 3 overlapping days, with a documented threshold.
- Alarms: ingestion stopped, ingest errors, nightly ETL errors, station with no departures.
- Everything in Terraform; `tf-plan` no changes after apply; `tf-scan` clean or documented.

**Non-goals (later phases)**
- Monitoring, drift, health JSON, retraining on live data (Phase 9). GitHub Actions (Phase 8).
- Live-context model features (v2 stretch). Routing / A→B journeys.
- A live backfill of history (history stays HF + MinIO).

**Anti-goals (failure even if it "works")**
- Late trains labelled on time because ingestion missed them, without a flag.
- More than 60 calls/min to the DB API (account block) or API keys anywhere except `.env` and SSM
  SecureString (never in code, Terraform state, logs or chat).
- A public board that silently shows old data, or sample and live data mixed.
- Cost above the $1/month budget; anything billed per hour while idle.

## 3. Constraints and facts (measured 2026-11-02 unless marked)

| Fact | Consequence |
|---|---|
| DB API keys work (HTTP 200, 3 stations) | No owner action before building. |
| `fchg` per station: 119–210 KB XML, 29–44 KB gzipped (2 stations); reaches **11–18 h back** and 6–9.5 h ahead | A short outage loses no final delays. Bronze ≈ 1 MB per 15-min run (estimate). |
| `fchg` alone lacks plan data (only 17/20 `dp` have `pt`); join with `plan` on `s@id` | Plan cache needed. |
| `plan` per station-hour: 8–20 KB XML (~5 KB gz); `dp@ppth` lists the stops ahead | All-stops "Going to" is possible (§4 item 1). |
| HF `raw_data/year=/month=/day=/` already has November days (raw XML in `response_data`, ~52 MB files); the processed November month file is expected only in early December | Reconciliation can run within days, using our parser on HF raw XML (§8). |
| Lambda memory quota **512 MB**, account concurrency **5** | Jobs stay ≤ 512 MB (stream bronze, test early). ≤ 2 jobs run at once. |
| Free plan, budget $1/month; real Phase 6 cost still `TBD` | Cost estimate in §11; owner OK before AWS writes. |

Rules that bind this phase: TDD; `defusedxml` only; httpx timeouts, tenacity retries on 429/5xx/connection
errors only (max 3, exponential backoff + jitter); one IAM role per Lambda scoped to exact prefixes;
every AWS write and new dependency needs the owner's OK; label and silver schema unchanged.

## 4. Decisions

**Needs the owner's yes (in this approval)**
1. **"Going to" matches every stop ahead**, not only the final stop. Additive contract change: board
   rows get optional `via` (list of station names after this stop, from `dp@cpth` if changed, else
   `dp@ppth`), API `train.via: string[]` (empty for sample data). Silver schema unchanged.
2. **New dependencies:** `httpx` (now dev-only → runtime of the jobs image), `tenacity`, `defusedxml`;
   dev: `types-defusedxml`. All on the `rules.md` allow list. New extra `jobs` in `pyproject.toml`.
3. **AWS changes** (§9, each apply still shown first): 2 SSM SecureString parameters for the DB API
   keys, ECR repo `puenktlich-jobs`, Lambdas `ingest` + `etl_daily`, 2 schedules, alarms, and at
   cutover the sample-board schedule switched off (Lambda kept for rollback). Estimated
   +$0.10–0.30/month.
4. **Reconciliation threshold** (proposal, fixed after the first measurement with the owner):
   ≥ **95 %** of HF departures (30 stations) found in live silver by `event_id`, and ≥ **95 %**
   agreement on `is_late` for matched, non-cancelled rows.

**Claude's calls (owner may override)**
- **One `ingest` run every 15 min** instead of separate `plan` (hourly) and `changes` runs: it reads
  `fchg` for all 30 stations, then tops up the plan cache with missing hours within a call budget.
  One schedule, never two runs competing for the 60 calls/min limit.
- **Plan cache covers Berlin hours H−3 … H+6** (late trains stay visible; the UI's 6 h button is fully
  covered). Missing hours are fetched nearest-first, ≤ 70 plan calls per run (≤ 100 calls per run in
  total ≈ 2 min at 50/min). After an outage the cache is full again within about 1 h.
- **Parity check first (before any AWS change):** our parser on HF `raw_data` for a past day that is
  already in local HF silver (e.g. 2026-08-24; HF raw exists for August 2026, ~165 MB/day) must
  reproduce that HF silver: same `event_id`s, `delay_min`, `is_cancelled`, `final_destination`. This
  settles HF's rules (`fchg`-only stops, `pde` vs. last `ppth`, missing `ct`) **before** live code is
  final, so live silver matches the training data. Result documented; mismatches fixed in this phase.
- **Silver comes from plan stops only**, unless the parity check shows HF also keeps `fchg`-only
  (added) trains; then live does the same. Either way their count goes into the quality report.
- **Gap detection lives in the quality report**, not in silver (schema unchanged), using the **same
  fields training already reads** (`training/split.py::gap_masks`): `low_volume_hours` (Berlin local
  hours, same string format as HF) for hours where the run itself missed data for all stations, and
  `stations[].volume_drop_days` (UTC days) for a station with missing plan hours or failed `fchg`
  calls. A gap counts when no plan was fetched for that hour, or the longest `fchg` gap around it
  exceeds **3 h** (conservative; lookback measured 11–18 h at only 2 stations). Extra live-only
  fields: `fchg` runs expected (96) vs. succeeded per station, longest gap, stops never seen in
  `fchg`, `fchg`-only stops, stations with zero departures.
- **Below the threshold:** I investigate with the per-station numbers, fix, and re-measure once in
  this phase (≈ 3 more nights). If it is still below for a reason on HF's side (e.g. HF polls `fchg`
  too rarely), you decide with the numbers whether to document it and exit.
- **Separate `jobs` image** (architecture §2): base deps + `jobs` extra, no FastAPI/LightGBM. Phase 9
  adds Evidently to it.
- **Metrics via Powertools EMF** (log lines, no `cloudwatch:PutMetricData` permission): `EventsIngested`
  (per run), `EmptyStations` (per ETL day). 2 custom metrics, total alarms 6 of 10.
- **Safe cutover:** `ingest` first writes the board to `live/boards/candidate.json.gz` (env var) while
  the sample board stays public; after one good day the env var flips to `latest.json.gz`. A
  Terraform variable `board_mode = "live" | "sample"` decides which writer has a schedule (and alarm):
  the sample-board Lambda, `seed_handler` and `samples/` data stay, so going back is one variable
  and one apply. Only the schedule that is not in use is removed.
- **Secrets:** Terraform creates the two SecureString parameters with a placeholder value and
  `ignore_changes = [value]`; `make put-db-api-secrets` copies the values from `.env` into SSM via
  boto3 (`aws/ssm` key, never printed). Lambdas read them once per cold start.
- **Local first:** `make ingest-local` runs one real ingest against MinIO (keys from `.env`) before
  anything touches AWS; `make etl-live-local DAY=…` runs the ETL on MinIO bronze.

## 5. Target architecture

```
EventBridge Scheduler (Europe/Berlin)
 ├─ every 15 min ─► Lambda ingest (jobs image, 512 MB, 5 min)
 │                   ├─ GET /fchg/{eva} ×30, GET /plan/{eva}/{YYMMDD}/{HH} (missing hours, ≤ 70)
 │                   ├─ bronze/timetables/kind=fchg|plan/date=<UTC run date>/run=<ISO-ts>.jsonl.gz
 │                   ├─ live/plans/latest.json.gz   (parsed plan cache, H−3…H+6)
 │                   └─ live/boards/latest.json.gz  (LiveBoard, source "live")  ─► API (unchanged)
 └─ 03:30 daily ──► Lambda etl_daily (jobs image, 512 MB, 10 min)
                     ├─ reads bronze plan (run dates D−2…D) + fchg (D−1…now) for UTC day D−1
                     ├─ silver/departures/source=live/date=D−1/part-0.parquet
                     └─ silver/_quality/source=live/date=D−1.json
Laptop: make reconcile DAYS=… ─► HF raw XML (30 stations) vs live silver ─► report (local + S3)
```

## 6. Code (Python, test-first)

New package `src/dbdelay/timetables/`:
- `client.py` — `TimetablesClient` (httpx, base URL and headers per architecture §8, timeout 10 s,
  tenacity on 429/5xx/connection errors, max 3 attempts). Returns status + body; never logs headers.
- `ratelimit.py` — `RateLimiter(calls_per_min=50, clock, sleep)`; injectable clock for tests.
- `parser.py` — `defusedxml` parsing of `plan` and `fchg` into typed stop records: `s@id` → `ride_id`
  + `stop_index`; `tl@c/n`; `dp@pt/ct/pp/cp/l/ppth/cpth/cs/pde`; Berlin `YYMMDDHHmm` → UTC with the
  same DST rule as HF (ambiguous/nonexistent → dropped and counted). Arrival-only stops are ignored.
- `bronze.py` — one JSONL.gz per run (one line per call: kind, eva, hour, requested_at, HTTP status,
  raw XML or error) and a streaming reader.

New package `src/dbdelay/live/`:
- `plans.py` — plan cache model + merge/prune + "which hours are missing".
- `board.py` — plan cache + this run's `fchg` → `LiveBoard` (`source="live"`): rows with
  `leaves_at ≥ now − 30 min` and planned ≤ now + 6 h; delay = `ct − pt`; `cs="c"` → cancelled;
  platform `cp` else `pp`; `final_destination` = last `ppth` entry (builder checks HF semantics, incl.
  `pde`, against reconciliation). If a station's `fchg` call fails, its rows come from the previous
  board and the run counts it as failed.
- `ingest.py` — one run: limiter → fchg ×30 → plan top-up → bronze → cache → board → result
  (`calls`, `failed_stations`, `events`). Partial success is OK; zero successful stations raises.
- `etl.py` — bronze → silver for one UTC day, streaming (one bronze object at a time, keeping only the
  latest observation per stop). Same `event_id`, label and validation (`validate_silver`) as HF;
  `ingested_at` = run time of the latest observation used. Overwrites the day (idempotent).
- `quality.py` — `LiveQualityReport` (pydantic) with the gap fields from §4.
- `reconcile.py` — HF raw XML (filtered to our 30 EVAs, parsed with the same parser) vs. live silver:
  match rate both ways, `is_late` agreement, delay difference distribution, unmatched samples.

Changed: `serving/board.py` (`via` optional, default empty), API response model + frontend filter
(`via` + destination; label "Going to"), `config.py` (DB API SSM names, board key, plan budget),
`pyproject.toml` + lock. Handlers: `services/jobs/ingest_handler.py`, `etl_handler.py` (thin) and
`services/jobs/Dockerfile` (same base image digest and hash-locked requirements as the API image).

## 7. Terraform

- `ecr`: second instance `puenktlich-jobs` (same rules: immutable tags, scan on push, keep 3).
- `ssm`: `/puenktlich/prod/db_api/client_id`, `/…/api_key` (SecureString, placeholder, ignore value).
- `lambda_image`: `ingest` (timeout 300 s) and `etl_daily` (timeout 600 s), both 512 MB, own roles:
  ingest → `GetObject/PutObject` on `bronze/timetables/*`, `live/plans/*`, `live/boards/*`,
  `ssm:GetParameter` on `/puenktlich/prod/db_api/*`; etl → `ListBucket` (prefix `bronze/timetables/`),
  `GetObject` there, `PutObject` on `silver/departures/source=live/*` and `silver/_quality/source=live/*`.
  (Builder checks whether `aws/ssm` decrypt needs an explicit `kms:Decrypt` with `kms:ViaService`.)
- `schedules`: `ingest` `rate(15 minutes)`, retry 0; `etl_daily` `cron(30 3 * * ? *)` Europe/Berlin,
  retry 1. `board_mode` picks the board writer's schedule (ingest board key vs. sample board).
- `observability`: "ingestion stopped" (`EventsIngested` sum 0 over 60 min, missing = breaching),
  "ingest errors" (Errors > 0 in 2 consecutive 15-min periods), "ETL errors" (Errors > 0, 1 day),
  "empty stations" (`EmptyStations` > 0). The sample-board alarm exists only in `board_mode = "sample"`.
- Data lake: bronze expiry 30 days already exists; no other change.

## 8. Reconciliation

- Source: HF `raw_data` days that overlap live silver (download only the files for those days to
  git-ignored `data/raw/hf_raw/`). Builder first reads the parquet schema (columns, which endpoints
  are included, how often HF polls `fchg`).
- Both sides go through **our** parser and the same "latest observation" rule, so the check measures
  ingestion completeness (risk #1). Parser semantics vs. HF's own processing are covered by the
  parity check (§4) on an August day. When HF's processed November file arrives
  (~early December), the same comparison runs against it as a second check (follow-up, not an exit
  criterion).
- Output: `data/reconciliation/<day>.json` and `silver/_quality/source=live/reconciliation/date=<day>.json`;
  the measured numbers go into the phase doc and CHANGELOG.

## 9. Rollout (each AWS write shown to the owner first)

1. Local: parity check on an August HF raw day, `make ingest-local` (real API → MinIO),
   `make etl-live-local`, all checks green.
2. AWS stage A: SSM parameters + jobs ECR → `make put-db-api-secrets` → `make jobs-image-push`.
3. AWS stage B: `ingest` (board → `candidate` key) + `etl_daily` + schedules + alarms; API image with
   `via` support + `make web-deploy`.
4. After ≥ 1 good day: flip the board key to `latest.json.gz` and `board_mode = "live"` (one apply);
   `make smoke-prod` checks `data_source = live` and `data_as_of` < 20 min.
5. After 3 nightly ETL days: `make reconcile`, threshold check, docs.

## 10. Testing

- Unit (TDD): parser on `tests/fixtures/sample_plan.xml` / `sample_fchg.xml` + hand-made edge cases
  (cancelled, changed platform/path, DST, missing `pt`, malformed XML, XML bombs rejected); client with
  `httpx.MockTransport` (retries on 429/5xx only, no retry on 4xx, timeout); limiter with a fake clock;
  ingest/ETL/quality with moto S3 and a fake client; board contract (`via` optional, old boards valid);
  frontend filter tests for `via`; reconciliation on tiny frames.
- Integration: one real `make ingest-local` run (manual, not in CI), jobs image in the Lambda emulator.
- Infra: `make tf-plan`, `make tf-scan`; prod: `make smoke-prod` extended for live data.
- Measured and documented: run duration, calls per run, ETL duration and peak memory on a real day.

## 11. Cost (estimate; real figures from Cost Explorer)

Lambda ≈ 0.5 GB × ~80 s × 2,880 runs + ETL ≈ 120k GB-s/month (inside the always-free 400k) → $0.
S3 bronze ≈ 3 GB at 30-day retention ≈ $0.07; ECR jobs image ≈ $0.05–0.10; requests, logs, 2 custom
metrics, 4 alarms ≈ $0–0.10. **Total +$0.10–0.30/month** (estimate, not measured).

## 12. Docs to update

`architecture.md` §3.2 (candidate key, live quality/reconciliation paths), §7 (`via`), §8 (single
run + plan top-up, budgets), §9 (alarms), §11, §16; `phases.md` Phase 7; `prd.md` §6.3 (`via`);
README; CLAUDE.md; CHANGELOG; `Phases/phase-7-live-pipeline.md` + `Phases/README.md`.

## 13. Exit criteria (phases.md)

- Public board shows real departures, "Live", `data_as_of` < 20 min.
- 3 consecutive days of live silver written and validated, each with a quality report.
- Reconciliation ≥ the agreed threshold, numbers documented.

## 14. Left to the builder

Exact module/function names, JSON layout of the plan cache and bronze lines, limiter algorithm,
how the board reuses the previous board on a failed station, Makefile target names beyond those
listed, test fixture design, CloudWatch alarm periods within the stated intent.

## 15. Open for later (not Phase 7)

Training on live silver (Phase 9 gold builder must read live quality flags); comparison with HF's
processed November file; `fchg`-only added trains if reconciliation shows they matter; PRD latency
and speed decision from Phase 6; Phase 8 GitHub→AWS route.
