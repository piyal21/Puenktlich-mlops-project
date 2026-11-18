# Phase 7 — Live data pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real departures on the public board every 15 min, labelled live silver every night, and proof (parity + reconciliation) that live data matches Hugging Face.

**Architecture:** New `dbdelay.timetables` (DB API client, rate limiter, defusedxml parser, bronze JSONL) and `dbdelay.live` (plan cache, board builder, ingest run, observations → silver, ETL, quality report, reconciliation). Two thin Lambda handlers in a new `jobs` image, scheduled by EventBridge Scheduler; Terraform adds SSM secrets, ECR repo, Lambdas, schedules, alarms and a `board_mode` switch.

**Tech Stack:** Python 3.12, httpx + tenacity, defusedxml, pandas/pyarrow/Pandera (existing silver contract), pydantic, Powertools (logger + EMF metrics), moto, Terraform AWS ~> 6.0, React/TS (filter), Vitest.

**Spec:** `docs/superpowers/specs/2026-11-12-phase-7-live-pipeline-design.md` (approved 2026-11-12). Read it first.

## Global Constraints

- Label `is_late = delay_min >= 6`, cancelled → null; silver schema (architecture §3.3) **unchanged**; `event_id` via `dbdelay.data.silver.make_event_id`; validation via `dbdelay.data.schemas.validate_silver`.
- XML only via `defusedxml.ElementTree`. httpx timeout 10 s. tenacity: retry only 429/5xx/connection errors, max 3 attempts, exponential backoff + jitter. Never retry other 4xx.
- DB API: ≤ **50 calls/min** client-side (every attempt counts); headers `DB-Client-Id`, `DB-Api-Key`, `Accept: application/xml`; base URL `https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1`.
- API keys only in `.env` (local) and SSM SecureString (prod). Never logged, printed, committed or put in Terraform state.
- Times: UTC everywhere; DB `YYMMDDHHmm` is naive Europe/Berlin → ambiguous/nonexistent → dropped and counted (same rule as HF).
- Plan cache: Berlin hours **H−3 … H+6**; ≤ **70** plan calls per run; fetch order H, H+1 … H+6, then H−1 … H−3.
- Board: rows with `leaves_at ≥ now − 30 min` and `planned ≤ now + 6 h`; `source="live"`, `replayed_from=None`.
- Gap rule: a departure is covered when its station has a successful `fchg` call in `[planned, planned + 3 h]`.
- Reconciliation threshold: ≥ **95 %** of HF departures found by `event_id`; ≥ **95 %** `is_late` agreement on matched non-cancelled rows. Parity target (HF raw through our code vs HF silver): ≥ **99 %** both.
- Lambdas 512 MB; ingest timeout 300 s, ETL 600 s; schedules Europe/Berlin: ingest `rate(15 minutes)` retry 0, ETL `cron(30 3 * * ? *)` retry 1.
- `mypy --strict` on `src/dbdelay/`, ruff rules as in `pyproject.toml`, line length 100. Thin handlers; logic in `src/dbdelay/`.
- Every AWS write and every `terraform apply` needs the owner's explicit OK at that moment. No account ids in committed files.
- Commits: Conventional Commits on `phase-7/live-pipeline`; **no `Co-Authored-By` trailer** (owner rule in CLAUDE.md overrides any default). Never delete branches. Run `uv run ruff check --fix` + `uv run ruff format` before `git add` (pre-commit aborts silently otherwise).
- Numbers in docs only when measured; otherwise `TBD`.

## Review Focus

1. **DST days** (2027-03-28 spring forward, 2026-10-25 fall back): plan hours are not fetched twice or skipped wrongly, and ambiguous times are dropped and counted, not crashed on → tests in Task 2 and Task 6.
2. **One station's `fchg` fails in a run:** its rows come from the previous board, the run succeeds, the failure is counted → test in Task 7.
3. **Cancelled, then reinstated:** an earlier `fchg` says `cs="c"`, the latest one has no `cs` → not cancelled (latest observation wins) → test in Task 4.
4. **Garbage bodies:** HTTP 200 with an HTML error page, truncated XML, or an entity-expansion bomb → `DataValidationError`, counted per station, never a crash of the whole run → tests in Task 2 and Task 8.
5. **UTC day edge:** a departure at 23:50 UTC (00:50/01:50 Berlin next day) lands in the earlier UTC partition, with plan data fetched on the previous run date → test in Task 9.

---

### Task 0: Commit the spec, plan and session notes

- [ ] **Step 1:** `git add CLAUDE.md docs/superpowers/specs/2026-11-12-phase-7-live-pipeline-design.md docs/superpowers/plans/2026-11-12-phase-7-live-pipeline.md`
- [ ] **Step 2:** `git commit -m "docs(phase-7): design spec, implementation plan and session notes"`

