# Retrain-requested alarms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `puenktlich-drift` / `puenktlich-performance` email exactly when the monitor writes a retrain flag.

**Architecture:** The monitor handler emits a `RetrainRequested` EMF metric (dimensions `service`, `reason`) after every run, 1 for the reason of a newly written flag, else 0. The two alarms switch to that metric (Sum ≥ 1 in 1 h, ALARM email only). Then roll out: API image + Terraform apply.

**Tech Stack:** Python 3.12, aws-lambda-powertools 3.35 (`single_metric`), Terraform (AWS provider), pytest.

**Spec:** `docs/superpowers/specs/2026-11-24-retrain-requested-alarm-design.md`

## Global Constraints

- Namespace `Puenktlich`; dimension `service=monitor` on every monitor metric; new metric `RetrainRequested` with extra dimension `reason` ∈ {`drift`, `performance`}.
- ≤ 10 custom metrics (after: 9) and ≤ 10 alarms (after: still 9).
- Alarm names unchanged: `puenktlich-drift`, `puenktlich-performance`. Statistic `Sum`, period 3600, 1 evaluation period, threshold 1, `GreaterThanOrEqualToThreshold`, `treat_missing_data = "notBreaching"`, `alarm_actions` only (no `ok_actions`).
- `DriftShare`, `BrierRatio`, `BrierDaily`, `AUCDaily`, `LabeledEvents` unchanged.
- No new dependencies. `mypy --strict`, ruff rules as in `pyproject.toml`.
- No Claude co-author trailer in commits (owner rule).

## Review Focus

- A forced run (`threshold_overrides`) that writes a flag → `RetrainRequested{reason}` = 1 (owner call: forced flags alarm too). Test in Task 1.
- A run where a streak exists but `_already_requested` suppresses the write (`flag_written is None`) → both values 0. Covered by the existing `_outcome(... flag_written=None)` path in Task 1.
- `no_data` / `too_few_rows` days → `RetrainRequested` still emitted as 0 for both reasons (the other metrics are skipped). Test in Task 1.
- `run_monitor` raises → nothing emitted (monitor-missing alarm covers it). No new test: the handler calls `_emit` only after `run_monitor` returns.
- Existing EMF assertions must keep working with 3 EMF lines on stdout: the `_metrics` test helper must select the main line (the one with `LabeledEvents`), not assume a single line. Task 1.

---

### Task 1: Emit `RetrainRequested` from the monitor handler

**Files:**
- Modify: `services/api/monitor_handler.py` (`_emit`, module docstring)
- Test: `tests/unit/test_monitor_handler.py`

**Interfaces:**
- Consumes: `MonitorOutcome.flag_written: RetrainRequest | None` (`RetrainRequest.reason: Literal["drift", "performance"]`).
- Produces: EMF records `RetrainRequested` (unit Count) with dimensions `{"service": "monitor", "reason": <reason>}`, one record per reason, every run.

- [ ] **Step 1: Write the failing tests.** Change `_metrics(text)` to return the EMF line that contains `"LabeledEvents"`. Add `_retrain(text) -> dict[str, float]` mapping `reason` → `RetrainRequested[0]` over EMF lines containing `"RetrainRequested"`, and assert each such line has `service == "monitor"` and its `CloudWatchMetrics[0]["Dimensions"]` contains `["service", "reason"]` (order-insensitive). Extend `_outcome(day, status, flag: RetrainRequest | None = None)`. Tests:
  - `test_retrain_requested_zero_without_flag` (ok day, no flag) → `{"drift": 0.0, "performance": 0.0}`.
  - `test_retrain_requested_one_for_flag_reason` (flag reason `performance`) → `{"drift": 0.0, "performance": 1.0}`; `out["flag"] == request_id`.
  - `test_retrain_requested_forced_flag_counts` (flag with `forced=True`, reason `drift`) → `{"drift": 1.0, "performance": 0.0}`.
  - `test_retrain_requested_emitted_on_no_data` (`no_data`) → both 0.0.
  - `RetrainRequested` must not appear in the main line (`"RetrainRequested" not in _metrics(...)`).
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_monitor_handler.py -q` → new tests FAIL (no `RetrainRequested` lines).
- [ ] **Step 3: Implement.** In `_emit`, after `metrics.flush_metrics()`, for each reason in `("drift", "performance")` use `aws_lambda_powertools.metrics.single_metric(name="RetrainRequested", unit=MetricUnit.Count, value=1 if flag and flag.reason == reason else 0, namespace="Puenktlich", default_dimensions={"service": "monitor"})` and `add_dimension("reason", reason)`. Update the module docstring (the new metric and why: alarm = trigger).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_monitor_handler.py -q` → all pass; `make lint typecheck` → clean.
- [ ] **Step 5: Commit** `feat(monitor): emit RetrainRequested when a retrain flag is written`.

### Task 2: Alarms on `RetrainRequested` + docs

**Files:**
- Modify: `infra/modules/observability/main.tf` (resources `drift`, `performance`, comment above them)
- Modify: `docs/architecture.md` §9 (step 7 metric list; alarm table rows Drift / Performance)
- Modify: `docs/runbook.md` (sections `puenktlich-drift`, `puenktlich-performance`: meaning = "the monitor wrote a retrain flag"; note there is no OK email)

**Interfaces:**
- Consumes: Task 1's metric name + dimensions.

- [ ] **Step 1:** Change both alarms per Global Constraints (dimensions `{ service = "monitor", reason = "drift" }` / `"performance"`); descriptions: "The monitor requested a retrain (reason drift): <rule> on 3 days in a row, same model." / "(reason performance) …". Remove `ok_actions`.
- [ ] **Step 2: Run** `make tf-fmt tf-validate tf-scan` → all exit 0.
- [ ] **Step 3:** Docs as listed; add a short incident note (21–22 Nov false alarms) to the runbook's drift section.
- [ ] **Step 4: Commit** `fix(alarms): drift/performance alarms fire on RetrainRequested`.

### Task 3: Verify, review, roll out

- [ ] **Step 1:** `make check` → all pass, coverage ≥ 80 %; `uv run pre-commit run --all-files` → clean.
- [ ] **Step 2:** `code-reviewer` subagent on the branch; fix confirmed findings test-first.
- [ ] **Step 3:** `make aws-check` → `make image-push` (new API tag) → set `api_image_tag` in the git-ignored `infra/envs/prod/terraform.auto.tfvars` → `make tf-plan` (expect: monitor + API Lambdas image update, 2 alarms changed, nothing destroyed) → `make tf-apply`.
- [ ] **Step 4:** Manual monitor run for yesterday: `aws lambda invoke --function-name puenktlich-monitor` with `{}`; then `aws cloudwatch list-metrics --namespace Puenktlich --metric-name RetrainRequested` shows both reasons; `aws cloudwatch describe-alarms --alarm-names puenktlich-drift puenktlich-performance` → both `OK`; `make smoke-prod-live` passes.
- [ ] **Step 5:** Update CLAUDE.md status; commit `docs: retrain-requested alarm rolled out`.
