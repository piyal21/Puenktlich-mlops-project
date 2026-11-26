# Retrain-requested alarms (Phase 10 hardening) — design

Status: approved by the owner 2026-11-24.

## Intent

An alarm email from `puenktlich-drift` or `puenktlich-performance` must mean exactly one thing:
**the monitor requested a retrain** (it wrote `control/retrain_requested.json`). Today the alarms
and the retrain trigger use different rules, so they can disagree.

## Why (the incident)

`puenktlich-drift` went to ALARM on 21 and 22 Nov 2026 (05:04 → OK 19:04) although no retrain
was requested. The alarm takes the `Maximum` of `DriftShare` over rolling 24 h windows
(3 × 86400 s). Manual backfill/re-runs on 19 Nov emitted v1-era values (0.43–0.57) at unusual
hours; mixed with the nightly values they made three "bad windows" in a row. The trigger
(`evaluate_trigger`) correctly saw no streak: it needs 3 consecutive calendar days, all `ok`,
same model version, plus `_already_requested` de-duplication. Alarm windows cannot express that.

## Goals / non-goals

- Goal: alarm = flag written, for both reasons (drift, performance). One email per request.
- Goal: stay within the free tier (≤ 10 custom metrics, ≤ 10 alarms).
- Non-goal: changing the trigger rule, thresholds, history, or the health page.
- Anti-goal: an email that fires without a flag, or a flag without an email.

## Approaches

1. **Chosen — `RetrainRequested` metric from the monitor.** The handler emits
   `RetrainRequested` (dimensions `service=monitor`, `reason=drift|performance`) after every run:
   value 1 for the reason of a newly written flag, else 0. Alarms: `Sum >= 1` in a 3600 s period.
   Alarm and trigger share one decision, so they cannot disagree.
2. Re-tune the existing alarms (e.g. calendar-aligned periods, `datapoints_to_alarm`). Rejected:
   CloudWatch cannot check "same model version" or "already requested"; disagreement stays possible.
3. Alarm on S3 writes of the flag object (EventBridge on S3 events). Rejected: more moving parts
   (bucket notifications, EventBridge rule), no gain over a metric the handler already can emit.

## Design

- `services/api/monitor_handler.py::_emit`: always emit `RetrainRequested` for both reasons
  (2 custom metrics → 9 in total) via Powertools `single_metric` with dimension `reason`, so the
  existing metrics keep only `service=monitor`. Value = 1 iff `outcome.flag_written` has that reason.
- Forced demo runs (`threshold_overrides`) also count: they write a real flag (owner-approved call).
- Emitted after the history/flag write, so a crash before the write emits nothing (the
  *monitor missing* alarm covers crashes).
- `infra/modules/observability/main.tf`: `puenktlich-drift` / `puenktlich-performance` keep their
  names; metric `RetrainRequested`, dimensions `{service = "monitor", reason = "<reason>"}`,
  statistic `Sum`, period 3600, 1 evaluation period, threshold 1, `GreaterThanOrEqualToThreshold`,
  `treat_missing_data = "notBreaching"`, **`alarm_actions` only** (no OK email: "OK" only means
  time passed). Alarm count stays 9.
- `DriftShare` and `BrierRatio` stay as metrics (no alarm on them).
- Docs: `architecture.md` §9 (metrics + alarm table), `docs/runbook.md` (alarm meaning), handler
  docstring.
- Rollout: API image push → `make tf-apply` (no cost change; owner standing OK).

## Left to the builder

Test layout; exact wording of alarm descriptions and docs.
