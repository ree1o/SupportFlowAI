# M3 verification record

Verified locally across 2026-10-28–29. This record covers M3 only; M4 has not begun.
Results below were observed, rather than inferred from workflow configuration.

## Checks and results

| Check | Observed result |
| --- | --- |
| Complete Windows backend suite, Python 3.12 | **105 passed**, no skips; includes 63 M1/M2 regressions and 42 M3 cases |
| Complete Linux backend suite, Python 3.12.12 | **105 passed**, no skips, real PostgreSQL 16.10 |
| Dependency consistency | `python -m pip check` passed on Windows and Linux |
| Ruff lint and format | Passed on Windows and Linux, 30 Python files formatted |
| mypy application check | Passed on Windows and Linux, 18 source files |
| Frontend strict TypeScript + production build | `npm run build` passed on Windows and in the Linux frontend container |
| Source formatting | Prettier 3.6.2 applied/checked using the existing local tool cache; no dependency changes |
| Browser regression and M3 scenarios | **8 passed**, desktop Chrome and Pixel 7 emulation, against final Compose rebuild |
| Compose configuration and build/start | `docker compose config --quiet` and `up --build -d --wait --wait-timeout 180` passed |
| Running database schema | `0003_tickets (head)`; `alembic check` reports no new operations |
| Fresh install / rollback / re-upgrade | Passed in isolated disposable PostgreSQL schemas |
| M2-data upgrade preservation | Existing user/session/membership data survives M2-to-M3 upgrade; session remains usable |
| PostgreSQL health / ports | Accepting connections; existing `127.0.0.1:15432 → 5432` mapping preserved |
| API health / frontend proxy | Liveness and readiness return 200 through both ports 8000 and 5173 |
| OpenAPI composition | Exact expected 11 helpdesk paths and 17 HTTP operations verified |

The Linux suite used the existing test image's pinned tooling with the final `app`,
`tests` and `migrations` directories mounted read-only. Dependency locks and test
configuration did not change. Compose runtime images were rebuilt from current source.
The disposable test database is separate from the development database; each test
creates/cleans only its own generated schema. The existing development volume was
preserved. No destructive downgrade was run against the development schema.

## Persistence and security evidence

The M3 cases exercise:

- Contact create/read/update/search/archive/restore, validation and scoped authorization.
- Category normalization, PostgreSQL-generated uniqueness, roles and inactive history.
- Ticket intake, joined detail, bounded filters/search/sort/pagination and invalid inputs.
- Complete status transition matrix, lifecycle timestamps, no-op/stale/strict versions.
- Agent claim/release policy, management override and disabled membership behavior.
- Separate reply/note operations, public projection exclusion and ordered paging.
- Append-only API and PostgreSQL UPDATE/DELETE guards on messages and audit events.
- Multi-field audit grouping and safe metadata with no conversation body copies.
- Forced audit failures rolling back create, mutation, reply and note transactions.
- Independent PostgreSQL sessions synchronized before competing assignments/status
  changes: one success, one 409, one increment and matching committed audit.
- Membership changes under an organization lock while a request waits: demoted actor,
  disabled actor and disabled assignment target cannot use stale permissions.
- Preloaded contact/category state is refreshed under its row lock: concurrent archive
  or deactivation cannot be accepted using the old SQLAlchemy identity-map value.
- Unauthorized/unknown resource references, cross-tenant reads/writes and direct
  PostgreSQL composite FK rejection for requester/category/assignee/creator,
  message ticket/author and audit ticket/actor/message relationships.
- Audit message references cannot cross tickets even within one organization.
- Session/Origin/CSRF enforcement inherited from identity, with mutation-route coverage.
- Fresh-schema migration round trips and an M2-to-M3 upgrade preserving identity data.

The original M1/M2 tests still cover health/outages, sanitized errors/logs, session
cleanup, passwords/cookies, CSRF, session expiry/revocation/rotation, role escalation,
organization isolation and concurrent final-owner protection.

## Browser evidence

Each scenario runs in desktop and mobile projects:

1. Existing onboarding/login/logout, organization switching, renewal and owner protection.
2. Existing membership access, disabling and expired browser session behavior.
3. Create contact/category/ticket, assign, change priority/category, post public reply
   and internal note, inspect audit, resolve/close/reopen, filter the queue, switch
   organization and reject a wrong-tenant detail request. Public projection excludes
   the internal sentinel text. The page has no horizontal overflow at either viewport.
4. Two tabs read the same version. One changes priority; the other's reply returns
   actual HTTP 409. Draft stays intact; reload reflects the new priority and retry succeeds.

Desktop/mobile screenshots were visually inspected. Screenshots, browser output,
synthetic accounts and local test URLs remain ignored local artifacts. Browser checks
intentionally leave synthetic records in the local development stack for inspection.

An intermediate run encountered a browser fetch connection failure while refreshing
the M2 member list during concurrent Docker verification. The membership write returned
201 and no API 5xx was logged. Three isolated desktop repetitions passed afterward,
followed by the complete eight-scenario run. The connection failure was not reproduced;
its exact host/browser/proxy cause was not established. No retry or skip was added to
hide it. Browser verification should run after the Compose stack is stable.

## Reproduction

Use the [README Windows/Docker setup and test commands](../README.md#tests-and-checks).
Supply `TEST_DATABASE_URL` for a dedicated PostgreSQL database ending `_test`; otherwise
database tests skip and do not constitute full verification. Do not use a shared or
production database. With the local Compose application running, from `frontend`:

```powershell
$env:PLAYWRIGHT_CHANNEL = 'chrome' # Installed Chrome; omit for Playwright Chromium
npm.cmd run build
npm.cmd run test:e2e
```

The [ticket design](tickets.md) describes exact API paths, status transitions, role
policy, locking and manual demonstrations. The GitHub workflow executes the full
backend suite with PostgreSQL, frontend build and Compose/browser checks. A remote
Actions run is not claimed as locally observed evidence.

## Limits

All final required local checks passed. This is a
loopback HTTP development environment using synthetic data, not a public deployment.
No performance benchmark is claimed. Offset pagination can shift during new writes;
substring search and simple category/member lists target modest demo volumes.
Title/requester edits, deletions and message edits are intentionally absent. Public
replies are stored, not sent. Customer access, recovery/invitations, jobs, delivery,
SLA, webhooks, incidents, AI and advanced RLS are out of M3. The public-only projection
still requires staff authorization; M4 must implement its own customer-access policy.
Append-only triggers are not protection against a privileged database administrator.
M4 requires explicit approval.
