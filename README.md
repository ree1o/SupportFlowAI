# SupportFlow AI

Intelligent Customer Support & Incident Management Platform.

**Current implementation: M1 — Reproducible Application Baseline.** This is not yet a
working helpdesk. There is no authentication, ticket model, Redis, Celery, or AI code.
The next milestone requires approval before implementation.

## Documentation

- [Architecture and incident permissions](docs/architecture.md)
- [Release roadmap: 0.1 through 1.1](docs/roadmap.md)
- [Preliminary data model and ER diagram](docs/data-model.md)
- [Verification record and limitations](docs/verification.md)
- [Created-file inventory](docs/files.md)

Owner and Administrator will manage/approve incidents; Agent can view/report candidates.
Basic authorization and tenant isolation begin in M2. PostgreSQL RLS is deferred to 1.0.

## Implemented baseline

- FastAPI application factory and lifecycle management.
- Validated settings and secret-masked PostgreSQL configuration.
- Synchronous SQLAlchemy pool and independent sessions with rollback/close cleanup.
- Alembic baseline revision; no business tables and no automatic `create_all`.
- Database-independent liveness and migration-aware readiness.
- JSON application logs, server-generated request IDs, and safe API errors.
- Minimal React/TypeScript status page with retry and unavailable states.
- Exact Python dependency pins with hash locks and npm package lock.
- Local Docker Compose startup ordering and GitHub Actions validation.

## Windows: recommended Docker setup

