# Phase 8 — CI/CD (route A)

Branch `phase-8/ci-cd` · 2026-11-18 · PR #5.

## 1. Goal

Every change should be checked automatically before it reaches `main`. The original plan also had GitHub
deploy to AWS after each merge. This AWS account (new sign-up experience, ADR 0002) blocks GitHub's secure
login method (OIDC), and long-lived AWS keys in GitHub are forbidden. So the owner chose **route A**: GitHub
runs all the checks, and deploys stay on the laptop (`make` targets with a 12 h `aws login`). It is free and
needs no change to the account.

## 2. What was built

| Piece | File | Why |
|---|---|---|
| CI workflow | `.github/workflows/ci.yml` | Five jobs on every pull request and every push to `main`. |
| Dependency updates | `.github/dependabot.yml` | Weekly update PRs for Python (uv), the MLflow image (pip), npm, GitHub Actions, Docker images and Terraform. |
| Branch protection | GitHub settings | `main` accepts changes only through a PR with all 5 checks green; no force-push, no deletion. |
| Image scan | `make image-scan` | Trivy scan of the built Lambda images; fails on vulnerabilities that already have a fix. |
| Badge | README | CI status of `main`. |

The five jobs:

| Job | What it checks |
|---|---|
| python | `uv.lock` matches `pyproject.toml`, ruff, mypy, 571+ unit tests with ≥ 80 % coverage, pre-commit hooks |
| frontend | ESLint/Prettier, TypeScript, Vitest, production build, `npm audit` (runtime deps, high+) |
| terraform | `fmt`, `validate` (both roots), tflint, Trivy misconfiguration scan |
| security | gitleaks over the whole git history, pip-audit on both Lambda images and the MLflow image |
| images | locked requirement files match `uv.lock`, both Lambda images build, Trivy image scan |

Safety of the workflow itself: it runs on `pull_request` (not `pull_request_target`), the token is read-only,
no secrets are used, every action is pinned to a commit SHA, the checkout token is not kept on disk, and
nothing in it can reach AWS.

## 3. What the new checks found (and what was fixed)

The first runs were the point: they found real problems.

- **Python packages with known vulnerabilities** in the Lambda images: `opentelemetry-api`, `certifi` → updated.
- **`vite`** (website build tool) with a high-severity advisory → `npm audit fix`; npm now reports 0.
- **61 high-severity OS package issues** in the Lambda base image pinned since October → newest base image plus
  `dnf upgrade --releasever=latest` during the build (Amazon Linux 2023 only upgrades within its release
  without that flag). Both images now pass the scan.
- **MLflow image** (local stack): outdated `aiohttp`, `certifi`, `gitpython`, `pillow`, `pyasn1`, `werkzeug`,
  `opentelemetry` → all updated. The `mlflow` 3.16.1 server itself has 3 advisories fixed in 3.17; it only
  runs on the laptop (127.0.0.1) and must be upgraded together with the training client, so CI ignores
  exactly those 3 IDs with the reason written next to them.
- **Terraform modules** did not declare their Terraform/provider versions (tflint) → `versions.tf` added.

All fixes were rolled out to AWS: 4 Lambdas updated, live smoke test 10/10.

## 4. Key decisions and why

- **Route A** (owner, 2026-11-18): free, no account change. Route B (Paid plan, irreversible) or C (new account)
  can still be chosen later; the workflow would then gain a deploy job.
- **Fail only on fixable vulnerabilities.** An unfixable finding cannot be acted on and would keep CI red.
- **Skip `aws-lambda-rie` in the image scan.** It is AWS's local emulator inside the base image and never
  runs on Lambda.
- **Owner keeps admin override** on `main` (`enforce_admins` off) for emergencies.

## 5. How it was tested

| Check | Result |
|---|---|
| Final CI run on PR #5 | 5/5 green: python 3m50s, images 3m11s, terraform 45s, frontend 34s, security 18s |
| gitleaks (local, full history) | 135 commits, no leaks |
| tflint | 0 issues |
| `make image-scan` (both images) | exit 0 |
| pip-audit Lambda images / MLflow image | clean / clean except the 3 documented mlflow IDs |
| AWS after the security rollout | api 388.6 MB, jobs 338.6 MB; `make smoke-prod-live` 10/10 |
| Code review (opus) | 0 Critical, 4 Important — all fixed |

## 6. How to use it

- Open a PR: the five checks run; merge only when green.
- Dependabot PRs arrive on Mondays. A Python update that touches a Lambda runtime library also needs the
  locked image files: check out the PR branch, `make api-requirements jobs-requirements`, commit, push.
- Deploy after merging (laptop): `make image-push` / `make jobs-image-push` → tags in the git-ignored tfvars →
  `make tf-plan` → `make tf-apply` → `make web-deploy` → `make smoke-prod-live` (README §10).

## 7. Known gaps

- No automatic deploys (route A by design).
- MLflow server advisories until the server + client upgrade.
- `make test-dags` and the integration tests need Docker and the local stack, so they stay local.

## 8. What's next

Phase 9 — Monitoring & continuous training: score yesterday's departures every night, track drift, publish a
health page, trigger retraining. Note from Phase 7: the historical HF data probably undercounts late trains,
which matters for retraining on live data.
