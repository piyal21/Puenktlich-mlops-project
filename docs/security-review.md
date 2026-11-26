# Security review — Phase 10 (2026-11-26)

Scope: the controls in `architecture.md` §12, checked against the code and Terraform on
`main` + `phase-10/retrain-alarm` (commit `8daa96b`). Method: static reading of `infra/`,
`services/`, `src/dbdelay/`, `.github/workflows/ci.yml`, `frontend/`; `npm audit --omit=dev`
(0 vulnerabilities). No AWS reads: the CLI session could not be renewed on 2026-11-26
(see finding S5). Numbers marked *estimate* are calculated from AWS list prices, not measured.

## Summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| S1 | Cost-abuse ceiling: throttling allows ~ $4/day of Lambda/API spend (*estimate*), enough to burn the $100 Free-plan credits in weeks | Medium | Owner decision |
| S2 | `/api/docs` loads Swagger UI from jsdelivr with a floating version (`swagger-ui-dist@5`), no integrity hash, on our origin | Low–Medium | Fix proposed |
| S3 | API Gateway's default `execute-api` URL is public: callers can skip CloudFront (60 s board cache, security headers) | Low | Accept + document (S1 is the real limit) |
| S4 | Dormant `puenktlich-sample-board` Lambda still may write the **live** public board key | Low | Fix proposed (needs AWS) |
| S5 | Single sign-in identity, no second admin path: on 2026-11-26 the owner's sign-in showed "no projects" while the stack kept running | High (availability of operations) | Runbook + AWS Support |

Accepted as before (ADR 0002, `infra/.trivyignore`), re-confirmed: SSE-S3 instead of KMS keys,
no S3/CloudFront access logs, no WAF, no X-Ray, default CloudFront certificate. Each would cost
about as much as the whole $1/month budget; the data is public timetable data. The Phase 6 note
"revisit access logs in Phase 10" is closed with this decision: keep them off (cost, and fewer
personal data — no client IPs are stored anywhere).

## Controls checked (§12)

| Area | Result | Evidence |
|---|---|---|
| Identity | ✅ no IAM users, no long-lived keys; CI has `contents: read` only, actions pinned by SHA, `persist-credentials: false` | `ci.yml`; ⚠️ see S5 |
| Least privilege | ✅ one role per Lambda; S3 actions on exact prefixes, `ListBucket` with `s3:prefix` conditions; SSM on exact parameter ARNs; the public API role is read-only and cannot read the DB API keys | `infra/envs/prod/main.tf` |
| Secrets | ✅ DB API keys in SSM SecureString, outside Terraform state; `SecretStr` in settings; sent only as request headers, never logged | `config.py`, `timetables/client.py`, `modules/ssm` |
| Storage | ✅ all 4 buckets: Block Public Access, `BucketOwnerEnforced`, SSE-S3, versioning, TLS-only policy; web bucket readable only by this CloudFront distribution (OAC + `AWS:SourceArn`); state bucket `prevent_destroy`, lockfile, encrypted | `modules/data_lake`, `modules/web_cdn`, `bootstrap/` |
| Edge | ✅ HSTS 1 year, strict CSP (`script-src 'self'`, `frame-ancestors 'none'`), `nosniff`, `DENY`, `strict-origin-when-cross-origin`; HTTPS only, TLS 1.2 to the origin | `modules/web_cdn`; ⚠️ docs page: S2 |
| API abuse | ✅ throttling 10 req/s, burst 20; `hours` 1–6; body ≤ 4,096 bytes (411/413); `extra="forbid"` + patterns/lengths on `/predict`; station search ≤ 50 chars; errors are problem+json without stack traces or submitted values; request ids validated before reuse | `modules/http_api`, `services/api/app/` |
| Cost abuse | ⚠️ cache + throttling + budget e-mail exist, but see S1 | |
| Supply chain | ✅ `uv.lock` (hashes), `package-lock.json`, pip-audit (both images + MLflow), `npm audit --audit-level=high`, Trivy config + image scans, gitleaks on full history, Dependabot weekly; ECR tags immutable, scan on push | `ci.yml`, `modules/ecr` |
| Code | ✅ Ruff `S`; untrusted XML only through `defusedxml`; no `pickle`/`eval`/`shell=True`; the one `subprocess` call is a fixed `git rev-parse` argv; frontend has no `innerHTML`/`dangerouslySetInnerHTML` | `timetables/parser.py`, `training/pipeline.py` |
| Model integrity | ✅ SHA-256 per file in the manifest, verified before load; models bucket versioned (90 days) | `registry/artifacts.py` |
| Privacy | ✅ no accounts or cookies; API access logs store request id, time, method, path, status, latency — no IP, no user agent; 14-day log retention | `modules/http_api`, `modules/lambda_image` |

