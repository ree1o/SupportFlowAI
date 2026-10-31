# Source inventory — M1 baseline and M2 identity

The workspace was empty. All source/configuration/documentation files below were created
for M1; no pre-existing application files were replaced.

M2 adds identity and tenant boundaries. The M1 list below is historical; the M2
additions and main modifications are listed separately at the end.

## Root and CI

- `.gitignore` — ignores local secrets, environments, caches, test databases and builds.
- `.gitattributes` — line-ending rules for Windows/Linux portability.
- `.env.example` — local configuration template.
- `README.md` — scope, setup, tests, troubleshooting and M2 approval boundary.
- `compose.yaml` — PostgreSQL, one-shot migration, API and frontend local services.
- `.github/workflows/ci.yml` — backend, frontend and Compose smoke checks.

## Documentation

- `docs/architecture.md` — decisions, diagrams, permission matrix and security progression.
- `docs/data-model.md` — preliminary future schema and ER diagram.
- `docs/roadmap.md` — independently demonstrable releases 0.1–1.1.
- `docs/verification.md` — observed checks, fixes and limitations.
- `docs/files.md` — this inventory.

## Backend

- `backend/Dockerfile`
- `backend/.dockerignore`
- `backend/pyproject.toml`
- `backend/requirements.in`
- `backend/requirements.txt`
- `backend/requirements-dev.in`
- `backend/requirements-dev.txt`
- `backend/alembic.ini`
- `backend/app/__init__.py`
- `backend/app/config.py`
- `backend/app/database.py`
- `backend/app/errors.py`
- `backend/app/health.py`
- `backend/app/logging.py`
- `backend/app/main.py`
- `backend/migrations/env.py`
- `backend/migrations/script.py.mako`
- `backend/migrations/versions/0001_baseline.py`
- `backend/tests/conftest.py`
- `backend/tests/test_api.py`
- `backend/tests/test_config.py`
- `backend/tests/test_postgres.py`
- `backend/tests/test_sessions.py`

## Frontend

