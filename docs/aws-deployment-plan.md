# Proposed AWS deployment plan (NOT approved, NOT deployed)

No AWS resource has been created, changed or queried by this project. Region `eu-west-1` is unverified; the existing
home lab has not been inspected. Before approval, a read-only inspection (account, region, existing VPC and IAM) is
needed. Prices below are rough orders of magnitude for a low-traffic demo, not quotes; check the AWS pricing pages.

## Smallest viable architecture

| Need | Service | Why / alternative |
|---|---|---|
| Static UI | S3 + CloudFront | Cheapest hosting for the built React app |
| Sign-in | Cognito user pool, groups = the four roles | Replaces demo auth. Backend validates the JWT (issuer, audience, expiry, signature) and maps `cognito:groups` to roles; pandal scope in a custom attribute or a DynamoDB table |
| API | API Gateway (HTTP API) + one Lambda running FastAPI via an adapter such as Mangum | Single function first; split only when needed. Alternative: one small Fargate task (steady cost) |
| Operational state | DynamoDB on-demand | Replace `repositories/store.py`. Keys: pandal, run, incident; GSIs for status and time |
| Evidence and archives | S3 (private, lifecycle rules) | Only after a privacy review |
| Buffering and routing | SQS, EventBridge | Add when a real video worker publishes measurements; not needed for simulation |
| Multi-stage workflow | Step Functions | **Defer.** The local runner is enough until retries or branching outgrow Lambda |
| Logs, metrics, alarms | CloudWatch | Implement `TelemetryExporter` with EMF metrics |
| Model key | Secrets Manager | Only when a real model is enabled |

## Things that must change before deploying

1. Replace demo auth with Cognito token validation (`CognitoVerifier` is a placeholder).
2. Implement DynamoDB repositories behind the existing interfaces; the simulation tick must move out of the
   in-process loop (EventBridge Scheduler or a Step Functions Map), since Lambda has no background thread.
3. Make dedupe writes conditional (DynamoDB conditional puts) so concurrent ticks cannot double-open incidents.
4. Serve over HTTPS only; restrict CORS to the CloudFront domain; add WAF rate limiting.
5. Move the token out of `localStorage` or accept the XSS trade-off knowingly.

## Cost expectation (demo scale, rough)

Mostly free-tier or cents per month for S3, CloudFront, Cognito (small user count), DynamoDB on-demand, Lambda
and API Gateway. CloudWatch logs and any NAT gateway are the usual surprises (avoid NAT). A Fargate task or
Step Functions state transitions at one run per pandal-minute would add noticeable cost; estimate before choosing.
Set an AWS Budget alert before deploying anything.

## Security posture

Least-privilege IAM roles per function; no wildcard resources; S3 block public access; encryption at rest
(DynamoDB, S3 defaults); no secrets in environment files or code; CloudTrail on; audit table append-only.

## IAM needed to deploy (to be reviewed)

Create/update for: S3, CloudFront, Cognito, API Gateway, Lambda, DynamoDB, IAM roles for those functions, CloudWatch.
Prefer a dedicated, permission-bounded deployer role, and infrastructure as code (CDK or SAM) so changes are reviewable.

## Rollback

Everything via IaC in a dedicated stack; rollback is stack deletion plus export of DynamoDB tables first. Keep the
local SQLite build as the fallback. DNS is not changed, so there is nothing to revert outside the stack.

## Approval gate

Nothing is deployed until you review this plan, confirm the region and account, and say so explicitly.
