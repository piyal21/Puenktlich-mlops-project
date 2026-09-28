# Phase 1 — Data discovery & contracts

**Goal:** understand the real Deutsche Bahn data well enough to freeze the decisions everything else depends
on: what "late" means, which stations we support, and the exact shape of the cleaned data ("silver").

**Status:** ✅ merged into `main` (merge commit `867636a`, 2026-09-25). Decisions confirmed by the owner
2026-09-26 (ADR 0001 accepted). Branch `phase-1/data-discovery` is kept.

---

## 1. What was built

| Deliverable | File(s) | What it is |
|---|---|---|
| Exploratory analysis | [`notebooks/01_eda.ipynb`](../notebooks/01_eda.ipynb) | 3 months of history analysed with DuckDB; 10 findings summarised at the top; 4 charts. |
| Station list | [`configs/stations.yaml`](../configs/stations.yaml) | The 30 supported stations (EVA id, name, federal state), one per city, all 16 states. |
| Data contract | [`src/dbdelay/data/schemas.py`](../src/dbdelay/data/schemas.py) | `SilverDepartures` (Pandera): the exact columns, types and rules every cleaned row must satisfy. |
| Decision record | [`docs/adr/0001-label-and-leakage-policy.md`](../docs/adr/0001-label-and-leakage-policy.md) | Why the label is "≥ 6 min late", why cancellations are excluded, what the model may and may not see. |
| Live API samples | [`tests/fixtures/`](../tests/fixtures/) | Real `plan` and `fchg` XML from the DB Timetables API (Kiel Hbf) for future parser tests. |
| Fetch helper | [`scripts/fetch_api_samples.py`](../scripts/fetch_api_samples.py) | Re-captures API samples; loads keys safely from settings, prints only status codes. |
| Tests | `tests/unit/test_schemas.py`, `tests/unit/test_stations_config.py` | 20 "broken data" cases each proven to fail the intended check; station-list guards. |
| Doc updates | `docs/architecture.md` §3.3/§14/§18, `docs/phases.md` | The contract details learned from the data. |

## 2. The data

- **History:** Hugging Face dataset `piebro/deutsche-bahn-data`, one parquet file per month.
  Sample months analysed: **2025-10, 2026-03, 2026-08** — chosen to cover a change in the dataset's scope,
  both daylight-saving switches and the latest month (~1.3 GB, kept out of git).
- **Live:** DB Timetables API — `plan` (the timetable for one station and hour) and `fchg` (all known changes).

## 3. What the analysis found (the 10 findings, short)

1. **Scope changed:** from 2025-11 the dataset covers ~5,300 stations instead of 131. Our 30 stations have data
   on every day of all three months.
2. **Base rate:** 22.5–26.3 % of (non-cancelled) departures at our stations are ≥ 6 min late; 3.6–4.2 % are cancelled.
3. **Delay semantics:** delay = changed − planned departure. When DB never reported a change, the dataset stores
   the planned time (delay 0) — so "no update" means "on time". The live pipeline must do the same.
4. **Keys:** the dataset's `id` has exactly the live API's `s@id` format
   (`<trip hash>-<trip start YYMMDDHHmm>-<stop number>`). The dataset's own "ride id" column is only the hash and
   repeats on other days, so it cannot identify a ride. Station ids carry a leading zero in the dataset.
5. **Time zones:** timestamps are German local time without offset. The repeated autumn hour exists and cannot be
   disambiguated reliably.
6. **Gaps:** some collection outages (e.g. 2026-03-16 evening) and month files that overlap at their edges.
7. **Station ids change:** DB merged Berlin Hbf's upper-level id (8011160) into 8098160 during 2026.
8. **Signal exists:** ICE trains are late ~46 % of the time vs S-Bahn ~16 %; stations range 3 %–43 %; hour and
   weekday matter too — timetable-only features are worth modelling.
9. **Risk levels:** Low < 20 % / Medium 20–45 % / High ≥ 45 % split departures ~47 / 34 / 18 %.
10. **Live = history format:** live ids match the dataset; `plan` and `fchg` join 19/19 on them.

## 4. Decisions frozen in this phase

**Label (ADR 0001)**
- Late = departure **≥ 6 minutes** after plan (DB's official threshold).
- No change reported ⇒ on time (delay 0).
- Cancelled ⇒ no delay, no label; kept for display and monitoring only.
- Early departures are not late; arrival-only rows are dropped.

**Leakage policy** — the model may only use what the timetable says in advance (station, train type, line,
destination, stop number, planned time, calendar, holidays). Never the actual delay, cancellation or change messages.

**Silver contract** (enforced automatically, no silent type conversion)
- Station id in API form (7 digits); ride id = trip id without stop number; row id = SHA-1 of
  `eva|ride_id|planned time (minute, UTC)`; all times in UTC.
- Rows in the ambiguous/non-existent daylight-saving hour are dropped and counted, not guessed.
- Row rules: cancelled ⇒ empty delay and label; otherwise delay = changed − planned, and late = delay ≥ 6.
- Data is partitioned by UTC day; duplicates across overlapping month files are removed.

**Stations** — 30 stations, one current non-S-Bahn id each; Berlin's retired id is mapped via `hf_aliases`.

## 5. How it was tested

- `make check` → ruff + format clean, mypy `--strict` clean, **62 unit tests pass, 100 % coverage**; all pre-commit hooks pass.
- Each of the 20 broken-data test cases asserts the **specific** check it must trigger, and a test proves error
  messages never contain row values (no data leaks into logs).
- All 30 station ids returned live data from the API on 2026-09-25.
- Every number in the notebook summary was re-checked against the notebook's own outputs.
- Independent code review: 6 should-fix items (e.g. `$` vs `\Z` in patterns, a missing delay-vs-times check,
  the Berlin id merge) — all fixed; re-review passed.

## 6. How to reproduce

```bash
# 1. download the three sample months (~1.3 GB, git-ignored)
uv run hf download piebro/deutsche-bahn-data --repo-type dataset --local-dir data/raw/hf \
  monthly_processed_data/data-2025-10.parquet monthly_processed_data/data-2026-03.parquet \
  monthly_processed_data/data-2026-08.parquet
# 2. run the notebook
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/01_eda.ipynb
# 3. re-capture live samples (needs DB API keys in .env)
uv run python scripts/fetch_api_samples.py 8000199
# 4. tests
make check
```

## 7. Known gaps and risks carried forward

- **"No update ⇒ on time"** can hide late trains if the live pipeline misses updates → Phase 7 must record whether
  each stop was seen in the change feed and reconcile live vs history.
- **Station ids can change again** → Phase 7/9 should alert on empty station boards.
- Risk thresholds are not yet in a config file (Phase 4 revisits them on the calibrated model).
- The dev fetch script uses Python's built-in HTTP client; the production client (httpx + retries) comes in Phase 7.

## 8. What comes next

Phase 2 turns these decisions into a re-runnable Airflow pipeline that downloads the history, cleans it into
the silver format and quarantines bad rows.