## Findings

### S1 — Cost-abuse ceiling (Medium, owner decision)

- **What:** nothing caps spend; the budget only e-mails. A script hitting uncached paths
  (`POST /api/v1/predict`, or any path through the `execute-api` URL, S3) at the throttle limit
  keeps the account's 5 concurrent Lambda executions busy all day.
- **Ceiling (*estimate*):** 5 executions × 86,400 s × 0.5 GB ≈ 216,000 GB-s/day ≈ $3.60/day
  compute, plus ≤ 864,000 requests/day (≈ $0.17 Lambda + ≈ $0.86 HTTP API) ≈ **$4–5/day**.
  The $100 Free-plan credits would last about 3 weeks; when they run out the account stops.
- **Options:** (a) lower the throttle to rate 2 / burst 5 (a demo needs far less; one board
  load is one request) — cuts the request part, not the concurrency part; (b) add an alarm on
  API Lambda invocations (e.g. > 20,000 per hour) — the 10th and last free alarm; (c) accept.
  Recommended: (a) + (b); both are free and need one `make tf-apply`.

### S2 — Floating third-party script on the docs page (Low–Medium, fix proposed)

- **What:** FastAPI's default Swagger UI page loads `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/…`.
  `@5` follows every new 5.x release, and the docs CSP allows all of `cdn.jsdelivr.net`. A bad
  release (or a hijacked package) would run on our origin. Impact is limited (no cookies, no
  login, read-only data), but it could deface the site or misuse the API under our name.
- **Fix:** serve the docs page with an **exact** version (e.g. `swagger-ui-dist@5.x.y`) and
  narrow the docs CSP to that path (`https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.x.y/`).
  jsdelivr npm files are immutable per version. No new dependency. Code + test now; the CSP
  part needs `make tf-apply`.

### S3 — Public `execute-api` URL (Low, accept)

- **What:** CloudFront is not the only way in. Direct callers skip the 60 s board cache and the
  security headers (headers do not matter for JSON API clients). The API is public and
  read-only by design, so no data is exposed.
- **Why accept:** disabling the default endpoint needs a custom domain; an origin-secret header
  checked in the Lambda adds a secret to rotate. The cost risk is the same as S1 and is handled
  there.

### S4 — Dormant sample-board Lambda can overwrite the live board (Low, fix proposed)

- **What:** since `board_mode = "live"` the `puenktlich-sample-board` Lambda has no schedule, but
  its role still has `s3:PutObject` on `live/boards/latest.json.gz`. A manual invoke would replace
  the live board with the sample day until the next 15-minute ingest run.
- **Fix:** grant `WriteBoard` only while `board_mode = "sample"` (or remove the Lambda). Needs a
  `make tf-plan` / `make tf-apply` once AWS access is back.

### S5 — Single sign-in identity (High for operations)

- **What:** the whole account is reached through one AWS sign-in (the new project-based
  experience: no root user, no IAM users, no IAM Identity Center on the Free plan). On
  2026-11-26 that sign-in showed "You don't have any projects yet" in the console and in
  `aws login`, while the stack kept serving. Until AWS Support restores access, nobody can
  deploy, roll back a model or rotate keys.
- **Audit (2026-11-26):** no Claude session, repo code or shell history on the laptop deleted or
  closed anything. The only AWS deletion ever made from the laptop: 4 old Terraform state
  versions (2026-11-19, owner request).
- **Do:** keep MFA on; record the recovery path in `docs/runbook.md` ("lost console access":
  do not create a new project; contact AWS Support with account ID, region, project name and
  proof the stack is running). After access returns: CloudTrail event history check for
  close/delete events. Longer term this is part of the "Free plan ends 2027-04-25" decision
  (Phase 10 task): a Paid plan unlocks advanced features (Identity Center → a second admin).
