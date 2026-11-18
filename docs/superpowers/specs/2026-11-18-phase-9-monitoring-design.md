# Phase 9 — Monitoring & continuous training: design

Status: **builder-checked, decisions by Claude under the owner's standing OK** · Date: 2026-11-18 · Branch: `phase-9/monitoring`
Owner standing instruction (2026-11-14, repeated 2026-11-18): decide without asking, ask only about cost.
Every decision below that the owner did not make is marked **(Claude's call)**.

## 1. Intent

The system should watch its own model every day and retrain when the model goes stale, and it
should show this publicly on the Model Health page. Since Phase 7 the live pipeline produces labelled
silver for every day (`source=live`), so for the first time we can score **all of yesterday's real
departures** and compare the predictions with what really happened.

Why now: the champion (v1) was trained on Hugging Face history that ends 2026-08-31. Phase 7's
reconciliation showed that HF history probably **undercounts lateness** (live sees later delays in
95 % of the rows where the two differ). If that is true, the live Brier score will be worse than the
test Brier and the calibration will be too optimistic. Phase 9 makes that visible and closes the loop.

## 2. Goals, non-goals, anti-goals

**Goals**
- A daily `monitor` job (AWS, after the nightly ETL) scores every labelled live departure of day D-1
  with the current champion and records performance, data drift and prediction drift.
- History of daily results, served by the API and drawn on the Model Health page (≥ 7 days).
- CloudWatch metrics + alarms for: monitor missing, drift, performance.
- A retrain flag after 3 bad days in a row (drift or performance), and one retrain loop that works end
  to end: flag → retrain on HF + live data → gate → release or rejection → visible on the health page.
- `docs/runbook.md`: every alarm → meaning → first checks → fix.

**Non-goals**
- Automatic deploys from GitHub (route A, ADR 0002). Retraining stays on the laptop (training plane).
- New model features, new label, new model family.
- Per-request prediction logging analysis (user traffic is not representative; we score all trains).
- Fixing the HF undercount in history (we measure it and prefer live data where both exist).

**Anti-goals**
- Cost above the $1/month budget: more than 10 CloudWatch alarms or 10 custom metrics, new always-on
  resources, a bigger API cold start for public users.
- A health page that shows numbers we did not measure, or looks healthy when the monitor did not run.
- A retrain that silently replaces the champion without passing the existing gate.
- Monitoring on days with ingestion gaps that look like model failures (gap rows must be excluded the
  same way training excludes them).

## 3. Constraints and facts

| Fact | Consequence |
|---|---|
| Lambda memory capped at 512 MB, concurrency 5 (account) | Monitor must stay well below 512 MB. |
| Jobs image has no LightGBM; the API image has the slim scoring path (no sklearn) | Monitor runs on the **API image** with its own handler (like the sample-board Lambda in Phase 6). |
| API cold start already 6.8 s at the origin (Phase 6) | No heavy new runtime dependency in the API image. |
| Live silver: 11–14 k rows/day since 2026-11-14; quality report has gap fields | ~12 k labelled rows per day → stable daily Brier/AUC. |
| `reference_sample.parquet` (50 k train rows: features + label + `p_late`) is in every model bundle | Drift reference = the champion's own training sample. |
| `make web-deploy` runs `aws s3 sync --delete` on the web bucket | Health JSON must **not** live in the web bucket → served by the API from the data lake. |
| CloudWatch free tier: 10 alarms, 10 custom metrics. Today: 6 alarms (live mode), 2 metrics | Phase 9 may add ≤ 3 alarms and ≤ 5 metrics. |
| Training reads only `silver/departures/source=hf/` (`split.py`) | Retraining on live data needs the reader to include `source=live`. |
| HF monthly parquet in MinIO ends 2026-08 | Sep–Oct 2026 must be backfilled (local, free) so the training window has no hole. |
| Airflow compose has no triggerer service | `drift_watch` uses a sensor in `reschedule` mode, not a deferrable one **(Claude's call)**. |
| Laptop sleeps; AWS creds are a 12 h `aws login` session | Airflow never talks to AWS; laptop `make` targets move data between S3 and MinIO. |

## 4. Design

### 4.1 Daily monitor (AWS)

New Lambda `puenktlich-monitor` = API image, `command = ["monitor_handler.handler"]`, 512 MB,
timeout 300 s, async retries 1. EventBridge Scheduler: **04:30 Europe/Berlin** (ETL runs at 03:30 and
takes ~4 min). Manual invocations may pass `{"day": "YYYY-MM-DD"}` (backfill, idempotent) and
`{"threshold_overrides": {...}}` (for the forced-loop demo; validated, logged).

Steps for day D (default: yesterday, UTC — same day the ETL just wrote):
1. Load the champion via the SSM pointer and `load_bundle` (checksums), plus `reference_sample.parquet`.
   Today `load_bundle` verifies that file and drops it (`registry/artifacts.py`); the registry gets a
   small, checksum-verified way to read it (the API keeps not loading it).
2. Read `silver/departures/source=live/date=D/part-0.parquet` and `silver/_quality/source=live/date=D.json`.
   No silver → status `no_data` (recorded, metric `LabeledEvents = 0`), no exception.
3. Drop cancelled rows (`is_late` null) and the rows the quality report flags as gaps — the **same
   rule** training uses (`low_volume_hours`, flagged station-days). Reuse that code, don't copy it.
4. Score with `bundle.predict` (same path as the API).
5. **Performance**: Brier, ROC-AUC, log loss, ECE (10 bins), actual late rate, mean `p_late`,
   labelled rows. Metrics in numpy (the API image has no sklearn); unit tests compare them with
   sklearn (dev dependency) on random data.
6. **Data drift** per model input column + **prediction drift** on `p_late`, against the reference
   sample filtered to the same day type (weekday vs weekend/holiday, architecture §9).
7. Write results, emit metrics, evaluate triggers (4.3, 4.4).

Drift method **(Claude's call, deviates from rules.md "Evidently"; rules.md row + ADR 0003 updated,
owner can veto)**: use Evidently's own default tests for large samples, computed with **scipy**, which
is already in the API image (`services/api/requirements.txt`, via lightgbm) — no new dependency:
**Wasserstein distance normed by the reference std** (`scipy.stats.wasserstein_distance`) for numeric
columns and **Jensen–Shannon distance** (`scipy.spatial.distance.jensenshannon`, base 2) for
categorical ones; a column is drifted when its score ≥ **0.1**. Reason: Evidently would add its
dependency tree (statsmodels, scikit-learn, plotly, nltk, a web server, …) to the API image that the
public API also runs on — measured in §8. Columns are compared after `build_features` (rare
categories already folded into `OTHER`), so drift is measured on what the model actually sees.

Which columns count **(Claude's call, from the builder check)**: one day of data against a multi-month
reference makes the date-derived columns drift by construction (`weekday` is a single value, `month`
11 never occurs in v1's reference, holidays). So:
- `DriftShare` = drifted / 7 **content columns**: `eva`, `train_type`, `line_key`, `destination_key`,
  `stop_index`, `hour_local`, `minute_of_day`.
- Calendar columns (`weekday`, `is_weekend`, `month`, `is_public_holiday`) are reported for
  information only, never counted.
- **Prediction drift** (`p_late`) is reported separately, not in the share.
- Reference = reference sample rows of the same day type (weekday vs weekend/holiday), as in
  architecture §9.

### 4.2 Results storage and API

Data lake (`DATA_BUCKET`), written by the monitor:
- `monitoring/daily/date=D.json` — full result for one day (per-column drift scores, metrics, model
  version, champion test Brier, status, trigger state). Idempotent: a re-run replaces it.
- `monitoring/history.json` — compact list (one entry per day, newest last, max 366), rebuilt by
  read-modify-write; a re-run for day D replaces D's entry.
- `monitoring/retrains.json` — retrain decisions, written by the laptop (4.5).

API (additive contract change, `architecture.md` §7 + `prd.md` §6.3 updated):
`GET /api/v1/monitoring` → `{latest: Daily | null, history: [HistoryEntry], retrains: [Retrain]}`.
Missing files → empty lists / `null` (200, not 404). 403-for-missing handled like Phase 7.
`Cache-Control: public, max-age=300`. API role gains `s3:GetObject` on `monitoring/*` only.

### 4.3 Metrics and alarms (EMF, namespace `Puenktlich`, dimension `service=monitor`)

Metrics (5 new → 7 total): `LabeledEvents`, `BrierDaily`, `AUCDaily`, `DriftShare`,
`BrierRatio` (= BrierDaily / champion test Brier; lets one alarm survive champion changes).
Not emitted on `no_data` days except `LabeledEvents = 0`.

Alarms (3 new → 9 total, all to the existing SNS topic):

| Alarm | Rule |
|---|---|
| Monitor missing | no `LabeledEvents` datapoint for ~26 h (missing = breaching) — exact period/evaluation settings are the builder's call within CloudWatch limits |
| Drift | `DriftShare` > 0.30 on 3 consecutive daily datapoints |
| Performance | `BrierRatio` > 1.10 on 3 consecutive daily datapoints |

### 4.4 Retrain triggers

`configs/monitoring.yaml` (copied into the API image): `drift_share_max: 0.30`,
`brier_ratio_max: 1.10`, `consecutive_days: 3`, `min_labeled_rows: 2000` (days below are `too_few_rows`
and don't count either way), `drift_threshold: 0.1`, `ece_bins: 10`.
Each history entry stores its own verdict (`drift_bad`, `performance_bad`) **and the thresholds used**,
always from `configs/monitoring.yaml`. After writing day D the monitor reads `history.json`: if the last
3 **consecutive calendar days** are bad for the same reason (drift or performance), and
`control/retrain_requested.json` does not exist, it writes that file:
`{reason, days, metrics, model_version, requested_at, forced}`. A `no_data` or `too_few_rows` day, or a
day scored by a different model version, breaks the streak.

**Triggers are armed from the first run, backfill included (Claude's call).** If the live Brier really is
> 1.10 × the test Brier on 3 days in a row, that is a real performance event (prd §10 success metrics: "at least one real, documented drift event";
phases.md also allows a forced one) and the loop runs on it.

`threshold_overrides` (manual invocation only) change **only** that invocation's trigger decision: the
stored entries keep the config verdicts, and the flag is written with `forced: true` and the override
values. The health page labels forced retrains "forced demo". The forced demo is used only if no real
flag occurs before the phase closes.

### 4.5 Retrain loop (laptop, route A)

```
AWS monitor ── control/retrain_requested.json (S3)
   laptop: make retrain-sync      S3 → MinIO: live silver + live quality (missing days only), the flag;
                                   S3 flag → control/archive/ (so the monitor can raise the next one)
   Airflow: drift_watch DAG        sensor (reschedule, 30 min) on MinIO control/retrain_requested.json
                                   → TriggerDagRunOperator(training_pipeline, wait) → decision record
                                   control/decisions/<ts>.json → flag → control/archive/
   laptop: make retrain-publish    new decisions → S3 monitoring/retrains.json;
                                   if released: same code as `make publish-model` (bundle + SSM pointer)
```

- **Training on live data**: the silver reader reads `source=hf` **and** `source=live`. For a day that
  has both, **live wins** (Phase 7: live is more complete) **(Claude's call)**. Gap flags come from
  each source's own quality report. The snapshot records how many rows came from each source.
- The training loader today reads only monthly HF quality reports under `source=hf/`
  (`split.py` `_load_reports`, `latest_silver_day`); it learns to read daily reports (live, and the
  HF-raw days below) too. `build_snapshot` still refuses a window with a missing day.
- Fill the window **(Claude's call)**: HF 2026-09 and 2026-10 via the existing `backfill_history` DAG
  (started 2026-11-18). Days from 2026-11-01 to the day before the first live silver day are rebuilt
  from **HF raw** with the Phase 7 parity code (`live/parity.py`, 100 % parity with HF silver), written
  as `source=hf` day files with a daily gap report (low-volume hours / station-days, same rules as the
  monthly HF report). `make retrain-sync` fills such days automatically; HF monthly files replace them
  later only through an explicit backfill.
- The gate is unchanged (champion re-evaluated on the same new test set). A rejected challenger is a
  normal outcome and is published to the health page too.
- Releasing a gate-approved champion to prod (`make retrain-publish`) is the gate's decision, not a
  manual promotion **(Claude's call; owner can veto — then publishing stays a separate approval)**.
- Forced demo for the exit criterion: invoke the monitor with lowered `threshold_overrides` for 3
  consecutive backfilled days → flag → sync → DAG → gate → publish decision.

### 4.6 Model Health page

Data: `GET /api/v1/monitoring` (TanStack Query) + existing `/api/v1/model`. Charts with **Recharts**
(in the rules.md stack; new npm dependency, lazy-loaded on the health route only). Sections:
1. Status line: last checked day, model version, overall state (`OK` / `Watch` / `Retrain requested` /
   `Monitor late` when the latest day is older than 2 days).
2. Daily charts (≥ 7 days): Brier vs champion test Brier and the 1.10 limit; ROC-AUC; drift share vs
   0.30; **actual late rate vs mean predicted** (makes the HF undercount visible).
3. Latest drift table: column, score, method, drifted yes/no.
4. Retrain log: date, reason, decision, new version or "kept vN".
5. Existing champion cards stay. Empty state when there is no monitoring yet.
Accessible: every chart has a text summary / table alternative; colours from `design.md` tokens.

## 5. Testing

- Unit (TDD): metrics vs sklearn; drift vs scipy; gap filtering shared with training; triggers
  (streaks, breaks, existing flag, overrides); history read-modify-write (idempotent re-run); handler
  with moto (no silver, normal day, flag written); API route (empty, full, 403-as-missing); silver
  reader live-wins rule; DAG import test; frontend components + page states.
- Real runs: monitor on the emulator against MinIO live days; then on AWS for every live day since
  2026-11-14 (backfill), memory and duration measured; `make smoke-prod-live` + new monitoring check;
  forced loop end to end; health page checked in headless Chrome (0 CSP violations).
- `make check`, `make web-check`, `make test-dags`, `make tf-scan`, `tf-plan` no changes after apply.

## 6. Cost (expected)

| Item | Expected |
|---|---|
| Monitor Lambda, 1 run/day, ~TBD s × 512 MB | inside Lambda free tier |
| S3: a few KB/day of JSON | ~$0 |
| CloudWatch: 9 alarms, 7 custom metrics | inside free tier (10/10) |
| ECR: API image grows only by our code (no new runtime dependency) | ~$0 change |
| Recharts in the website bundle (~100 KB gz, lazy) | ~$0 (CloudFront free tier) |

No new always-on resource. If the measured numbers differ, the owner is asked before apply.

## 7. Docs touched

`docs/rules.md` §2 drift row (Evidently → Evidently's default tests via scipy; reason) and
`docs/adr/0003-drift-without-evidently.md`; `architecture.md` §5 (live + HF-raw days in the training
window), §7 (`/monitoring`), §9 (monitor on the API image, content-column drift share, BrierRatio,
alarm list); `prd.md` §6.3; `docs/runbook.md` (new); `phases.md`; README; CHANGELOG;
`Phases/phase-9-monitoring.md`.

## 8. Builder's call

File and function names; exact alarm period settings; JSON field names beyond those listed; chart
details within `design.md`; how the DAG archives the flag; plan task order.

## 9. Measurements behind the calls

- Evidently spike (2026-11-18, python:3.12-slim, `evidently` latest + pandas < 3 + lightgbm): TBD.
