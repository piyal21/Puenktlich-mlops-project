# Threat model (STRIDE-lite) — Phase 10, 2026-11-26

One page: what we protect, who could attack it, and which control answers each threat.
Details and open findings: `docs/security-review.md` (S1–S5). Controls: `architecture.md` §12.

## What we protect

| Asset | Why it matters |
|---|---|
| Public site + API availability | The portfolio demo must be up |
| Live board and predictions (integrity) | Wrong numbers under our name |
| Model artifacts + champion pointer | A swapped model silently serves bad predictions |
| DB API keys | Our access to the live feed (no new Timetables subscriptions are possible) |
| AWS account access + $100 credits | Lose them and the whole online plane stops |
| Repo, CI, laptop (training plane) | Where every release starts |

No personal data is stored: no accounts, no cookies, no client IPs in logs.

## Data flow and trust boundaries

```
 Internet user ──HTTPS──▶ CloudFront ──OAC──▶ S3 web bucket (static site)
                   │  B1      └──HTTPS──▶ API Gateway (2 req/s) ──▶ API Lambda ──read──▶ S3 data/models, SSM pointer
 DB Timetables API ◀──HTTPS── ingest Lambda (15 min) ──write──▶ S3 bronze/board        B2: AWS-internal, IAM per Lambda
       B3 (untrusted XML)      etl / monitor Lambdas (nightly) ──▶ S3 silver/monitoring
 HF dataset ──▶ laptop: Airflow + MLflow + MinIO (training) ──make targets, aws login──▶ S3 models + SSM   B4: admin path
 GitHub ──▶ CI checks only (no AWS credentials; OIDC blocked by SCP)                                    B5: supply chain
```

## Threats and controls

| STRIDE | Threat | Control | Residual |
|---|---|---|---|
| **S**poofing | Someone signs in as the owner | AWS sign-in with MFA; CLI sessions 12 h; no IAM users or long-lived keys | Single identity, no second admin (S5) |
| | Fake upstream data | TLS to the DB API; DB keys only in SSM | — |
| | Direct access to the web bucket | OAC + `AWS:SourceArn` = this distribution only | — |
| **T**ampering | Swapped or corrupted model | SHA-256 per file in the manifest, checked before load; versioned bucket; pointer in SSM; only the laptop writes | Laptop compromise = release compromise |
| | Overwritten board | Only the ingest role may write it; sample-board write only in sample mode (S4) | — |
| | Malicious XML from upstream | `defusedxml`, parser rejects bad events, Pandera contract on silver | — |
| | Poisoned history (HF) | Silver contract + quality reports; gate compares Brier, AUC, slices before release | Slow, subtle drift could pass the gate |
| | Malicious dependency or CDN script | Hash-locked `uv.lock`, `package-lock.json`, SHA-pinned actions, Trivy, pip-audit, npm audit, Dependabot; Swagger UI pinned + narrow CSP (S2) | Trust in pinned upstream releases |
| **R**epudiation | "Who changed this?" | CloudTrail event history (90 days); git history; MLflow lineage; `control/decisions/` + `monitoring/retrains.json`; API access logs with request id (14 days) | No S3/CloudFront access logs (cost) |
| **I**nformation disclosure | DB keys leak | SecureString, not in Terraform state (old versions deleted 2026-11-19), `SecretStr`, never logged | Rotation still pending (owner) |
| | Errors reveal internals | problem+json without stack traces or submitted values | — |
| **D**enial of service / cost | Traffic flood or scraping | CloudFront 60 s board cache; throttle 2 req/s, burst 5; account concurrency 5; `puenktlich-api-traffic` alarm; budget e-mail | No hard spend cap; public `execute-api` URL (S1, S3) |
| | Upstream outage or rate limit | Rate limiter (50 calls/min; the API allows 60), stop near the Lambda deadline; board marked stale after 20 min; ingestion alarms | Board goes stale, site stays up |
| | Lost AWS access | Runbook "lost console access"; AWS Support | Operations blocked until restored (S5) |
| **E**levation of privilege | A Lambda bug used to reach more | One role per Lambda, exact prefixes/ARNs, no `*`; the public API is read-only | — |
| | Code execution from input | No `pickle`/`eval`/`shell=True`; strict Pydantic models; body ≤ 4 KB | — |
| | CI used to reach AWS | CI has no AWS credentials at all | — |

## Top residual risks (owner view)

1. **Single sign-in identity (S5):** happened on 2026-11-26. Mitigation: AWS Support; longer term the Free-plan decision (Paid plan → a second admin).
2. **Cost abuse (S1):** throttle + alarm warn within the hour; there is no hard cap on this account.
3. **DB API key rotation** not yet done after the Phase 7 state exposure.
4. **Laptop = release path:** whoever controls the laptop and an AWS session can release a model. Keep the OS updated, disk encrypted, and `aws login` sessions short.