### Task 1: Dependencies and settings

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `src/dbdelay/config.py`, `.env.example`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: extra `jobs = ["httpx>=0.28.1", "tenacity>=9", "defusedxml>=0.7.1"]`; dev group adds `tenacity`, `defusedxml`, `types-defusedxml`, `boto3-stubs[s3,ssm]`. `Settings` gains `db_api_base_url: str` (default above), `db_api_client_id_param: str | None = None`, `db_api_key_param: str | None = None`, `plan_call_budget: int = Field(default=70, ge=0)`, `api_calls_per_min: int = Field(default=50, gt=0, le=60)`.

- [ ] **Step 1:** Write failing tests `test_live_settings_defaults` (asserts the four defaults above) and `test_api_calls_per_min_rejects_above_60` (`API_CALLS_PER_MIN=61` → `ValidationError`).
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_config.py -v` → both FAIL.
- [ ] **Step 3:** Add the extra and dev deps (`uv add --optional jobs …`, `uv add --dev …`), then the settings fields. `.env.example` gets commented placeholders for the two `*_PARAM` names (no values).
- [ ] **Step 4:** Run `uv run pytest tests/unit/test_config.py -v` → PASS; `make typecheck` clean.
- [ ] **Step 5:** `git commit -m "build(deps): jobs extra (httpx, tenacity, defusedxml) and live ingestion settings"`

### Task 2: Timetable parser

**Files:**
- Create: `src/dbdelay/timetables/__init__.py`, `src/dbdelay/timetables/parser.py`
- Test: `tests/unit/test_timetables_parser.py` (fixtures `tests/fixtures/sample_plan.xml`, `sample_fchg.xml`)

**Interfaces:**
- Produces:
  - `parse_db_time(value: str) -> datetime | None` — `YYMMDDHHmm` Berlin → aware UTC; `None` if ambiguous or nonexistent (detect with `fold=0` vs `fold=1` offsets and a UTC round trip).
  - `class StopEvent(BaseModel)` (frozen, `extra="forbid"`, JSON-serialisable): `stop_id: str`, `ride_id: str`, `stop_index: int`, `eva: str`, `train_type: str | None`, `train_number: str | None`, `line_number: str | None`, `planned_utc: datetime | None`, `changed_utc: datetime | None`, `cancelled: bool`, `planned_platform: str | None`, `changed_platform: str | None`, `planned_path: tuple[str, ...]`, `changed_path: tuple[str, ...] | None`, `distant_endpoint: str | None`.
  - `@dataclass(frozen=True) class ParseResult: events: tuple[StopEvent, ...]; drops: dict[str, int]` with drop keys `"no_departure"`, `"bad_id"`, `"dst_ambiguous_or_nonexistent"`.
  - `parse_timetable(xml: bytes, eva: str) -> ParseResult` — raises `DataValidationError` on malformed XML, entity bombs (`defusedxml` exceptions) or a root that is not `<timetable>`.

Mapping: `s@id` must match `-?[0-9]+-[0-9]{10}-[1-9][0-9]*` (else `bad_id`); `ride_id` = id minus `-<n>` suffix, `stop_index` = suffix; `tl@c` upper-cased/stripped, `tl@n`; from `dp`: `pt`, `ct`, `pp`, `cp`, `l`, `ppth`/`cpth` split on `|` (empty → `()`), `pde`; `cs == "c"` → cancelled. `<s>` without `<dp>` → `no_departure`. Unparseable `ct` with valid `pt` → `changed_utc=None` and counted under `dst_ambiguous_or_nonexistent`.

- [ ] **Step 1: Write the failing tests**
  - `test_plan_fixture_parses_departures`: Kiel plan → the stop `-7979240743321752940-2609251743-1` has `ride_id="-7979240743321752940-2609251743"`, `stop_index=1`, `train_type="ERX"`, `train_number="21031"`, `line_number="RE83"`, `planned_utc=2026-09-25T15:43Z`, `planned_path[-1]=="Lübeck Hbf"`, `distant_endpoint=="Lüneburg"`; arrival-only stops counted under `no_departure`.
  - `test_fchg_fixture_has_five_cancellations`: `sum(e.cancelled for e in events) == 5` (fixtures README).
  - `test_parse_db_time_dst`: `"2610250230"` → `None` (ambiguous, 2026-10-25); `"2703280230"` → `None` (nonexistent); `"2609251705"` → `2026-09-25T15:05Z`.
  - `test_malformed_xml_raises`, `test_html_error_page_raises`, `test_entity_bomb_raises` (billion-laughs snippet) → `DataValidationError`.
  - `test_bad_id_counted`: `<s id="x">` → `drops["bad_id"] == 1`.
- [ ] **Step 2:** `uv run pytest tests/unit/test_timetables_parser.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement `parser.py` as specified.
- [ ] **Step 4:** Same command → PASS; `make typecheck`.
- [ ] **Step 5:** `git commit -m "feat(timetables): defusedxml parser for plan and fchg"`

### Task 3: Rate limiter, API client, bronze records

**Files:**
- Create: `src/dbdelay/timetables/ratelimit.py`, `client.py`, `bronze.py`
- Test: `tests/unit/test_timetables_client.py`, `tests/unit/test_timetables_bronze.py`

