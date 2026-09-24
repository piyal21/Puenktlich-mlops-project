# PRD — Pünktlich: Deutsche Bahn Delay Risk Predictor

> **Product Requirements Document.** Defines *what* we build, *for whom*, and *how we know it works*.
> Companion docs: `architecture.md` (how), `rules.md` (constraints for AI-assisted coding), `phases.md` (build order), `design.md` (UI look & feel).

| Field | Value |
|---|---|
| Project name | **Pünktlich** ("on time?") — repo slug `puenktlich` |
| Owner | MD Piyal Ahmmed |
| Type | End-to-end MLOps portfolio project (public, open source) |
| Status | Planning |
| Primary goal | Demonstrate production-grade, junior-level MLOps skills to hiring managers in Germany |
| Secondary goal | Ship a small public web app real people can actually use |

---

## 1. Problem

Train delays are part of daily life in Germany. Riders see the *current* delay on the departure board, but they can't tell **how risky a departure is before it is delayed** — e.g. "Is the 17:42 RE from Erfurt Hbf usually late at this time on a Friday?"

Deutsche Bahn publishes open timetable and delay data (CC BY 4.0), and a community project archives it as a large historical dataset. That makes it possible to learn delay patterns and show a **delay-risk score** per departure.

For the portfolio, the problem is a vehicle: it naturally needs **real ETL, large data, time-based validation, real drift, fast ground truth, and a public UI** — exactly the skills an MLOps role needs.

## 2. Goals and non-goals

