# SupportFlow AI

Intelligent Customer Support & Incident Management Platform.

**Current implementation: M9 - Release Hardening and Portfolio Evidence.**
A working staff helpdesk extends the verified identity and application baseline.
The staff helpdesk now supports customer self-service and recoverable asynchronous
notifications, SLA snapshots, deadline monitoring, staff breach alerts, operational reports,
and signed customer intake from explicitly contact-bound integration sources. It also
includes staff-reviewed incident proposals from bounded lexical correlation and separate
staff-only semantic ticket retrieval using a local multilingual embedding model. Neither
capability infers a root cause or requires a hosted AI provider.

## Documentation

- [Architecture and incident permissions](docs/architecture.md)
- [Release roadmap: 0.1 through 1.1](docs/roadmap.md)
- [Preliminary data model and ER diagram](docs/data-model.md)
- [Verification record and limitations](docs/verification.md)
- [Created-file inventory](docs/files.md)
- [Identity API, sessions, CSRF, permissions and browser demo](docs/identity.md)
- [M2 verification results](docs/m2-verification.md)
- [Ticket API, workflow, permissions, tenant constraints and concurrency](docs/tickets.md)
- [M3 verification results](docs/m3-verification.md)
- [Customer portal, account lifecycle, and background processing](docs/customer-portal.md)
- [SLA semantics, deadlines, metrics, and API](docs/sla.md)
- [Signed inbound webhook integration guide](docs/integrations.md)
- [Manual incidents and deterministic Incident Radar](docs/incident-radar.md)
- [Semantic retrieval, privacy and evaluation](docs/semantic-retrieval.md)
- [Security hardening, RLS, limits, and retention](docs/security-hardening.md)
- [Backup and recovery runbook](docs/backup-recovery.md)
- [Reproducible performance measurements and limitations](docs/performance-report.md)
- [M9 local verification record and limitations](docs/m9-verification.md)
- [Release checklist and demo walkthrough](docs/release-readiness.md)

Application authorization and explicit tenant filtering are reinforced with PostgreSQL RLS.

## Implemented baseline

- FastAPI application factory and lifecycle management.
- Validated settings and secret-masked PostgreSQL configuration.
- Synchronous SQLAlchemy pool and independent sessions with rollback/close cleanup.
- Additive Alembic migrations for identity, helpdesk, customer access, notifications and durable jobs; no automatic `create_all`.
- Database-independent liveness and migration-aware readiness.
- JSON application logs, server-generated request IDs, and safe API errors.
- React/TypeScript login, onboarding, protected workspace and team management, plus `/status`.
- Argon2id passwords, opaque hashed sessions, Origin/CSRF checks, explicit tenant RBAC.
- Organization-scoped contacts, categories, ticket queues, assignment and lifecycle rules.
- Separate public replies/internal notes, append-only audit history, atomic version conflicts.
- Customer verification, invitation, recovery, contact-bound access, ticket portal and notifications.
- PostgreSQL transactional outbox, Celery/Redis worker, local SMTP via Mailpit, retries and recovery.
- Responsive staff queue, intake/detail, contact management and category settings.
- Versioned organization SLA policies, immutable ticket target snapshots, UTC deadlines,
  retry-safe breach notifications, ticket SLA indicators, and operational reporting.
- HMAC-SHA256 inbound signatures, encrypted one-time credentials, contact-bound
  sources, atomic idempotent webhook receipts, and integration provenance in ticket history.
- Local CPU multilingual sentence embeddings, PostgreSQL pgvector exact search, durable
  tenant-scoped indexing, and a held-out lexical comparison for staff related-ticket lookup.
- Forced PostgreSQL RLS for tenant-owned tables, a restricted runtime role, transaction-local
  tenant context, targeted Redis rate limits, and bounded operational retention.
- PostgreSQL backup/restore runbook and a deployment readiness checklist.
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
The API and worker connect as the restricted `supportflow_runtime` role; the migration
service uses the configured local initialization account. Replace the disposable runtime
password before using anything outside a local demo.

Open:

- UI: <http://localhost:5173>
- Local email inbox (Mailpit): <http://localhost:8025>
- OpenAPI documentation: <http://localhost:8000/api/docs>
- Liveness: <http://localhost:8000/api/health/live>
- Readiness: <http://localhost:8000/api/health/ready>

```powershell
docker compose ps
docker compose logs --tail 100 api migrate db worker
docker compose down
```

`down` preserves database data. `docker compose down --volumes` **deletes this project's
database volume**; use only when intentionally resetting disposable local data.

## Demonstrate M5

Sign in as an organization Owner or Administrator, open **SLA & operations**, and set
the four priority response/resolution targets in minutes. Create a ticket and inspect its
policy version and deadline snapshot on the detail page. Agents can see the deadlines but
cannot edit the policy. Set a target to one minute to demonstrate a breach; the Celery
Beat worker scans once per minute, and the alert appears in Staff notifications after a
scan. The same page shows the current-month operational report. Exact metric cohorts and
late/reopen rules are in [docs/sla.md](docs/sla.md).
Changing POSTGRES_* credentials does not reinitialize an existing database volume.

## Demonstrate M6

Create a customer contact, open **Integrations**, and create a source bound to that
contact. Save its one-time credential and follow the [signed webhook guide](docs/integrations.md)
to create a ticket and append a public message. The source can only act for that contact;
receipts show event IDs and outcomes without retaining raw payloads. Rotate or revoke the
credential to verify immediate invalidation. Agents cannot manage signing keys.

## Demonstrate M7

Create at least two contacts and tickets describing the same specific symptom within
the configured window, plus one unrelated ticket. Open **Incident Radar**, run detection,
inspect terms/category/time evidence, remove any false-positive ticket, and confirm the
proposal. The incident timeline records review while linked ticket statuses and SLA
deadlines stay unchanged. Reject another proposal and rerun to see exact evidence
suppression. Full thresholds, evaluation, and role rules are in
[docs/incident-radar.md](docs/incident-radar.md).

## Demonstrate M8

Build the worker image, then explicitly prepare its model cache once:

```powershell
docker compose build worker
docker compose run --rm worker python -m app.semantic.setup_model
```