**Interfaces:**
- Produces:
  - `RateLimiter(calls_per_min: int = 50, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep)` with `acquire() -> None` (sliding 60 s window).
  - `@dataclass(frozen=True) class ApiResponse: status: int; body: bytes`.
  - `TimetablesClient(client_id: str, api_key: str, *, limiter: RateLimiter, base_url: str, timeout_s: float = 10.0, transport: httpx.BaseTransport | None = None, wait: tenacity wait strategy = wait_exponential_jitter(initial=1, max=10))`; `plan(eva: str, hour_local: datetime) -> ApiResponse` (path `/plan/{eva}/{YYMMDD}/{HH}` of the Berlin hour), `fchg(eva: str) -> ApiResponse`. Returns the last response after retries are exhausted; raises `ExternalServiceError` when every attempt fails to connect. `__repr__` never shows the key.
  - `@dataclass(frozen=True) class BronzeRecord: kind: Literal["plan", "fchg"]; eva: str; hour: str | None` (`"YYMMDDHH"` Berlin, plan only)`; requested_at: datetime; status: int | None; body: str | None; error: str | None`.
  - `bronze_key(kind: str, run_at: datetime) -> str` → `bronze/timetables/kind={kind}/date={run_at:%Y-%m-%d}/run={run_at:%Y-%m-%dT%H%M%SZ}.jsonl.gz`; `records_to_bytes(records: Iterable[BronzeRecord]) -> bytes` (gzip, `mtime=0`); `iter_records(data: bytes) -> Iterator[BronzeRecord]`.

- [ ] **Step 1: Write the failing tests** (client via `httpx.MockTransport`, limiter with fake clock/sleep, `wait=wait_none()`):
  - `test_limiter_sleeps_after_50_calls_in_a_minute`: 51st `acquire()` sleeps `≈ 60 − elapsed`.
  - `test_headers_and_plan_path`: request has the three headers and path `/plan/8000199/260925/17`.
  - `test_retries_429_then_succeeds` (3 attempts max, each attempt calls `limiter.acquire`), `test_no_retry_on_404`, `test_gives_up_after_three_5xx` (returns status 503), `test_connect_error_three_times_raises`.
  - `test_repr_hides_key`.
  - `test_bronze_roundtrip` and `test_bronze_key_format` (`2026-11-12T08:15:00Z` → `.../date=2026-11-12/run=2026-11-12T081500Z.jsonl.gz`).
- [ ] **Step 2:** Run both test files → FAIL.
- [ ] **Step 3:** Implement the three modules.
- [ ] **Step 4:** Run both test files → PASS; `make typecheck`.
- [ ] **Step 5:** `git commit -m "feat(timetables): rate limiter, DB API client with retries, bronze records"`

### Task 4: Observations → silver rows (shared by ETL, parity and reconciliation)

**Files:**
- Create: `src/dbdelay/live/__init__.py`, `src/dbdelay/live/conform.py`
- Test: `tests/unit/test_live_conform.py`

**Interfaces:**
- Consumes: `StopEvent` (Task 2), `make_event_id`, `validate_silver`, `SILVER_COLUMNS`, `Station`.
- Produces:
  - `@dataclass class MergedStop: plan: StopEvent; plan_seen_at: datetime; change: StopEvent | None; change_seen_at: datetime | None`.
  - `class StopMerger` with `add_plan(event: StopEvent, seen_at: datetime) -> None`, `add_change(event: StopEvent, seen_at: datetime) -> None` (latest `seen_at` wins per `stop_id`; changes for unknown stops kept in `fchg_only: dict[str, StopEvent]`), `stops() -> dict[str, MergedStop]`.
  - `final_destination(stop: MergedStop) -> str | None` — last of `change.changed_path` if set, else last of `plan.planned_path`; `None` when empty. (Parity check, Task 5, may change this rule.)
  - `via(stop: MergedStop) -> tuple[str, ...]` — same path choice, all entries.
  - `to_silver(stops: Mapping[str, MergedStop], stations: Sequence[Station], day: date) -> tuple[pd.DataFrame, dict[str, int]]` — rows whose `planned_utc` falls on UTC `day`; `changed = change.changed_utc or planned`; `cancelled = change.cancelled if change else plan.cancelled`; `delay_min` null when cancelled; `source="live"`; `ingested_at = max(plan_seen_at, change_seen_at)`; `station_name` from stations; drops `"no_planned_time"`, `"not_supported_station"`, `"duplicate"`; result passes `validate_silver`, sorted by `event_id`.

- [ ] **Step 1: Write the failing tests:** `test_no_change_means_on_time` (delay 0, `is_late False`), `test_delay_7_is_late`, `test_latest_change_wins_reinstated_train` (Review Focus 3), `test_cancelled_has_null_label`, `test_changed_path_sets_destination`, `test_out_of_day_rows_excluded`, `test_output_passes_silver_contract`.
- [ ] **Step 2:** `uv run pytest tests/unit/test_live_conform.py -v` → FAIL.
- [ ] **Step 3:** Implement `conform.py`.
- [ ] **Step 4:** Same command → PASS; `make typecheck`.
- [ ] **Step 5:** `git commit -m "feat(live): merge plan and change observations into silver rows"`

