# M2 verification record

Verified locally on 2026-10-28. M2 is the stopping point; M3 requires approval.

## Observed checks

| Check | Observed result |
| --- | --- |
| Complete backend suite on Windows / Python 3.12.4 | **63 passed**, no skips |
| Complete backend suite in Linux / Python 3.12.12 | **63 passed**, no skips |
| Ruff lint and format checks | Passed on Windows and Linux; 23 Python files |
| mypy | Passed on Windows and Linux; 13 application source files |
| Dependency consistency | `pip check` passed; hash-locked installs succeeded on both platforms |
| Frontend TypeScript and Vite production build | Passed |
| Clean npm lockfile install | Passed; registry audit reported 0 known vulnerabilities at installation |
| Desktop browser flows | Onboarding, login/logout, renewal, switching, membership disable and owner guard passed |
| Mobile browser flows | Same scenarios passed; no page-wide horizontal overflow |
| Screenshot inspection | Desktop/mobile workspace layouts inspected; screenshots remain ignored |
| Alembic | Fresh schema, baseline upgrade, downgrade/re-upgrade and preservation tests passed |
| Running Compose schema | `0002_identity (head)`; `alembic check` found no drift |
| Docker Compose | Configuration parses; API/frontend/test images built; stack starts healthy |
| Database port | Preserved `127.0.0.1:15432 → 5432`; containers use `db:5432` |
| Actual database outage/recovery | Liveness stayed 200; readiness and database-backed auth returned safe 503; readiness recovered to 200 |
| Secret/artifact review | Reviewed task changes, tested ignores, no real credentials or generated artifacts included |

The final Linux suite used the pinned backend test image with final application and
test source mounted read-only. The separately built running API image contains the
same final application code. Tests connect to a dedicated disposable PostgreSQL
database, with a random schema per test. Development data was not reset or dropped.
The browser suite has four cases: two scenarios each on desktop and mobile Chrome.

## Security and consistency evidence

- Normalized email uniqueness and database enforcement; signup rollback on collisions.
- Argon2id hashing, verification, login-time rehash and generic invalid-credential errors.
- Hashed session storage, secure-cookie configuration and token-format constraints.
- Missing/invalid/expired/absolutely-expired/revoked sessions and disabled accounts.
- Login/logout/rotation, absolute renewal bounds and preservation of an existing
  valid session after a wrong login attempt.
- CSRF/Origin checks on login, registration and every authenticated mutation.
- Organization creation/listing/selection and cross-tenant/nested-ID rejection.
- Explicit role matrix, self-escalation rejection, removal/disable/reactivation.
- Final-owner demotion/disable/removal protection and concurrent self-demotion race.
- Actor permission refresh after waiting for a tenant lock.
- An outsider query returns 404 without waiting on another tenant's organization lock.
- Composite selected-organization/user membership foreign key.
- M1 health, error, logging, migration, session cleanup and rollback regression tests.

## Fixes made during verification

- Corrected organization-name validator reuse after PostgreSQL API tests exposed a 500.
- Corrected the workspace-address accessible name found by the real browser tests.
- Kept failed-login errors distinct from expired-session errors, preserving valid sessions.
- Scoped organization queries to active membership before acquiring write locks.
- Excluded browser reports/screenshots from Git and Docker contexts.

## Limits

The expanded GitHub Actions workflow is configured but no remote CI outcome is claimed
by this local record. Local Chrome was used; CI installs Playwright Chromium.
Email ownership is not verified in M2. Membership enrollment targets existing accounts
under manager control. Invitations, recovery, MFA, rate limiting, session retention
cleanup and RLS remain later work, as detailed in [identity design](identity.md).
Synthetic browser accounts remain in the local development stack for inspection.
