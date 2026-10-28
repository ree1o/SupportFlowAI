# M1 created-file inventory

The workspace was empty. All source/configuration/documentation files below were created
for M1; no pre-existing application files were replaced.

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