### Task 5: Parity check on an August HF raw day (local, no AWS)

**Files:**
- Create: `src/dbdelay/live/hf_raw.py`, `src/dbdelay/live/reconcile.py`, `scripts/parity_check.py`
- Modify: `Makefile` (target `parity-check DAY=2026-08-24`), `.gitignore` (`data/raw/hf_raw/`, `data/reconciliation/`)
- Test: `tests/unit/test_live_reconcile.py`, `tests/unit/test_hf_raw.py`

**Interfaces:**
- Consumes: `parse_timetable`, `StopMerger`, `to_silver`, `hf_download` (`dbdelay.data.hf_backfill`), local HF silver via `silver_key`.
- Produces:
  - `hf_raw_paths(day: date) -> list[str]` — files under `raw_data/year={y}/month={m}/day={d}` (unpadded month/day) **plus** the next day's folder (late-night `fchg`), via `huggingface_hub.HfApi().list_repo_tree(..., repo_type="dataset")`.
  - `iter_hf_raw(path: Path, evas: set[str]) -> Iterator[tuple[Literal["plan", "fchg"], str, datetime, bytes]]` (kind, eva, requested_at UTC, XML). Column names come from Step 1 and are written in the module docstring.
  - `@dataclass(frozen=True) class Comparison: day: date; left_rows: int; right_rows: int; matched: int; match_rate_left: float; match_rate_right: float; is_late_agreement: float | None; delay_exact: float | None; cancelled_agreement: float | None; destination_agreement: float | None; per_station: dict[str, dict[str, float | int]]` with `to_json() -> str`.
  - `compare_silver(left: pd.DataFrame, right: pd.DataFrame, day: date) -> Comparison` (join on `event_id`; agreement rates over matched rows; `is_late` over matched non-cancelled rows).

- [ ] **Step 1: Inspect the HF raw schema.** Download one file of `raw_data/year=2026/month=8/day=24` to `data/raw/hf_raw/` and print `pyarrow.parquet.read_schema` plus the distinct values of the endpoint/kind column, via a scratch script run with `uv run python <file>` (never `python -`). Record the columns, how plan vs `fchg` is told apart, and how often `fchg` is polled per station.
- [ ] **Step 2: Write the failing tests:** `test_compare_identical_frames` (rates 1.0), `test_compare_counts_missing_both_ways`, `test_is_late_agreement_ignores_cancelled`, `test_iter_hf_raw_filters_evas` (tiny parquet written in the test with the Step 1 columns), `test_hf_raw_paths_adds_next_day` (fake `HfApi`).
- [ ] **Step 3:** Run them → FAIL. Implement `hf_raw.py` and `reconcile.py`; `scripts/parity_check.py` (thin) downloads the day's files, merges observations in `requested_at` order, builds silver for the day, reads local HF silver for the same day from MinIO, and writes `data/reconciliation/parity-<day>.json` with a printed summary.
- [ ] **Step 4:** Run tests → PASS. Then `make up` and `make parity-check DAY=2026-08-24`.
- [ ] **Step 5: Reach parity.** Target ≥ 99 % `match_rate` both ways and ≥ 99 % `delay_exact`/`destination_agreement`. For each gap, read mismatched rows, adjust the rule in `conform.py`/`parser.py` **test-first** (e.g. `pde` vs. last `ppth`, `fchg`-only added trains kept or not, missing `ct`). Re-run until the target is met or the remaining gap is explained (write the explanation down for the phase doc). Record the final numbers.
- [ ] **Step 6:** `git commit -m "feat(live): HF raw parity check and silver comparison"` (plus a separate `fix(live): …` commit per rule change).

### Task 6: Plan cache

**Files:**
- Create: `src/dbdelay/live/plans.py`
- Test: `tests/unit/test_live_plans.py`

**Interfaces:**
- Consumes: `StopEvent`.
- Produces:
  - `PLANS_KEY = "live/plans/latest.json.gz"`.
  - `class PlanCache(BaseModel)`: `schema_version: Literal[1] = 1`, `fetched: dict[str, list[str]]` (eva → `"YYMMDDHH"` Berlin hours), `stops: list[StopEvent]`, `seen_at: dict[str, datetime]` (stop_id → fetch time).
  - `wanted_hours(now: datetime) -> list[datetime]` — Berlin hour starts in fetch order (H, H+1 … H+6, H−1 … H−3), built by stepping UTC hours and converting, deduplicated by `YYMMDDHH`.
  - `missing(cache: PlanCache, evas: Sequence[str], now: datetime, budget: int) -> list[tuple[str, datetime]]` — hour-major, at most `budget`.
  - `add_hour(cache, eva: str, hour: datetime, events: Sequence[StopEvent], seen_at: datetime) -> PlanCache`; `prune(cache, now) -> PlanCache` (drops hours older than H−3 and their stops).
  - `load_cache(store) -> PlanCache` (missing/invalid → empty, logged) and `save_cache(store, cache) -> None`.

