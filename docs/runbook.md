# Runbook — alarms

Every alarm sends an email from the SNS topic `puenktlich-alerts` (and a second email when it returns
to OK — except *drift* and *performance*, which email only "retrain requested"). Region `eu-north-1`. All commands run on the laptop after `aws login --profile puenktlich`.

Useful everywhere:

```bash
make aws-check                                    # login still valid?
MSYS_NO_PATHCONV=1 aws logs tail /aws/lambda/puenktlich-<name> --since 2h --profile puenktlich --region eu-north-1
make smoke-prod-live                              # the public site end to end
```

## puenktlich-api-5xx — API returned more than 5 server errors in 5 minutes

- **Meaning:** users see "Something went wrong" or empty boards.
- **First checks:** `aws logs tail /aws/lambda/puenktlich-api` for `unhandled error` and the request ids;
  `make smoke-prod-live`; did a deploy just happen (`git log`, the image tag in the git-ignored tfvars)?
- **Fix:** a bad image → set the previous `api_image_tag`, `make tf-plan`, `make tf-apply`. A bad model →
  `make rollback-prod`. A broken board file → see *ingestion stopped*.

## puenktlich-api-throttles — API Lambda was throttled

- **Meaning:** more than 5 requests ran at the same moment (account concurrency limit = 5, ADR 0002).
- **First checks:** CloudWatch `Throttles` and `ConcurrentExecutions` for `puenktlich-api`; is a monitor
  backfill or another Lambda burst running at the same time?
- **Fix:** usually passes on its own (CloudFront caches boards for 60 s). Spread manual Lambda
  invocations out. A higher limit needs an AWS quota request (cost decision: ask the owner).

## puenktlich-sample-board-errors (only while `board_mode = "sample"`)

- **Meaning:** the replayed sample board was not refreshed twice in a row.
- **First checks / fix:** logs of `puenktlich-sample-board`; `make upload-sample-days` if sample days are missing.

## puenktlich-ingestion-stopped — no departures ingested for 60 minutes

- **Meaning:** the live board goes stale; tomorrow's live silver gets a gap.
- **First checks:** logs of `puenktlich-ingest` (HTTP 401/403 = DB API keys, 429 = rate limit, timeouts =
  DB API down); the scheduler `puenktlich-ingest` is enabled; the DB API status page.
- **Fix:** rotated or expired keys → put the new keys into `.env`, `make put-db-api-secrets`. DB API outage →
  wait; the quality report flags the gap hours and training excludes them.

## puenktlich-ingest-errors — ingest failed in two consecutive runs

- **Meaning:** the ingest Lambda raised (not just an empty answer).
- **First checks:** the stack trace in the ingest logs; S3 permissions (`AccessDenied`), memory (`Max Memory Used`
  in the REPORT line vs 512 MB), the time budget.
- **Fix:** code bug → fix test-first, `make jobs-image-push`, tag in tfvars, `make tf-apply`.

## puenktlich-etl-errors — nightly live ETL failed

- **Meaning:** yesterday has no live silver; the monitor will report `no_data` for it.
- **First checks:** logs of `puenktlich-etl-daily`; memory (ETL peaked at ~400 of 512 MB in Phase 7).
- **Fix:** re-run the day: `aws lambda invoke --function-name puenktlich-etl-daily --payload '{"day":"YYYY-MM-DD"}' …`
  (idempotent), then re-run the monitor for that day (see *monitor missing*).

## puenktlich-empty-stations — a station had no departures yesterday

- **Meaning:** EVA drift (a station got a new number, like Berlin Hbf) or a feed gap for that station.
- **First checks:** `silver/_quality/source=live/date=D.json` → `empty_stations`; the DB API plan for that EVA.
- **Fix:** EVA changed → add the new number to `hf_aliases` in `configs/stations.yaml`, redeploy the jobs image.

## puenktlich-monitor-missing — no daily monitor result for 26 h