1. Install Docker Desktop, enable the WSL 2 backend, and select **Linux containers**.
   Follow the [official WSL 2 instructions](https://docs.docker.com/desktop/features/wsl/).
2. Start Docker Desktop. In PowerShell from the repository root:

```powershell
docker version
docker compose version
Copy-Item .env.example .env
docker compose up --build -d --wait --wait-timeout 180
```

Do not overwrite an existing `.env`. The defaults are local demo credentials only.
The first build downloads images/packages and needs internet access. PostgreSQL uses
a persistent named volume. Compose waits for database health, runs Alembic once, and
starts the API only after migration success. No local Python/Node install is required.

Open:

- UI: <http://localhost:5173>
- OpenAPI documentation: <http://localhost:8000/api/docs>
- Liveness: <http://localhost:8000/api/health/live>
- Readiness: <http://localhost:8000/api/health/ready>

```powershell
docker compose ps
docker compose logs --tail 100 api migrate db
docker compose down
```

`down` preserves database data. `docker compose down --volumes` **deletes this project's
database volume**; use only when intentionally resetting disposable local data.
Changing POSTGRES_* credentials does not reinitialize an existing database volume.

All published ports bind to 127.0.0.1. The frontend uses Vite's development server and
proxies `/api` to FastAPI: no broad CORS policy is needed. This Compose file is for
local development, not a public production deployment. A production runtime, TLS,
secret rotation and least-privilege database credentials are later deployment work.

## Host development on Windows

Requires Python 3.12 and Node 22 (at least 22.12), plus PostgreSQL 16 or Docker.
Commands below avoid PowerShell activation scripts and `npm.ps1`, so no execution-policy
change is necessary. Run in the indicated directories.

From repository root:

```powershell
Copy-Item .env.example .env # Only if .env does not already exist
docker compose up -d db
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r backend\requirements-dev.txt
Set-Location backend
..\.venv\Scripts\alembic.exe upgrade head
..\.venv\Scripts\uvicorn.exe app.main:create_app --factory --reload --no-access-log
```

In a second terminal, from repository root:

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

The root `.env` URL uses `localhost`. Compose overrides it to the internal `db` hostname.
If using an existing PostgreSQL installation instead, create a dedicated development
database/account and edit `.env` accordingly. Never use an important database for tests.
Do not run the host API and Compose API on port 8000 simultaneously.

## Configuration

| Variable | Default / requirement |
| --- | --- |
| SUPPORTFLOW_DATABASE_URL | Required; `postgresql+psycopg://user:password@host:port/database` |
| SUPPORTFLOW_ENVIRONMENT | `development`, `test`, or `production` |
| SUPPORTFLOW_LOG_LEVEL | `INFO` |
| SUPPORTFLOW_DATABASE_POOL_SIZE | 5 |
| SUPPORTFLOW_DATABASE_MAX_OVERFLOW | 5 |
| SUPPORTFLOW_DATABASE_TIMEOUT_SECONDS | 3; connect, pool acquisition, and statement bounds |
| POSTGRES_USER / PASSWORD / DB | Compose local database initialization |
| API_PROXY_TARGET | Vite-only server configuration; defaults to `http://127.0.0.1:8000` |
| TEST_DATABASE_URL | Dedicated integration database ending in `_test` |

Environment variables override `.env`. URL-encode special characters in URL credentials.
Compose's interpolated local password should be URL-safe; do not paste unescaped reserved
characters into the generated URL. `.env` is ignored and never copied into images.

## Health and failure behavior

`GET /api/health/live` returns 200 `{ "status": "ok" }` without connecting to PostgreSQL.
`GET /api/health/ready` returns 200 `{ "status": "ready" }` only if PostgreSQL responds
and its Alembic revision matches this application. Otherwise it returns 503 with a stable
error code (`database_unavailable` or `schema_not_ready`) and request ID.
An unmigrated database is not healthy merely because `SELECT 1` works.

To demonstrate an outage in the disposable Compose environment:

```powershell
docker compose stop db
curl.exe -i http://localhost:8000/api/health/live
curl.exe -i http://localhost:8000/api/health/ready
docker compose start db
```

Expect 200 liveness, 503 readiness, then restored readiness after PostgreSQL restarts.
The UI can be refreshed with **Check again**. Application logs contain matched route
templates, not raw paths/query strings or exception messages. Uvicorn access logging is
disabled by the documented command to avoid unredacted URL logging.

## Tests and checks

From `backend`:

```powershell
..\.venv\Scripts\python.exe -m pip check
..\.venv\Scripts\ruff.exe check .
..\.venv\Scripts\ruff.exe format --check .
..\.venv\Scripts\pytest.exe
```

Without `TEST_DATABASE_URL`, PostgreSQL tests are explicitly skipped, not counted as
passed. To run the entire suite against a disposable Compose database, first create it
from the repository root (default local credentials):

```powershell
docker compose exec db createdb -U supportflow supportflow_test
$env:TEST_DATABASE_URL = 'postgresql+psycopg://supportflow:supportflow_dev_only@localhost:5432/supportflow_test'
Set-Location backend
..\.venv\Scripts\pytest.exe
```

Create the test database once; an already-existing error is harmless only if it is the
intended disposable database. Tests refuse a database whose name does not end `_test`
or which contains non-baseline user tables. The migration test downgrades/re-upgrades
that database. Do not run these tests concurrently against the same database.

Frontend, from `frontend`:

```powershell
npm.cmd ci
npm.cmd run build
```

Build includes strict TypeScript checking. There is no frontend unit-test framework
for this small status page; CI smoke-checks the frontend and same-origin API proxy.

Docker-only backend unit test alternative, from root:

```powershell
docker build --target test -t supportflow-backend-test ./backend
docker run --rm supportflow-backend-test
```

Integration tests skip in that standalone container unless an explicitly reachable
dedicated `TEST_DATABASE_URL` is provided. GitHub Actions provides PostgreSQL for all
backend tests, builds the frontend, and exercises Compose startup/outage recovery.
The workflow is committed configuration; a successful remote CI run requires pushing
this repository to GitHub.

## Dependency policy

Direct Python pins live in `backend/requirements*.in`; full hash-locked resolutions in
`requirements*.txt`. Frontend direct pins and transitive integrity hashes live in
`package.json` and `package-lock.json`. Container tags and CI runtime versions are pinned
to explicit releases; image tags are not immutable digests. Review upgrades deliberately.

To regenerate Python locks under Python 3.12 after an intentional dependency change:

```powershell
.\.venv\Scripts\python.exe -m pip install pip-tools==7.6.2
Set-Location backend
..\.venv\Scripts\pip-compile.exe --generate-hashes --output-file requirements.txt requirements.in
..\.venv\Scripts\pip-compile.exe --generate-hashes --output-file requirements-dev.txt requirements-dev.in
```

Run both Windows and Linux CI checks after regenerating. No secret-bearing custom
registry URL should enter a lockfile.

## Troubleshooting

- **docker not recognized:** install/start Docker Desktop and reopen the terminal.
- **Docker cannot connect:** check Docker Desktop is running in Linux-container mode.
- **npm.ps1 blocked:** use `npm.cmd`; no policy change is needed.
- **Port 5432 already in use:** stop the conflicting disposable service or change the
  Compose host port and host `.env` URL together; the internal database remains on 5432.
- **503 schema_not_ready:** run `alembic upgrade head` or rerun the Compose migration service.
- **503 database_unavailable:** inspect database startup, hostname/port and credentials.
- **UI unavailable:** check `docker compose logs frontend`; allow the server to start.

## Next: M2 (requires approval)

Users, organizations, Owner/Administrator/Agent memberships, secure server-side sessions,
password hashing, CSRF, and tenant-scoped authorization with isolation tests. RLS remains
scheduled for 1.0. Tickets begin in M3. No M2 functionality has been implemented.
