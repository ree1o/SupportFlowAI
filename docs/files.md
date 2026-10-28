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