Create two tickets with equivalent meaning but different phrasing, such as “Cannot sign in
after resetting my password” and “Authentication fails after password change.” Open the
first ticket's Related tickets panel after the worker indexes it. Results are organization
and staff-visibility scoped; the score is similarity only, not a shared-root-cause claim.
Run the reproducible held-out comparison using the documented `supportflow_test` database
and local model cache instructions in [semantic-retrieval.md](docs/semantic-retrieval.md).
The model cache is local data and is not checked into Git.

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
$env:SUPPORTFLOW_COOKIE_SECURE = 'false' # Explicit local HTTP only
..\.venv\Scripts\uvicorn.exe app.main:create_app --factory --reload --no-access-log
```

In a second terminal, from repository root:

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

The root `.env` URL uses `127.0.0.1:15432`. Compose uses internal `db:5432`.
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
| SUPPORTFLOW_COOKIE_SECURE | `true` by default; Compose explicitly sets `false` for local HTTP |
| SUPPORTFLOW_ALLOWED_ORIGINS | JSON array of exact trusted origins; local UI origins by default |
| SUPPORTFLOW_FORWARDED_ALLOW_IPS | Uvicorn trusted proxy IPs/CIDRs; loopback only by default, never use `*` |
| SUPPORTFLOW_SESSION_TTL_SECONDS | 28800 (8 hours) |
| SUPPORTFLOW_SESSION_ABSOLUTE_SECONDS | 604800 (7 days); renewal cannot exceed this original limit |
| SUPPORTFLOW_REDIS_URL | `redis://redis:6379/0` in Compose |
| SUPPORTFLOW_SMTP_HOST / PORT | Mailpit service and SMTP port 1025 in Compose |
| SUPPORTFLOW_PUBLIC_APP_URL | `http://localhost:5173` for local account-action links |
| SUPPORTFLOW_EMAIL_FROM | Local sender identity captured by Mailpit |
| SUPPORTFLOW_TOKEN_ENCRYPTION_KEY | 32-byte Fernet key; use a unique secret outside local development |
| SUPPORTFLOW_SEMANTIC_MODEL_CACHE_DIR | Local model cache; Compose worker uses the named volume at `/var/cache/supportflow/models` |
| SUPPORTFLOW_SEMANTIC_INDEX_BATCH_SIZE | 50 tickets per manager backfill batch (bounded to 1-200) |
| SUPPORTFLOW_SEMANTIC_INDEX_MAX_ATTEMPTS | 5 retry attempts before visible failed state |
| SUPPORTFLOW_SEMANTIC_MIN_SIMILARITY | 0.35 cosine similarity display floor; a relevance signal, not a cause claim |
| SUPPORTFLOW_RUNTIME_DB_PASSWORD | Local-only non-owner API/worker role password; production secret-store value required |
| SUPPORTFLOW_RETENTION_BATCH_SIZE | 500 rows per table family and tenant cleanup batch (maximum 5,000) |
| SUPPORTFLOW_*_RETENTION_DAYS | Retention periods documented in [security hardening](docs/security-hardening.md) |

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
..\.venv\Scripts\python.exe -m mypy
..\.venv\Scripts\pytest.exe
```

Without `TEST_DATABASE_URL`, PostgreSQL tests are explicitly skipped, not counted as
passed. To run the entire suite against a disposable Compose database, first create it
from the repository root (default local credentials):

```powershell
docker compose exec db createdb -U supportflow supportflow_test
$env:TEST_DATABASE_URL = 'postgresql+psycopg://supportflow:supportflow_dev_only@127.0.0.1:15432/supportflow_test'
Set-Location backend
..\.venv\Scripts\pytest.exe
```

Create the test database once; an already-existing error is harmless only if it is the
intended disposable database. Tests refuse names not ending `_test` and use separate
random schemas per test; migration round-trips never touch the development schema.
On Windows, use `127.0.0.1` for the host-published PostgreSQL port so clients do not
wait on an unavailable IPv6 loopback address.

Frontend, from `frontend`:

```powershell
npm.cmd ci
npm.cmd run build
```

Build includes strict TypeScript checking. With the Compose stack running, browser
tests cover onboarding, login/logout, renewal, tenant switching, membership access,
ticket intake/assignment/workflow, public replies, internal notes, activity and two-tab conflicts:

```powershell
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

Use `$env:PLAYWRIGHT_CHANNEL = 'chrome'` to use an installed Chrome browser instead.
Desktop and mobile projects cover staff, customer, and signed integration workflows and
create synthetic accounts. Prefer a disposable test stack;
they intentionally leave those demo accounts for inspection. Never target a production URL.

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
- **Port 15432 already in use:** stop the conflicting disposable service or change the
  Compose host port and host `.env` URL together; the internal database remains on 5432.
- **503 schema_not_ready:** run `alembic upgrade head` or rerun the Compose migration service.
- **503 database_unavailable:** inspect database startup, hostname/port and credentials.
- **UI unavailable:** check `docker compose logs frontend`; allow the server to start.

## Try the staff helpdesk

1. Open `http://localhost:5173/register` and create a synthetic account/workspace, or sign in.
2. Open **Contacts**, create a customer contact, then create a category under **Categories**.
3. Open **Tickets → New ticket**, choose the requester, enter the issue and create it.
4. Assign the ticket, change priority/category, post a **Public reply**, then an **Internal note**.
5. Inspect both the conversation and activity. Resolve, close, and reopen as a manager.
6. Open the same ticket in two tabs. Change priority in the first, then post a draft in
   the second: expect 409, reload latest, review, and retry with the preserved draft.
7. Switch to a second workspace: the queue and contact directory are isolated.

Public replies are stored conversation records, not delivered messages. No customer
access exists in M3. Full permissions, transitions, API examples and limitations are
in [the ticket design](docs/tickets.md).

## Next milestone

M8 semantic search and embeddings require separate approval. M7 uses deterministic
lexical rules only; embeddings, vector storage, and generative AI are outside the
current implementation.
