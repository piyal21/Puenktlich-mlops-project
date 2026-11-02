# ADR-0002: AWS account type and its constraints

**Status:** Accepted (owner, 2026-10-31)
**Date:** 2026-10-31
**Deciders:** MD Piyal Ahmmed (owner)
**Evidence:** read-only AWS CLI checks (2026-10-30), the owner's `create-open-id-connect-provider` test,
the Phase 6 applies (2026-10-31), AWS docs "Sign up for AWS (new)" / "AWS Regions for your projects" /
"Service control policies for projects".

## Context

The online plane was designed for a classic AWS account in `eu-central-1` (Frankfurt), with root MFA,
an IAM Identity Center admin user and GitHub Actions deploying through OIDC (architecture §10–§12).

The owner's account was created with AWS's **new sign-up experience** on the **Free plan**:
resources live in a *project*, people sign in with an AWS Builder ID (no root user, no Identity
Center), and AWS manages the organization's service control policies (SCPs). What we measured:

| Fact | How we know |
|---|---|
| Region is fixed by the contact country: **`eu-north-1`** (Stockholm). `eu-central-1` and regional calls in `us-east-1` are denied by an AWS-managed SCP. | Read-only `lambda list-functions` per region |
| **GitHub OIDC is blocked**: `iam:ListOpenIDConnectProviders` and `iam:CreateOpenIDConnectProvider` → explicit deny by an AWS-managed SCP. | Read-only list call; owner's create call → AccessDenied, nothing created |
| CLI sign-in is `aws login --profile puenktlich` (12 h session, role `AccountFullAccessRole`). Terraform reads it; **boto3 needs `botocore[crt]`** for it. | Terraform read-only plan OK; boto3 `MissingDependencyException` |
| **Lambda memory is capped at 512 MB**; **concurrent executions = 5**. | `CreateFunction` → `MemorySize <= 512`; `get-account-settings` |
| **CloudFront needs account verification** by AWS Support before the first distribution. | `CreateDistribution` → 403 "Your account must be verified…" |
| Free plan: $100 credits, access stops on **2027-04-25** or when credits run out, unless upgraded. | Console |
| Changing region or SCPs requires "Activate advanced features", which needs an **irreversible** Paid-plan upgrade. | AWS docs |

## Decision

1. **Region `eu-north-1`** for everything regional (rules.md §8 updated, owner-approved 2026-10-31).
2. **Laptop deploys** for Phase 6: `aws login` + `make` targets (`tf-plan`/`tf-apply`, `image-push`,
   `upload-sample-days`, `publish-model`, `web-deploy`, `rollback-prod`). `infra/bootstrap` creates
   only the Terraform state bucket — no OIDC provider, no `gh-plan`/`gh-deploy` roles.
3. **No long-lived AWS keys anywhere**, including GitHub (rules.md §2 unchanged).
4. **Python CLIs** use a local profile `puenktlich-py` whose `credential_process` asks the AWS CLI for
   the `aws login` credentials (`make aws-py-profile`) — no new dependency.
5. **Lambdas at 512 MB**, no reserved concurrency; API Gateway throttling (10 rps / burst 20) and the
   60 s CloudFront board cache protect the 5-execution limit.
6. **Budget** `puenktlich-monthly` (created by hand, credits and refunds excluded) is imported into
   Terraform.
7. **Encryption and logging trade-offs** (Trivy findings accepted with reasons, `infra/.trivyignore` and
   inline `#trivy:ignore`): SSE-S3 / AES256 instead of customer-managed KMS keys (~$1/month each), no
   S3/CloudFront access logs (extra bucket and cost), unencrypted SNS alert topic (CloudWatch cannot
   publish to an `aws/sns`-encrypted topic), no WAF (not available here, ~$5+/month), default
   CloudFront certificate (no custom domain), no X-Ray (needs `xray:*` on `*`). Revisit in Phase 10.

## Options considered for GitHub → AWS (decided before Phase 8)

| Option | For | Against |
|---|---|---|
| A. Laptop-only deploys; CI runs checks only | Free, no account change | Portfolio loses automated CD |
| B. Paid plan + advanced features | Full control (OIDC, Frankfurt possible) | **Irreversible**; usage above credits is billed |
| C. New classic account ("Sign up for AWS (advanced)") | Matches the original design | Second account, other email; Terraform re-applies everything there |

## Consequences

- Docs and code say `eu-north-1`; latency from Germany to Stockholm is fine for this app.
- Deploys need the owner's 12 h `aws login`; there is no CD until the Phase 8 decision.
- The site goes offline on 2027-04-25 unless the plan is upgraded or the stack moves (option C is a
  re-apply, because everything is in Terraform).
- Cold starts are slower at 512 MB (measured: normal init 2.1–2.3 s; warm board 1.74–2.08 s; cold board 6.8–6.9 s). This misses the PRD targets at the origin (warm p95 < 300 ms, cold < 6 s); CloudFront cache hits (0.07–0.22 s) meet them. **Open owner decision:** accept for this account, or optimise scoring.