### Goals
1. Predict, for an upcoming departure at a supported station, the **probability it departs ≥ 6 minutes late** (DB's official punctuality threshold: a stop counts as punctual when it is less than 6 minutes late).
2. Serve predictions through a **public web app** and a **documented REST API**.
3. Run a complete, automated **MLOps lifecycle**: ingestion → validation → features → training → registry → gated release → serving → monitoring → drift-triggered retraining.
4. Deploy on **AWS within free-tier/credit limits** (target **< $5/month**), fully defined in **Terraform**, shipped by **GitHub Actions**.
5. Make the system **transparent**: a public *Model Health* page showing live accuracy, drift and model version.

### Non-goals (explicitly out of scope)
- Door-to-door journey planning, connections, or routing (use DB Navigator for that).
- Predicting the exact number of delay minutes (v1 is binary risk).
- Real-time streaming infrastructure (Kafka, Kinesis). Micro-batch every 15 min is enough.
- Kubernetes, SageMaker, MWAA, or any always-on paid compute.
- User accounts, logins, payments, or storing personal data.
- Covering every station in Germany. v1 covers a configured set of ~30 stations.
- Being an official product. The app **must not** look like or claim to be Deutsche Bahn.

## 3. Target users

| Persona | Who | What they want | How the product serves them |
|---|---|---|---|
| **Commuter Clara** (primary end user) | Student/commuter in Germany, checks trains on her phone | "Should I take the earlier train to make my connection?" | Station board with a clear risk badge per departure, mobile-first |
| **Recruiter / Hiring manager Hannes** (primary portfolio audience) | Tech lead hiring a Werkstudent/Junior MLOps engineer | "Can this person build and operate an ML system, not just a notebook?" | README with diagrams, live demo, Model Health page, clean CI/CD, IaC, tests |
| **Developer Dev** (secondary) | Engineer curious about the API | Call the API, read the docs | OpenAPI docs at `/api/docs`, stable versioned endpoints |

## 4. User stories

**End users**
- US-1: As a rider, I search a station by name and see upcoming departures (next ~3 h) with a delay-risk badge (Low / Medium / High) and the probability.
- US-2: As a rider, I tap a departure and see *why* it is risky (top 3 contributing factors, e.g. "Friday evening", "This line is often late").
- US-3: As a rider, I see the live status too (current reported delay, cancelled) so I trust the app.
- US-4: As a rider on a phone with poor connection, the page loads fast and still works if the API is briefly down (clear error state, retry).

**Portfolio audience**
- US-5: As a hiring manager, I open the *Model Health* page and see today's model version, when it was trained, daily accuracy/calibration over time, and drift status.
- US-6: As a hiring manager, I read the README and understand the architecture in < 5 minutes via diagrams.
- US-7: As a reviewer, I can run the whole stack locally with one command (`make up`) and a small sample dataset.

**Operator (me)**
- US-8: As the operator, I get an email when ingestion stops, the API errors, drift is detected, or the AWS bill exceeds budget.
- US-9: As the operator, a new model is only released if it beats the current champion and the baseline; otherwise the pipeline ends green with "not promoted".
- US-10: As the operator, I can roll back to the previous model in one command, without redeploying code.

## 5. Features

### 5.1 MVP (must have)
| ID | Feature | Notes |
|---|---|---|
| F-1 | Station search | Autocomplete over configured stations (`configs/stations.yaml`) |
| F-2 | Departure board with risk | Next 3 h of departures from live data, each with `p_late`, risk level, live status |
| F-3 | Departure detail with explanation | Top contributing features (LightGBM SHAP contributions) in plain language |
| F-4 | Model Health page | Current model card, daily AUC/Brier chart, drift status, last retrain, data freshness |
| F-5 | About / How it works page | Short explanation + link to GitHub + data attribution + "not affiliated with DB" disclaimer |
| F-6 | Public REST API | Versioned `/api/v1/...`, OpenAPI docs, rate limited |
| F-7 | Historical ETL | Backfill from Hugging Face dataset into bronze/silver layers, validated |
| F-8 | Live ETL | Scheduled ingestion from DB Timetables API into the data lake |
| F-9 | Training pipeline | Features → baseline → LightGBM → calibration → evaluation → MLflow registry |
| F-10 | Gated release & rollback | Champion/challenger with metric gates; pointer-based release; one-step rollback |
| F-11 | Monitoring | Daily data drift, prediction drift, model performance on yesterday's real outcomes |
| F-12 | Continuous training | Drift/performance trigger + monthly schedule start retraining |
| F-13 | CI/CD | Lint, type-check, test, scan, build, Terraform plan/apply, deploy, smoke test |
| F-14 | Infrastructure as code | 100% of AWS resources in Terraform (except the one-time bootstrap) |
| F-15 | Alerts & cost guardrails | CloudWatch alarms + SNS email + AWS Budgets |

### 5.2 Later (nice to have — see `phases.md` stretch phase)
- Deep-learning challenger (PyTorch entity-embedding MLP → ONNX) competing against LightGBM.
- Live-context features (station delay level in the last hours) with a documented training/serving parity test.
- German/English language toggle.
- Canary release using weighted Lambda aliases.
- Load test report (k6 or Locust).
- DVC for dataset versioning.

## 6. Functional requirements

### 6.1 Prediction definition
- **Unit of prediction:** one *departure event* = (station EVA number, train ride id, planned departure time).
- **Label:** `is_late = departure delay_in_min >= 6`. Cancelled departures are **excluded** from the label (shown in the UI as "Cancelled", never scored as late/on-time).
- **Prediction time:** when the user asks (typically 0–3 h before departure). v1 features must only use information that is **known at least 60 minutes before planned departure** (see leakage policy in `rules.md`).
- **Output:** `p_late` ∈ [0, 1], calibrated; `risk_level` = Low (< 0.20), Medium (0.20–0.45), High (≥ 0.45). Thresholds live in config and are revisited after EDA.

### 6.2 Data
- Historical source: Hugging Face dataset `piebro/deutsche-bahn-data` (monthly parquet, from July 2024, CC BY 4.0).
- Live source: DB API Marketplace **Timetables API** (`/plan`, `/fchg`, `/station`), free plan, 60 calls/min, CC BY 4.0.
- Station scope: ~30 stations in `configs/stations.yaml` (major hubs + a few Thuringia stations if present in history). Same list for training and serving.
- Every dataset has a schema contract and validation step; bad data stops the pipeline instead of flowing downstream.

### 6.3 API (v1)
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness + model version loaded |
| GET | `/api/v1/stations?q=` | Search supported stations |
| GET | `/api/v1/stations/{eva}/departures?hours=3` | Live board with predictions |
| POST | `/api/v1/predict` | Score one departure (for developers) |
| GET | `/api/v1/model` | Current champion metadata (version, trained_at, metrics) |

Errors use `application/problem+json` (RFC 9457). Details in `architecture.md`.

## 7. Non-functional requirements

| Category | Requirement |
|---|---|
| Latency | Warm API p95 < 300 ms for `/departures`; cold start < 6 s |
| Availability | Best effort; the frontend must degrade gracefully when the API is down |
| Freshness | Live board data ≤ 20 minutes old; stale data is labeled as such in the UI |
| Cost | < $5/month steady state; hard budget alarms at $1 and $5 |
| Security | No secrets in git; least-privilege IAM; OIDC for CI; image & dependency scanning; rate limiting; no personal data stored |
| Reproducibility | Every model traceable to code commit, data snapshot hash, params, and metrics in MLflow |
| Accessibility | WCAG 2.1 AA; risk never communicated by color alone |
| Mobile | Mobile-first, usable at 360 px width |
| Portability | Whole stack runs locally via Docker Compose (MinIO replaces S3) |
| Observability | Structured JSON logs, CloudWatch metrics/alarms, public health page |
| Legal | Attribute DB data (CC BY 4.0) and the HF dataset; show "not affiliated with Deutsche Bahn" |

## 8. ML requirements

- **Split strictly by time** (train → validation → test = most recent 14 days). No random splits.
- **Baseline required:** historical late-rate lookup by (station, train_type, hour, weekday). The model must beat it.
- **Primary metric:** Brier score (calibration + accuracy). **Secondary:** ROC-AUC, PR-AUC, log loss, expected calibration error.
- **Slice checks:** metrics per train type (ICE/IC/RE/RB/S) and per station tier; no slice may degrade by more than the configured tolerance vs. champion.
- **Explainability:** per-prediction top contributions (LightGBM `pred_contrib`), global feature importance in the model card.
- **Model card** published with every champion (data window, metrics, limitations, intended use).

## 9. MLOps requirements

| Capability | Requirement |
|---|---|
| Orchestration | Airflow 3 DAGs for backfill, training pipeline, drift watch |
| Experiment tracking | MLflow 3 (Postgres backend, S3-compatible artifact store) |
| Registry | MLflow registered model with `@champion` / `@challenger` aliases |
| Release | Champion exported to S3 with `manifest.json` + checksums; SSM parameter points to the live version |
| Rollback | Set the SSM pointer to the previous version (`make rollback`) — no code deploy |
| Monitoring | Evidently reports daily; metrics to CloudWatch + public JSON |
| Retraining triggers | Drift share > threshold for 3 consecutive days, or Brier worse than champion's test Brier by > 10 %, or monthly schedule |
| CI/CD | GitHub Actions with OIDC, required checks on PRs, deploy on `main` |
| IaC | Terraform ≥ 1.10, S3 remote state with native locking |

## 10. Success metrics

**Product**
- Model beats baseline Brier score by ≥ 5 % on the time-based test set.
- Live board data freshness ≤ 20 min for ≥ 95 % of the day.
- Warm p95 latency < 300 ms.

**Engineering / portfolio**
- `make up` → working local stack in < 10 minutes on a fresh machine.
- CI green on `main`; ≥ 80 % line coverage on `src/`.
- At least one real, documented drift event → retrain → gated decision (promoted or rejected) visible on the Model Health page.
- Monthly AWS cost < $5.
- README understandable in 5 minutes (test with a friend).

## 11. Constraints and assumptions

- AWS account on the new free-tier model (credits, 6-month Free plan). Region **eu-central-1 (Frankfurt)**.
- Local machine: x86-64, 32 GB RAM, no GPU → CPU-friendly models; Lambda images built for `x86_64`.
- Airflow and MLflow run **locally** in Docker (managed Airflow is too expensive). Retraining therefore runs when the local stack is up; this is a documented, conscious trade-off.
- DB API free plan: 60 calls/min, no SLA. Ingestion must respect the limit and tolerate gaps.
- The Hugging Face dataset has known missing hours in some months; pipelines must tolerate gaps.

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Label/feature leakage (using info not known before departure) | Model looks great offline, fails live | Leakage policy, feature "as-of" timestamps, unit tests for as-of logic |
| Training/serving skew | Different features live vs. training | One shared feature module used by training, API and monitor; parity test |
| DB API outage / rate limit | Stale board, missing labels | Retries with backoff, freshness alarm, stale badge in UI |
| AWS cost surprise | Bill | Serverless only, no NAT/ALB/EC2, budget alarms in Terraform, ECR lifecycle policy |
| Seasonality flags "drift" constantly | Alert fatigue | Compare against same-weekday reference; require 3 consecutive days; use performance-based trigger too |
| Free plan account closes after 6 months | Demo goes offline | Upgrade to paid plan before expiry (credits still apply) |
| Trademark/impersonation | Legal/ethical | Own name, own colors, no DB logo, clear disclaimer |

## 13. Glossary

| Term | Meaning |
|---|---|
| EVA number | Unique numeric station id used by DB APIs (e.g. Erfurt Hbf) |
| Hbf | Hauptbahnhof — main station |
| ICE / IC / EC | Long-distance trains |
| RE / RB | Regional express / regional trains |
| S | S-Bahn (suburban rail) |
| Bronze / Silver / Gold | Raw / cleaned & conformed / feature-ready data layers |
| Champion / Challenger | Live model / candidate model competing to replace it |
| Brier score | Mean squared error of predicted probabilities (lower is better) |
| Drift | Change in input data or model behaviour over time |

## 14. Open questions (decide during Phase 1)
- Final station list (check coverage per month in the HF data).
- Risk thresholds after looking at the calibrated probability distribution.
- Whether `line_number` / `final_destination_station` cardinality needs grouping.
- How `delay_in_min` behaves when no change time exists (0 vs. null) — verify in EDA.
