# Security and tenant isolation (M9)

## Defense in depth

FastAPI services continue to authenticate users, verify active membership/customer
access, and pass an organization into domain operations. PostgreSQL row-level security
(RLS) provides a second boundary; it does not replace application authorization and
does not defend against arbitrary SQL executed using the runtime credentials because
tenant context is a PostgreSQL setting, not a secret.

`supportflow_runtime` is `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, does not
own application tables, and has DML plus sequence usage only. It has no schema
creation, DDL, `TRUNCATE`, or Alembic version-table write privileges; it has SELECT
only so database-aware readiness can compare migration heads. Migrations use a
separate administrative connection. Production must use a dedicated migration
principal and a distinct runtime password. Never use a database superuser in API or
worker processes.

`app.tenancy` sets UUID tenant/user/source/token-hash context with PostgreSQL
`set_config(..., true)`. Settings are transaction-local and cleared on commit/rollback,
so a pooled connection cannot carry context into a later request. Missing, malformed,
or empty context resolves to NULL and fails closed. Code must set context again after
committing before making tenant-owned queries. Only set tenant context after checking
membership or a specific signed-source/token capability.

## RLS coverage

These tables enable and force RLS. Tenant policies protect reads and writes using
`organization_id`; a few tables have additional narrow policies for global lookup
flows described below.

| Tables | Policy purpose |
| --- | --- |
| `contacts`, `categories`, `tickets`, `messages`, `audit_events` | Tenant-owned support data |
| `customer_access`, `account_tokens`, `customer_invitations` | Tenant customer data plus own-grant or exact token-hash lookup policies |
| `notifications` | Tenant boundary plus own notifications only while an active customer grant exists |
| `sla_policies`, `sla_policy_versions`, `ticket_slas` | SLA policy and snapshots |
| `integration_sources`, `webhook_receipts` | Tenant integrations; source-ID SELECT permits credential lookup before tenant context is known |
| `incidents`, `incident_tickets`, `incident_events`, `radar_runs`, `radar_proposals`, `radar_proposal_tickets`, `radar_suppressions` | Tenant incidents and evidence |
| `ticket_embeddings` | Tenant semantic index |

Global tables intentionally not RLS-scoped: `users` and `sessions` support global
identity; `organizations` and `memberships` are needed to discover/authorize tenant
membership; `outbox_events`, `background_jobs`, and `email_deliveries` are polled
across tenants by the shared worker; `alembic_version` is migration metadata.
Customer access and notifications also have narrowly scoped per-user policies; account
tokens require the matching user and organization context for writes. No raw
session, verification, recovery token, webhook secret, or signature is set as context.

The shared worker needs global outbox polling for Redis recovery. Before executing a
tenant-bound event, it sets that event's organization. Global delivery metadata remains
a capability of the shared runtime role; a dedicated delivery role is a possible
deployment hardening step. All business workflows remain tenant-scoped by application
services and foreign keys.

## Rate limits

Limits use atomic Redis `INCR` plus first-write `PEXPIRE`. Redis outage returns 503 on
the listed sensitive routes; ordinary ticket operations do not depend on Redis. Keys
are HMAC-SHA256 digests, not raw email or IP. Uvicorn's proxy middleware supplies the
client address only when the immediate peer matches `FORWARDED_ALLOW_IPS`; Compose
defaults that allowlist to loopback through `SUPPORTFLOW_FORWARDED_ALLOW_IPS`. Headers
from other peers are ignored. In production, set exact proxy IPs/CIDRs, restrict network
access so only those proxies can reach the API, and configure the proxy to replace
incoming forwarding headers. Never use `*` as the allowlist. 429 responses include
`Retry-After`.

| Operation | Per-key limit | Window |
| --- | ---: | ---: |
| Staff login | 10 | 15 minutes |
| Staff registration | 30 | 1 hour |
| Customer registration | 5 | 1 hour |
| Verification / recovery | 5–10 | 15 minutes to 1 hour |
| Invitation acceptance | 10 | 1 hour |
| Webhook delivery | 120 per source and caller IP | 1 minute |
| Related-ticket retrieval | 60 per organization and caller IP | 1 minute |
| Semantic backfill | 3 per organization and caller IP | 15 minutes |
| Manual Radar trigger | 5 per organization and caller IP | 15 minutes |

Test mode disables remote Redis access; direct tests exercise the atomic limiter
contract and errors. Production requires Redis. The current limiter is per API route
key and client IP, not a full gateway or global account lockout system.

## Retention

Celery Beat invokes a daily cleanup. Each record family is bounded to
`SUPPORTFLOW_RETENTION_BATCH_SIZE` (500 by default, maximum 5,000). Periods are
configurable with `SUPPORTFLOW_*_RETENTION_DAYS`. Calling
`app.retention.cleanup_transient_data(db, settings, dry_run=True)` returns candidate
counts and rolls back without deleting.

| Records | Default retention |
| --- | --- |
| Expired/revoked sessions | 30 days after expiration/revocation |
| Expired verification/recovery tokens | 30 days after expiry |
| Expired/accepted/revoked invitations | 90 days after terminal time |
| Webhook receipts | 365 days |
| Read notifications | 180 days; unread records are retained |
| Completed jobs and processed outbox events | 30 days |
| Old Radar runs with no proposals | 90 days |
| Obsolete stale embeddings | 30 days |
| Tickets, messages, audit history, incidents, proposal evidence, SLA history | Indefinite |
| Failed jobs and unprocessed outbox events | Retained until recovered/resolved |

No customer ticket content, ticket audit, incident notes/proposal evidence, or SLA
history is removed. Confirm legal and contractual requirements before applying to
real customer data.

## Runtime and secrets

Compose binds PostgreSQL to `127.0.0.1:15432` and web services to loopback. Do not
expose PostgreSQL/Redis publicly. API and worker use the runtime role; the migration
container uses the migration connection. Production secrets belong in a secret store,
not source, images, `.env`, or logs. Production requires HTTPS, secure cookies, stable
encryption keys, exact HTTPS origins, configured SMTP, persistent storage, and
explicit proxy topology. This portfolio project has no external penetration test or
security certification.

## Dependency verification

Python dependencies are pinned and hash-locked; CI installs them with
`--require-hashes` and runs `pip check`. Frontend dependencies are locked and CI uses
`npm ci`; the production build performs strict TypeScript checking. These checks detect
resolution and compatibility problems, not every known vulnerability. No automated
advisory-feed scanner is currently a release gate. Before a real deployment, review
current Python and npm advisories (for example, `pip-audit` and `npm audit`), validate
transitive findings, update lockfiles deliberately, and rerun the full verification
workflow. Advisory results change over time and are not represented by this static
document.