- [ ] **Step 1: Write the failing tests:** `test_wanted_hours_order`, `test_wanted_hours_fall_back_day_no_duplicate` and `test_wanted_hours_spring_forward_skips_missing_hour` (Review Focus 1), `test_missing_respects_budget_and_order` (cold start, 30 stations, budget 70 → 70 pairs, all of H and H+1 first), `test_prune_drops_old_hours`, `test_invalid_cache_reads_as_empty` (moto `s3_store`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; `make typecheck`.
- [ ] **Step 5:** `git commit -m "feat(live): plan cache for Berlin hours H-3..H+6 with a call budget"`

### Task 7: Live board (with `via`)

**Files:**
- Create: `src/dbdelay/live/board.py`
- Modify: `src/dbdelay/serving/board.py` (`BoardDeparture.via: tuple[str, ...] = ()`)
- Test: `tests/unit/test_live_board.py`, `tests/unit/test_board.py` (or the existing board test file)

**Interfaces:**
- Consumes: `PlanCache`, `StopMerger`/`MergedStop`, `final_destination`, `via`, `make_event_id`, `LiveBoard`.
- Produces: `build_live_board(cache: PlanCache, changes: Mapping[str, Sequence[StopEvent]], *, failed: Collection[str], previous: LiveBoard | None, stations: Sequence[Station], now: datetime, fetched_at: datetime) -> LiveBoard` — window and fields per Global Constraints; platform `changed_platform or planned_platform`; cancelled rows kept while `planned ≥ now − 30 min`; for an eva in `failed`, that station's rows come from `previous` (re-filtered to the window); sorted by `(planned_departure_utc, event_id)`.

- [ ] **Step 1: Write the failing tests:** `test_old_board_without_via_still_valid`, `test_delay_and_platform_from_change`, `test_delayed_train_planned_40_min_ago_still_shown`, `test_failed_station_keeps_previous_rows` (Review Focus 2), `test_via_from_planned_path`, `test_board_source_is_live`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; whole `make test` stays green (API and seed tests use `BoardDeparture`).
- [ ] **Step 5:** `git commit -m "feat(live): build the live board from plan cache and changes; optional via on board rows"`

### Task 8: Ingest run

**Files:**
- Create: `src/dbdelay/live/ingest.py`, `src/dbdelay/live/secrets.py`
- Test: `tests/unit/test_live_ingest.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 6, 7.
- Produces:
  - `@dataclass(frozen=True) class IngestResult: calls: int; fchg_ok: int; failed_stations: tuple[str, ...]; plan_hours_fetched: int; events: int; board_rows: int`.
  - `run_ingest(store: ObjectStore, client: TimetablesClient, stations: Sequence[Station], *, now: datetime, board_key: str, plan_budget: int) -> IngestResult` — order: `fchg` ×30 → plan top-up (`missing`) → write the two bronze objects (`kind=fchg`, `kind=plan`, skip an empty one) → save cache → write board → return. A failed call (exception, non-200, or parse error) is recorded in bronze with `status`/`error` and counted. Raises `ExternalServiceError` **after** writing bronze when no `fchg` call succeeded. `events` = departures parsed from successful `fchg` calls.
  - `load_db_api_credentials(settings: Settings, ssm: SSMClient | None) -> tuple[str, str]` — `.env` values when set, else `GetParameter(WithDecryption=True)` on the two `*_PARAM` names; raises `ConfigError` (message names the setting, never a value) when neither exists.

- [ ] **Step 1: Write the failing tests** (moto `s3_store`, fake client returning fixture XML per eva): `test_ingest_writes_bronze_cache_and_board`, `test_ingest_counts_html_body_as_failed` (Review Focus 4), `test_ingest_all_fchg_failed_raises_after_bronze`, `test_plan_budget_limits_calls`, `test_credentials_from_ssm` (moto SSM SecureString), `test_credentials_missing_message_has_no_value`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** `git commit -m "feat(live): 15-minute ingest run (fchg, plan top-up, bronze, board)"`

### Task 9: Nightly ETL and live quality report

**Files:**
- Create: `src/dbdelay/live/etl.py`, `src/dbdelay/live/quality.py`
- Modify: `src/dbdelay/training/split.py` (`gap_masks` takes `Sequence[GapReport]`, a `Protocol` with `low_volume_hours: list[str]` and `stations: Sequence[StationQuality]`)
- Test: `tests/unit/test_live_etl.py`, `tests/unit/test_live_quality.py`, existing split tests

**Interfaces:**
- Consumes: bronze reader, parser, `StopMerger`, `to_silver`, `silver_key(day, root)` with root `""` and the live partition path.
- Produces:
  - `live_silver_key(day: date) -> str` → `silver/departures/source=live/date=YYYY-MM-DD/part-0.parquet`; `live_quality_key(day) -> str` → `silver/_quality/source=live/date=YYYY-MM-DD.json`.
  - `class LiveQualityReport(BaseModel)`: `schema_version`, `source: Literal["live"]`, `day: date`, `rows_out`, `drops: dict[str, int]`, `late_rate`, `cancelled_rate`, `stations: list[StationQuality]` (reused; `volume_drop_days` = UTC days), `low_volume_hours: list[str]` (`"%Y-%m-%dT%H"` Berlin), `fchg_runs_expected: int = 96`, `fchg_runs_ok: dict[str, int]`, `longest_fchg_gap_min: dict[str, int]`, `plan_hours_missing: dict[str, list[str]]`, `never_seen_in_fchg: int`, `fchg_only_stops: int`, `empty_stations: list[str]`, `content_hash: str`.
  - `build_live_report(silver, merged, fchg_success: Mapping[str, Sequence[datetime]], plan_missing, stations, day) -> LiveQualityReport` — gap rule from Global Constraints: uncovered departures flag their station's UTC day; a Berlin hour where every station with departures is uncovered goes into `low_volume_hours`.
  - `run_etl(store: ObjectStore, stations: Sequence[Station], day: date) -> LiveQualityReport` — reads `kind=plan` run dates `day−1 … day+1` and `kind=fchg` run dates `day … day+1`, one object at a time (streaming), writes silver + report (overwrite = idempotent), emits nothing itself.

- [ ] **Step 1: Write the failing tests:** `test_etl_writes_valid_silver_and_report`, `test_etl_is_idempotent` (same bytes twice), `test_departure_at_2350_utc_lands_in_that_day` (Review Focus 5), `test_missing_fchg_runs_flag_station_day`, `test_all_stations_uncovered_flags_hour`, `test_empty_station_listed`, `test_gap_masks_accepts_live_report`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; `make test` green.
- [ ] **Step 5:** `git commit -m "feat(live): nightly bronze-to-silver ETL with a gap-aware quality report"`

### Task 10: Jobs image, handlers, local runs against the real API

**Files:**
- Create: `services/jobs/Dockerfile`, `services/jobs/requirements.txt` (generated), `services/jobs/ingest_handler.py`, `services/jobs/etl_handler.py`, `src/dbdelay/live/cli.py`
- Modify: `Makefile` (`jobs-requirements`, `ingest-local`, `etl-live-local DAY=…`, `jobs-image-build`), `pyproject.toml` (ruff `src` adds `services/jobs`), `.pre-commit-config.yaml` if it lists service paths
- Test: `tests/unit/test_jobs_handlers.py`

**Interfaces:**
- Consumes: `run_ingest`, `run_etl`, `load_db_api_credentials`, `load_stations`.
- Produces: `ingest_handler.handler(event, context) -> dict` (returns `IngestResult` as dict; emits EMF metric `EventsIngested` in namespace `Puenktlich`, service `ingest`); `etl_handler.handler(event, context) -> dict` (`event.get("day")` ISO date, default UTC yesterday; emits `EmptyStations` = `len(report.empty_stations)`, service `etl_daily`). `cli.py`: `ingest_main(argv) -> int`, `etl_main(argv) -> int` for the Makefile (MinIO via `.env`).
- Dockerfile mirrors `services/api/Dockerfile` (same base image digest, `--require-hashes`, `configs/stations.yaml`), no LightGBM/libgomp; requirements = `uv export --extra jobs --no-dev --no-emit-project` with hashes.

- [ ] **Step 1: Write the failing tests:** `test_ingest_handler_returns_counts_and_metric` (patch the client factory; assert EMF JSON in output has `EventsIngested`), `test_etl_handler_defaults_to_yesterday`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement handlers, CLI, Dockerfile, Makefile targets. **Step 4:** Run → PASS; `make check`.
- [ ] **Step 5: Real local run (no AWS):** `make up`, then `make ingest-local` twice 15 min apart. Expected: two bronze objects per run in MinIO, a `live/plans/latest.json.gz`, a `source="live"` board with departures for ~30 stations, ≤ 100 calls per run, 0 HTTP 429. Then `make etl-live-local DAY=<today UTC>` (partial day) → valid silver + report. Record run time, calls, and ETL peak memory (`/usr/bin/time` unavailable on Windows → measure inside the jobs image with the Lambda emulator, as in Phase 6).
- [ ] **Step 6:** `make jobs-image-build`; run both handlers in the Lambda runtime emulator against MinIO; record image size.
- [ ] **Step 7:** `git commit -m "feat(jobs): jobs image with ingest and nightly ETL handlers; local run targets"`

### Task 11: API and frontend — "Going to" any stop

**Files:**
- Modify: `services/api/app/schemas.py` (`TrainOut.via: list[str] = []`, filled from `row.via`), `frontend/src/api/types.ts` (`via?: string[]`), `frontend/src/lib/destinations.ts`, `frontend/src/components/DestinationFilter.tsx`
- Test: `tests/unit/test_api_routes.py` (or the board route test), `frontend/src/lib/destinations.test.ts`, `frontend/src/pages/BoardPage.test.tsx`

**Interfaces:**
- Produces: `placesOf(d: Departure): string[]` = trimmed non-empty `via ?? []` plus destination, deduplicated; `destinationsOf` lists every place; `goingTo` keeps departures whose places include the choice; `filterSummary` wording unchanged ("… trains go to X" stays true for "stop at X"; if the copy review prefers "stop at", change text and tests together). Label: `Going to` + hint `(any stop on the way)`.

- [ ] **Step 1: Write the failing tests:** API `test_board_train_has_via` (board row with `via=("Erlangen", "Bamberg")` → JSON `train.via`), `test_sample_board_via_empty`; Vitest `destinationsOf includes stops on the way`, `goingTo matches an intermediate stop`, `missing via falls back to destination`, BoardPage label text.
- [ ] **Step 2:** `make test` + `make web-check` → new tests FAIL.
- [ ] **Step 3:** Implement. Run `design:ux-copy` on the label/hint; `design:accessibility-review` on the filter.
- [ ] **Step 4:** `make check` and `make web-check` → PASS.
- [ ] **Step 5:** `git commit -m "feat(web): \"Going to\" matches every stop on the way (via)"`

### Task 12: Terraform

**Files:**
- Modify: `infra/modules/ssm/*` (two SecureString params, placeholder `"set-by-make"`, `lifecycle { ignore_changes = [value] }`), `infra/modules/schedules/*` (ingest + ETL schedules; sample schedule `count = var.board_mode == "sample" ? 1 : 0`; scheduler role may invoke the scheduled functions only), `infra/modules/observability/*` (alarms below; sample alarm counted like its schedule), `infra/envs/prod/main.tf`, `variables.tf` (`board_mode` with validation `"live"|"sample"`, default `"sample"`; `jobs_image_tag`), `outputs.tf`, `infra/.trivyignore` only with reasons
- Modify: `Makefile` (`put-db-api-secrets`, `jobs-image-push` mirroring `image-push`; `tf-plan` passes nothing new — tag lives in the git-ignored tfvars)
- Create: `src/dbdelay/live/secrets_cli.py` (`put_main(argv) -> int`: reads `.env` via `get_settings()`, `PutParameter(Type="SecureString", Overwrite=True)` for both, prints only parameter names)
- Test: `tests/unit/test_secrets_cli.py` (moto SSM; output never contains the values)

Wiring in `envs/prod/main.tf`:
- `module "ecr_jobs"` (`name = "puenktlich-jobs"`).
- `module "ingest"` (`lambda_image`, timeout 300, memory 512, command `["ingest_handler.handler"]`), env `APP_ENV=prod`, `DATA_BUCKET`, `BOARD_KEY = var.board_mode == "live" ? "live/boards/latest.json.gz" : "live/boards/candidate.json.gz"`, `DB_API_CLIENT_ID_PARAM`, `DB_API_KEY_PARAM`, `POWERTOOLS_METRICS_NAMESPACE=Puenktlich`, `POWERTOOLS_SERVICE_NAME=ingest`. Policy: `s3:GetObject`/`PutObject` on `bronze/timetables/*`, `live/plans/*`, `live/boards/*`; `ssm:GetParameter` on the two parameter ARNs (add `kms:Decrypt` with `kms:ViaService = ssm.eu-north-1.amazonaws.com` only if the first real run proves it is needed).
- `module "etl_daily"` (timeout 600, memory 512, command `["etl_handler.handler"]`), policy: `s3:ListBucket` with `s3:prefix` `bronze/timetables/*`, `s3:GetObject` on `bronze/timetables/*`, `s3:PutObject` on `silver/departures/source=live/*` and `silver/_quality/source=live/*`.
- Alarms (all to the existing SNS topic): `puenktlich-ingestion-stopped` (`EventsIngested`, dims `service=ingest`, Sum, period 3600, 1 of 1, `treat_missing_data = "breaching"`), `puenktlich-ingest-errors` (Lambda `Errors`, period 900, 2 of 2), `puenktlich-etl-errors` (period 86400, 1 of 1), `puenktlich-empty-stations` (`EmptyStations` > 0, dims `service=etl_daily`, period 86400).

- [ ] **Step 1:** Failing test `test_put_secrets_writes_secure_strings_without_printing` → FAIL → implement `secrets_cli.py` → PASS.
- [ ] **Step 2:** Terraform changes; `make tf-fmt`, `make tf-validate` → clean.
- [ ] **Step 3:** `make tf-scan` → exit 0 (new findings fixed or added to `.trivyignore` with a reason).
- [ ] **Step 4:** `make aws-check` then `make tf-plan` (read-only) → plan shows only the new resources; with `board_mode = "sample"` nothing existing is destroyed. Save the summary for the owner.
- [ ] **Step 5:** `git commit -m "infra(prod): DB API secrets, jobs repo, ingest and ETL Lambdas, schedules, alarms, board_mode"`

### Task 13: Production smoke test for live data

**Files:**
- Modify: `scripts/smoke_prod.py`, `Makefile` (`smoke-prod-live`)
- Test: `tests/unit/test_smoke_prod.py`

**Interfaces:** `--expect-live` flag adds two checks: board `data_source == "live"` and `now − data_as_of < 20 min`; and `train.via` is a list.

- [ ] **Step 1:** Failing tests `test_expect_live_fails_on_sample_board`, `test_expect_live_fails_on_old_data_as_of` → FAIL. **Step 2:** Implement → PASS.
- [ ] **Step 3:** `git commit -m "test(smoke): live-data checks for the production board"`

### Task 14: Rollout (each AWS write needs the owner's OK at that moment)

Give the owner the exact Claude Code allow lines first: `Bash(make put-db-api-secrets)`, `Bash(make jobs-image-push)`, `Bash(make smoke-prod-live)`. `make aws-check` before each stage; if the 12 h login expired, the owner runs `aws login --profile puenktlich`.

- [ ] **Stage A:** `make tf-plan` → show summary → OK → `make tf-apply` with only SSM params + `ecr_jobs` targeted (`-target=module.ssm -target=module.ecr_jobs`, both standalone). Then `make put-db-api-secrets`, `make jobs-image-push`; write `jobs_image_tag` into the git-ignored tfvars.
- [ ] **Stage B:** `make image-push` (API with `via`) and update `api_image_tag`; `make tf-plan` → OK → `make tf-apply` (ingest writes `candidate` board, ETL scheduled, alarms); `make web-deploy`; `make smoke-prod` (sample board still public, 10/10).
- [ ] **Check after first runs:** CloudWatch logs of 2+ ingest runs (calls, failed stations, duration, max memory), `candidate.json.gz` content, `EventsIngested` metric visible, no alarm in ALARM except the expected `etl-errors` state before the first night.
- [ ] **Stage C (after ≥ 1 good day):** `board_mode = "live"` in tfvars → `make tf-plan` (expect: ingest env change, sample schedule + alarm destroyed) → OK → `make tf-apply` → `make smoke-prod-live` → real-browser check (zenika/alpine-chrome, CSP log) that the board says "Live".
- [ ] **Nightly:** after each of 3 nights, read `silver/_quality/source=live/date=….json` (read-only) and note rows, gaps, ETL duration and memory.
- [ ] Commit any fixes found in rollout test-first, each as its own `fix(…)` commit.

### Task 15: Reconciliation against HF raw data

**Files:**
- Create: `scripts/reconcile_live.py`
- Modify: `Makefile` (`reconcile DAYS="2026-11-14 2026-11-15 2026-11-16"`)
- Test: `tests/unit/test_live_reconcile.py` (add `test_threshold_verdict`)

**Interfaces:** reuses `hf_raw_paths`, `iter_hf_raw`, `StopMerger`, `to_silver`, `compare_silver`. Adds `verdict(c: Comparison) -> bool` (≥ 0.95 `match_rate_right` where right = HF, and ≥ 0.95 `is_late_agreement`). Live silver read from AWS read-only with profile `puenktlich-py`; output `data/reconciliation/<day>.json` and (owner OK) upload to `silver/_quality/source=live/reconciliation/date=<day>.json`.

- [ ] **Step 1:** Failing `test_threshold_verdict` → implement → PASS.
- [ ] **Step 2:** Once HF raw has the 3 live days (check the HF tree; usually next day), run `make reconcile DAYS=…`. Record numbers per day.
- [ ] **Step 3:** Below threshold → per-station analysis, fix test-first, re-measure once on 3 new nights (spec §4); still below for an HF-side reason → bring the numbers to the owner.
- [ ] **Step 4:** `git commit -m "feat(live): reconciliation of live silver against HF raw data"`

### Task 16: Docs, review and branch record

**Files:** `docs/architecture.md` (§3.2 candidate key + live quality/reconciliation paths, §7 `via`, §8 single run + plan top-up + budgets, §9 alarms + metrics, §11 modules, §16 cost), `docs/phases.md` (Phase 7 ticks), `docs/prd.md` §6.3 (`via`), `README.md`, `CHANGELOG.md`, `Phases/phase-7-live-pipeline.md`, `Phases/README.md`, `CLAUDE.md` (Status + Session log), `.git/MERGE_SUMMARY.txt` (not committed).

- [ ] **Step 1:** `make check`, `make web-check`, `make test-integration`, `uv run pre-commit run --all-files`, `make tf-plan` ("No changes"), `make tf-scan` → all green; record the numbers.
- [ ] **Step 2:** Final whole-branch review with the `code-reviewer` subagent (most capable model); fix Critical/Important test-first; defer Minor to CHANGELOG. Then `security-review`.
- [ ] **Step 3:** Write the docs above with measured numbers only (`TBD` otherwise): parity and reconciliation results, run duration, calls/run, ETL duration and memory, image size, threshold verdict.
- [ ] **Step 4:** `git commit -m "docs(phase-7): changelog, phase doc, architecture and session memory"`; write `.git/MERGE_SUMMARY.txt` (Built · Key decisions · Tested · Known gaps · Docs touched); push the branch only when the owner asks; never delete branches.
