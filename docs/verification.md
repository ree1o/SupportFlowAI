# M1 verification record

Verified locally on 2026-10-28. These are observed results, not planned CI results.

## Environment

- Windows; Python **3.12.4**, Node **22.21.1**, npm **10.9.4**.
- Disposable PostgreSQL **16.1** cluster under ignored `.local/pgdata`, loopback port
  55432, dedicated `supportflow_test` database. No existing user database was modified.
- Docker CLI/Desktop was not available on PATH. No container was built or run locally.
- Compose/CI pin Python 3.12.12 and PostgreSQL 16.10; the exact container combination
  remains to be executed by Docker-enabled CI or a developer with Docker Desktop.

## Observed results

| Check | Result |
| --- | --- |
| `pytest -q` with real PostgreSQL | **20 passed**, no warnings, no skips |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 14 Python files formatted |
| `python -m pip check` | No broken requirements |
| Alembic upgrade / current | `0001_baseline (head)` |
| Alembic downgrade / re-upgrade | Passed in integration test; readiness tracked schema state |
| `alembic check` | No new upgrade operations detected |
| Transaction rollback | Duplicate PK failure rolls back earlier writes and transactional scratch DDL |
| Session isolation/cleanup | Distinct connections; successful uncommitted work rolls back on close |
| `npm ci` | Clean lockfile install passed |
| `npm run build` | Strict TypeScript checking and Vite production build passed |
| npm install audit | 0 known vulnerabilities reported at verification time |
| Live HTTP smoke | API liveness, readiness, OpenAPI, frontend HTML, and frontend `/api` proxy returned 200 |
| Actual PostgreSQL outage | Liveness remained 200; readiness returned 503 `database_unavailable` |
| PostgreSQL recovery | Readiness returned 200 through the frontend proxy after restart |
| YAML parsing | Compose and GitHub Actions files parse successfully |
| Container tag registry lookup | All three pinned official tags exist with Linux amd64 images |
| Linux/Python 3.12 dependency resolution | Hash-verified binary-wheel dry run passed for the development lock |

Tests also cover required/invalid settings, secret masking, unavailable/unmigrated DB
responses, consistent 404/405/500 errors, server-generated request IDs, safe JSON request
logs, and engine disposal. Unit tests do not require a running database. Integration tests
explicitly skip when TEST_DATABASE_URL is absent and refuse non-test databases.

## Fixed during validation

- Corrected Node type definitions to satisfy Vite's peer dependency requirements.
- Formatted Python files to meet Ruff checks.
- Regenerated dependency locks with an explicit Python 3.12 interpreter after discovering
  different default Python versions in the normal/elevated shells.
- Switched test-only transport to pinned `httpx2`, as required by the installed Starlette
  version, removing the deprecated `httpx` fallback warning.
- Made the frontend container working directory writable by its non-root Node user.

## Verification limits and next actions

- **Docker build, Compose execution, and Linux runtime tests remain unverified locally.**
  YAML parsing and package resolution are not substitutes for running containers.
- CI has backend/PostgreSQL tests, frontend build, and Compose startup/outage/recovery
  jobs. The GitHub workflow has not run remotely; this initially empty workspace has no
  Git metadata or configured remote.
- HTTP checks verified frontend delivery/proxy, not automated browser interactions or
  screenshot-based visual QA. The page is deliberately small; no browser test framework
  was introduced in M1.
- Local tool patch versions differ from pinned container versions as listed above.
- This is a local baseline, not a reviewed public production deployment. Advanced RLS
  and production hardening are scheduled for 1.0; initial authorization starts in M2.

Temporary verification servers are stopped after testing. Ignored `.venv`, `.local`,
frontend `node_modules`, and build output remain available locally. No M2 work began.
