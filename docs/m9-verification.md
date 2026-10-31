# M9 verification record

Verified locally on 2026-10-31 in the existing Windows 10 / Docker Desktop environment.
All database integration tests used `supportflow_m8_test` through host port `15432`; its
name ends in `_test`, and each integration test migrated and cleaned its own random
schema. No database volume was reset.

## Results

| Check | Observed result |
| --- | --- |
| Complete backend suite | 164 passed, zero skipped (`pytest -q --tb=short`, 191.28 s) |
| Ruff lint | Passed |
| Ruff formatting | Passed; 79 Python files already formatted |
| Mypy | Passed; 51 application source files, no issues |
| Python dependency consistency | `pip check`: no broken requirements |
| Frontend TypeScript and production build | Passed; Vite production assets built |
| Playwright | 16 passed across desktop and mobile projects |
| Compose configuration/build/start | Passed; API, PostgreSQL, Redis, worker and Mailpit healthy; local PostgreSQL remains `15432:5432` |
| API live/readiness | Both returned 200 while dependencies were healthy |
| Redis | `PING` returned `PONG`; Celery `inspect ping` returned one worker node |
| Alembic schema check | `alembic check`: no new upgrade operations |
| Fresh migration and M8-to-M9 upgrade | Passed; additive upgrade preserved synthetic tickets, public messages and embeddings; current head `0009_release_hardening` |
| Runtime RLS/privilege tests | Passed using `supportflow_runtime`; role is not superuser, table owner, or `BYPASSRLS`; missing/foreign context and pooled-connection reuse were checked |
| Backup/restore | Synthetic source restored into a separate `_test` DB; integrity, extensions, migration head and runtime-role readiness passed |
| PostgreSQL outage/recovery | Liveness stayed 200, readiness returned 503, then readiness recovered to 200 |
| Redis outage/recovery | Readiness stayed 200; login returned 503 `rate_limit_unavailable`; Redis recovered and Celery reconnected |
| Worker restart | API readiness remained 200; worker logs reached ready and subsequent ping returned `pong` |
| Request-path performance | Measured on synthetic datasets; details and limitations in [performance report](performance-report.md) |

The PostgreSQL suite also exercised tenant-scoped registration and recovery tokens,
invitation acceptance, signed webhook processing and duplicate receipts using the actual
runtime database role. Frontend browser scenarios exercised the customer/staff message
boundary, tenant switching, integration workflow, incident review, stale ticket conflict
handling, keyboard form order and narrow viewport layout.

## CI and publication status

GitHub Actions must be updated with the final M9 commit before their result can be
reported. The release workflow retains backend/PostgreSQL, frontend, and Compose smoke
checks; no CI result is inferred from this local run. The final delivery report records
the resulting commit, remote branch and Action status.

## Limits

This verification is not an external penetration test, security certification, public
deployment, concurrent load test, production-capacity test, or measured recovery-time/
recovery-point objective. The local benchmark records CPU description and logical CPU
count but not RAM or database resource consumption. The restore rehearsal used a
synthetic organization with no tickets; a populated production recovery rehearsal is
out of scope.