- `frontend/Dockerfile`
- `frontend/.dockerignore`
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/tsconfig.json`
- `frontend/vite.config.ts`
- `frontend/index.html`
- `frontend/src/main.tsx`
- `frontend/src/styles.css`

Generated local artifacts (`.venv`, `.local`, `node_modules`, `dist`, caches) are ignored
and excluded from this source inventory. No credentials or model downloads are required
for the baseline beyond the documented local database configuration.

## M2 additions

- `backend/app/identity/__init__.py`
- `backend/app/identity/models.py` — users, organizations, memberships and session records.
- `backend/app/identity/schemas.py` — validated inputs and public response models.
- `backend/app/identity/security.py` — Argon2id, opaque tokens, cookie and CSRF primitives.
- `backend/app/identity/service.py` — transaction, session and tenant permission rules.
- `backend/app/identity/api.py` — authentication and scoped organization routes.
- `backend/migrations/versions/0002_identity.py` — additive M1-to-M2 schema migration.
- `backend/tests/test_identity.py` — PostgreSQL persistence, access and concurrency tests.
- `backend/tests/test_security.py` — cookie/configuration and security primitive tests.
- `frontend/src/api.ts` — same-origin requests and session-expiry handling.
- `frontend/src/Health.tsx` — preserved M1 health page at `/status`.
- `frontend/playwright.config.ts` — desktop and mobile browser projects.
- `frontend/e2e/identity.spec.ts` — full authentication and organization flows.
- `docs/identity.md` — API, security, permissions, limits and manual demonstration.
- `docs/m2-verification.md` — observed M2 verification results.

Main updated files: backend application/configuration/database/error handling, Alembic
model registration, PostgreSQL test fixtures, dependency inputs and hash locks,
frontend entry/layout/styles/package lock, Compose local cookie configuration,
`.env.example` (host port 15432), CI (mypy and browser checks), ignore rules, README,
architecture/data-model/roadmap documents and the milestone boundary in AGENTS.md.
Real `.env`, local database URLs, dependency folders and screenshots remain ignored.

## M5 additions

- `backend/app/sla/{models,schemas,service,api}.py` - organization policies, immutable
  versions, ticket target snapshots, deadline scans, escalation, and aggregate reports.
- `backend/migrations/versions/0005_sla_reporting.py` - additive SLA policy, version,
  and ticket snapshot schema with tenant constraints and scan indexes.
- `backend/tests/test_tickets.py` and `backend/tests/test_postgres.py` - SLA boundaries,
  policy authorization, snapshot stability, idempotent scanning, report definitions, and
  migration-head verification.
- `frontend/src/support/Operations.tsx` - SLA administration, operational metrics, and
  staff breach notifications.
- `docs/sla.md` - deadline semantics, metric definitions, escalation, and endpoints.

## M4 additions

- `backend/app/customers/{models,schemas,service,api}.py` - explicit customer grants,
  account token lifecycle, customer-only ticket operations, notifications and staff recovery APIs.
- `backend/app/jobs.py` - Celery worker, outbox relay, SMTP delivery and retry recovery.
- `backend/migrations/versions/0004_customer_portal_and_durable_jobs.py` - additive
  customer access, account, outbox, job, notification and customer-author schema changes.
- `backend/tests/test_customers.py` - customer privacy, tenant isolation, account lifecycle,
  outbox delivery, failure and worker-recovery integration scenarios.
- `frontend/src/customer/Portal.tsx` - customer login, invitation, recovery, requests,
  public conversation and notifications.
- `frontend/e2e/customer.spec.ts` - desktop/mobile customer-to-agent conversation workflow.
- `docs/customer-portal.md` - M4 API, security, processing and local demo guide.
- `compose.yaml` - Redis, Celery/Beat worker and Mailpit local SMTP.

## M3 additions

- `backend/app/tickets/{models,schemas,service,api}.py` — helpdesk domain, validation,
  scoped queries, explicit permissions, transaction/workflow/version rules and REST routes.
- `backend/migrations/versions/0003_tickets_add_ticket_management_and_history.py` —
  five tables, composite tenant FKs, constraints/indexes and append-only history triggers.
- `backend/tests/test_tickets.py` — real PostgreSQL API, tenant, rollback, workflow,
  concurrency, authorization-race and migration tests.
- `frontend/src/auth.tsx` — existing identity context extracted for shared navigation.
- `frontend/src/Sidebar.tsx` — shared staff navigation and organization switching.
- `frontend/src/support/{SupportApp,Tickets,Contacts,Categories}.tsx` — staff routes/screens.
- `frontend/src/support/{types,remote}.ts` — typed responses and scoped read lifecycle.
- `frontend/e2e/tickets.spec.ts` — desktop/mobile staff flow and draft-preserving conflicts.
- `docs/tickets.md`, `docs/m3-verification.md` — design, API, manual demo and evidence.

Updated: API composition/error details, identity organization-lock helper, Alembic model
registration, PostgreSQL migration expectations, frontend entry/API/styles, preserved M2
browser navigation expectations, CI browser-step description, README/design/roadmap and
the AGENTS.md milestone boundary. No dependencies or Compose services were added.

## M6 additions

- `backend/app/integrations/{models,schemas,service,api}.py` - organization-owned
  contact-bound sources, encrypted signing keys, HMAC verification and receipt history.
- `backend/migrations/versions/0006_inbound_integrations.py` - source/receipt tables,
  tenant foreign keys, actor provenance and event uniqueness.
- `backend/tests/test_integrations.py` - signed delivery, retries, concurrent duplicates,
  role/tenant checks, source lifecycle, notifications, SLA snapshots and public-only behavior.
- `frontend/src/support/Integrations.tsx` - source management and recent receipt interface.
- `frontend/e2e/integrations.spec.ts` - signed source-to-ticket browser journey on desktop/mobile.
- `docs/integrations.md` - signature protocol, payload contract and runnable Python sender.
- Updated ticket/data model, architecture, roadmap, README, Alembic registration and CI-covered tests.

## M7 additions

- `backend/app/incidents/{models,schemas,correlation,service,api}.py` - tenant-scoped
  incident lifecycle, deterministic lexical candidates, evidence, review, suppression,
  and background run services.
- `backend/migrations/versions/0007_incident_radar.py` - additive incident, activity,
  detection run, proposal, and suppression schema with tenant constraints.
- `backend/tests/test_incidents.py`, `test_incident_correlation.py`, and
  `fixtures/radar_pairs.json` - workflow/security tests and a small labeled evaluation set.
- `frontend/src/incidents/IncidentRadar.tsx` and `frontend/e2e/incidents.spec.ts` - staff
  Radar and incident workflow, including browser coverage.
- `docs/incident-radar.md` - lifecycle, permissions, detector, evaluation and demo guide.
- Updated app/worker wiring, settings, Compose values, navigation, styling, README,
  roadmap, architecture and PostgreSQL migration expectations.