- **Meaning:** the health page stops updating ("Monitor late" after 2 days) and no retrain can be requested.
- **First checks:** logs of `puenktlich-monitor` (`ModelNotAvailableError` = no champion / SSM pointer;
  `ArtifactIntegrityError` = bundle or reference sample checksum; memory); did the ETL run (*etl errors*)?
- **Fix:** after fixing the cause, re-run the missed days (idempotent):
  `aws lambda invoke --function-name puenktlich-monitor --payload '{"day":"YYYY-MM-DD"}' --cli-binary-format raw-in-base64-out out.json …`

## puenktlich-drift — retrain requested (reason drift)

- **Meaning:** the monitor has just written `control/retrain_requested.json` with reason `drift`:
  drifted share of model inputs above 0.30 on 3 calendar days in a row, same model, no open request.
  The trains the model sees differ from its training data (new lines, timetable change, a station
  renamed). The alarm watches `RetrainRequested{reason=drift}`, so email = flag. There is no
  "back to OK" email; the alarm returns to OK on its own, at the latest after the next nightly monitor
  run (CloudWatch keeps the last datapoint while newer ones are missing). A second flag during that
  time sends no email, so the source of truth is `control/retrain_requested.json` / the health page.
- **History:** until Phase 10 this alarm used rolling 24 h windows on `DriftShare`; manual backfill
  runs on 19 Nov 2026 made it fire falsely on 21 + 22 Nov without any retrain request.
- **First checks:** health page → "Data drift on the last checked day" (which inputs, which scores);
  timetable change dates (mid-December); `excluded_rows` and gap flags (a feed problem looks like drift).
- **Fix:** run the retrain loop: `make retrain-sync` → Airflow `drift_watch` picks it up within 30 min
  (`make airflow-up`, keep the laptop awake) → `make retrain-publish`. A rejected challenger keeps the
  current model; that is a valid outcome and shows on the health page.

## puenktlich-performance — retrain requested (reason performance)

- **Meaning:** the monitor has just written a retrain flag with reason `performance`: daily Brier
  above 1.10 × the champion's test Brier on 3 calendar days in a row, same model (alarm on
  `RetrainRequested{reason=performance}`, ALARM email only). Predictions are worse on real
  outcomes than in the test period. Known cause (Phase 7):
  HF history probably undercounts lateness, so a model trained on it underestimates live late rates —
  compare "actual vs predicted late rate" on the health page.
- **First checks:** health page charts (is it calibration — predicted below actual — or ranking — AUC down?);
  label/feed gaps in the quality reports.
- **Fix:** the retrain loop as for *drift* (training now includes live days, live wins over HF).
  If the challenger is rejected again and again, investigate the label source before changing the gate.

## Budget — AWS Budgets `puenktlich-monthly` (actual > $1, forecast > $5)

- **Meaning:** spend is above plan (credits and refunds excluded).
- **First checks:** Cost Explorer by service (Lambda, S3, CloudWatch, ECR storage, data transfer).
- **Fix:** anything that raises cost needs the owner's decision (standing rule).

## Lost console / CLI access (no alarm — seen 2026-11-26)

- **Meaning:** signing in (console or `aws login --profile puenktlich`) shows "You don't have any
  projects yet". The stack keeps running (site, 15-min ingest, nightly jobs), but nobody can
  deploy, roll back a model or rotate keys. See `docs/security-review.md` S5.
- **First checks:** is the site still live (`curl https://d37lj2a19ugjqm.cloudfront.net/api/v1/health`,
  `board_generated_at` < 20 min old)? Same sign-in e-mail as when the account was created?
- **Fix:** do **not** click "Create your first project" and change nothing else. Contact AWS
  Support (Contact us → account and billing → sign-in) with: account ID, region `eu-north-1`,
  project name, sign-in e-mail, the date access was last good, and the live URL as proof the
  resources still run; ask them to restore access and keep every resource.
- **After access returns:** CloudTrail → Event history (90 days) filtered on event names with
  `Close`/`Delete`; `aws lambda list-functions`, `aws s3api list-buckets`; `make tf-plan` must show
  "No changes".
