# M10 implementation checkpoint

Updated: 2026-11-25 (Asia/Qyzylorda)

## Current status

M10.1 was first committed in `181a6b9f53720ec406e3ae3077935a51558777e4`. M10.2–M10.6
were completed and released in `5fae663f1dc18f82c7b27b0c1d5beeee6e1adb4a` on `main`.
The normal push to `origin` succeeded, and the remote `main` tip was verified to match.
GitHub Actions run [45646613287](https://github.com/ree1o/SupportFlowAI/actions/runs/45646613287)
passed backend, frontend, and compose-smoke. No M11 work has started.

The full M10 code is implemented and local verification passed, with the important
qualification that the synthetic generation quality targets below were not met. Do not
present the Copilot as production-ready or its answers as reliably grounded. It is opt-in,
experimental, and human-review-only. It remains opt-in and human-reviewed.

## Implemented phases

- **M10.1 — Local provider:** optional native Windows Ollama provider, fixed local endpoint
  allowlist, authenticated availability status, bounded request settings, no cloud fallback.
  Prior commit: `181a6b9f53720ec406e3ae3077935a51558777e4`.
- **M10.2 — Knowledge Base:** organization-scoped articles and immutable versions, draft /
  published / archived lifecycle, current-published-only retrieval, deterministic chunking,
  pattern-based PII minimization, versioned local Qwen embedding vectors, transactional
  outbox indexing, AI worker queue, retries and visible index status.
- **M10.3 — Grounded Copilot:** durable `ai_runs`, idempotent requests, asynchronous
  generation, public-message-only ticket context, cited reply draft, conversation summary,
  source validation and abstention. No generated response is sent automatically.
- **M10.4 — Staff UI:** Knowledge Base navigation and article lifecycle UI; ticket-level
  Copilot panel, status/error/loading states, source citations, summary, and explicit insert
  into the ordinary public-reply draft editor.
- **M10.5 — Bounded tools:** custom typed orchestration with only `search_knowledge_base`
  and `get_sla_status`; read-only, run-bound organization/ticket scope, four-turn maximum,
  unique tool calls, bounded inputs/results, server-side authorization rechecks.
- **M10.6 — Verification/docs:** synthetic multilingual evaluation, opt-in real local-model
  smoke, RLS tests, AI-result retention, docs, and full browser/regression run.

## Decisions and data boundaries

- Native Windows Ollama; primary model `qwen3.5:4b-q4_K_M`, digest
  `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`, 3,324,173,934-byte
  artifact. The existing `qwen3.5:2b-q4_K_M` is the documented, not auto-downloaded fallback.
- M8 ticket embeddings remain MiniLM 384D and separate. Knowledge Base uses a versioned
  `Qwen/Qwen3-Embedding-0.6B-Q` 1024D namespace. No model vectors are mixed. FastEmbed
  0.8.1 / ONNX Runtime 1.32.0 are pinned. The existing immutable model artifacts/caches are
  external to Git and were reused; no model was downloaded during this continuation.
- AI remains disabled by default. Ollama is optional and outside API health/readiness gates.
  Inference runs on the dedicated single-concurrency `ai` Celery worker. There is no paid
  API, cloud fallback, or external transmission of ticket/article data.
- Generation receives ticket title/status/priority/category and up to 12 bounded public
  messages. Reply generation additionally receives at most four current published chunks.
  It excludes internal notes, contact profile data, staff audit events, incident notes,
  other customers' ticket bodies, secrets, credentials, and webhooks.
- `ai_runs` is tenant-scoped with forced RLS, composite references, and restricted runtime
  grants. Jobs recheck requester membership, ticket visibility/version, and source revision.
  Result text/citations are redacted after the configured retention window (30 days default).
- Asynchronous running-job cancellation is not implemented; a running job returns a safe
  conflict. Queued cancellation is supported.

## Model observations

On the documented Windows 11 host (Core i5-12500H, 16 threads, 15.68 GiB RAM, RTX 3060
Laptop GPU with 6 GiB dedicated VRAM), the initial six-token Ollama smoke had 68.8 s cold
start and 0.843 s warm response, about 12.1 generated tokens/s. This is a short smoke, not a
reliable throughput benchmark. The model occupied 3,770 MiB dedicated VRAM after the probe.
Details and caveats are in `docs/local-ai.md`.

## Copilot evaluation

`backend/tests/fixtures/copilot_eval.json` contains 36 synthetic scenarios (12 development,
24 held out), English/Russian/Kazakh, and 10 published article examples. The final run used
exact cosine on Qwen KB embeddings, production top four and relevance >= 0.30, with the same
24 held-out cases and the local Qwen 4B model. Labels were not passed into prompts. See
`docs/ai-evaluation.md` for methodology, per-language breakdown, and limits.

Final recorded held-out results:

- Retrieval recall@4: **15/15 answerable cases (1.000)**; MRR@4 **0.844**.
- Answerable cases with at least one expected relevant citation: **5/15 (0.333)**.
- Correct abstentions for insufficient-evidence cases: **8/9 (0.889)**.
- Citation identifiers resolved to supplied references: **24/24 (1.000)**.
- Structured outputs accepted: **24/24 (1.000)**.
- End-to-end mean latency **9.57 s**, p95 **15.36 s** in this warm local run.
- Separate 12-case development run: retrieval recall@4 **9/9**, MRR@4 **0.944**, relevant
  citations **8/9**, correct abstentions **3/3**, and valid references **12/12**; mean
  latency **8.18 s**, p95 **14.93 s**. The gap from held-out results shows substantial
  fixture sensitivity; no prompt tuning was performed after either measurement.

Proposed citation-recall (>=0.80) and abstention (>=0.90) targets were not met. No human
answer-quality review was performed. Do not tune against this held-out set or claim quality
improvement from another run over the same cases. Any tuning requires a newly frozen held-out
set. Security/tenant tests and citation-ID validity are separate from semantic grounding.

## Verification completed

- Dedicated disposable PostgreSQL test database: `supportflow_m10_20261124_test`; connection
  verified. Full backend suite after final code changes: **201 passed, 1 intentional skip**
  (the opt-in live local-model test is skipped in standard CI to avoid requiring Ollama).
  That opt-in test was then run separately with the real local model and synthetic DB fixture:
  **1 passed in 18.63 s**.
- The suite includes fresh-schema migrations, M8-to-M10 upgrade/downgrade preservation,
  RLS/tenant tests as `supportflow_runtime`, AI run constraints, tool authorization, stale
  versions, citations, retention and existing M1–M9 regression coverage.
- Ruff: check passed; format check passed (107 files). Mypy passed for 68 application source
  files. `pip check` passed.
- `pip-compile --dry-run` lock regeneration could not reach the configured Python package
  index (`WinError 10013`, outbound network denied); no dependency files were modified by
  that attempt. The installed environment reports no broken requirements, and the Compose
  image build uses the pinned hash-checked requirements file. Treat lock regeneration as
  unverified until run from a network-enabled environment.
- Frontend `npm run build` passed, including TypeScript. Playwright desktop/mobile:
  **20 passed**.
- Initial browser attempt had 13 failures caused by legitimate HTTP 429 registration limits
  after repeated tests shared Redis DB 0. No limits were disabled and no keys were deleted.
  Rerunning against a fresh Redis logical DB 1 passed 20/20. The regular Compose config was
  then restored, so API and workers use Redis DB 0 again.
- Compose rebuilt successfully with no volume removal. API liveness `ok`, readiness `ready`;
  PostgreSQL 16 and pgvector 0.8.1 healthy; migration `0011_ai_runs`; Redis, API, frontend,
  worker, AI worker and Mailpit started/healthy. Host PostgreSQL mapping remains
  `127.0.0.1:15432 -> 5432`. `alembic check`: no new upgrade operations. Four published KB
  chunks reached `ready` from the browser-created synthetic articles.
- The local AI evaluation itself ran with the existing model caches and native Ollama; no
  customer data was used. `docker compose config --quiet` passed.
- At that verification checkpoint, M10 had not yet been published. Final commit, push, and
  GitHub Actions results are recorded below.

## Modified/untracked M10 scope

Inspect `git status --short --branch` for the authoritative path list. Changes are confined
to M10: configuration/Compose, `backend/app/ai/`, `backend/app/knowledge/`, semantic model
version compatibility, Celery/outbox and retention wiring, migrations `0010_knowledge_base`
and `0011_ai_runs`, tests/fixtures/evaluation tooling, Knowledge Base/Copilot React UI,
README, architecture/data-model/roadmap/local-AI/semantic docs, and this progress document.
Frontend Playwright artifacts and model caches are ignored generated/local files, not part
of the intended commit. No unrelated changes are known.

## Original publication checklist (superseded by the continuation below)

1. Inspect the actual full diff, including all untracked source, and validate docs links /
   formatting. Lock regeneration needs a network-enabled environment; `pip check` passed.
2. Confirm `.env`, model weights, cache, `frontend/test-results`, generated build outputs,
   and any test artifacts are excluded; run secret scan without printing values.
3. Recheck `git status`, current branch, staged state, `origin`, and remote `main` tip.
4. Stage only the M10 paths, inspect cached diff/stat/check and secrets, then commit with a
   Conventional Commit describing the technical functionality (no AI/model/tool attribution).
5. Normal-push to `origin/main`; verify the remote commit hash matches local HEAD.
6. Monitor the resulting GitHub Actions run until backend, frontend and compose-smoke finish.
   Investigate failures; never force-push or rewrite history.
7. After Actions results, update this checkpoint/README only if the record needs correction;
   if a post-push edit is needed, commit and push it normally and verify the new run.
8. Report M10 and stop. Do not start any future milestone without approval.

## Saved M10 verification checkpoint — 2026-11-25

This checkpoint records M10 after local implementation and verification resumed, before
publication. No M11 work has started.

### Repository and Git state

- Branch: `main`; HEAD and latest verified commit:
  `181a6b9f53720ec406e3ae3077935a51558777e4` (`feat(backend): add optional local
  generation provider`). The configured remote is `origin`
  (`https://github.com/ree1o/SupportFlowAI.git`). At the last remote check,
  `origin/main` matched this commit. No M10.2–M10.6 commit exists.
- M10 implementation is preserved locally. The index contains 63 M10 paths from the
  earlier explicit staging operation. Subsequent rate-limit edits are unstaged; the
  status showed `MM` for `backend/app/ai/api.py` and `docs/ai-copilot.md`, and unstaged
  changes for `backend/app/rate_limit.py` and `backend/tests/test_rate_limit.py`.
  This checkpoint document itself has staged M10 content plus this unstaged checkpoint
  addition. Inspect `git status --short --branch` and both staged/unstaged diffs before
  resuming; do not reset or discard either set.
- No commit or push has occurred for the uncommitted M10 work. Do not commit until the
  final review/checks are complete.

Exact status paths captured at this checkpoint (the first column pair follows `git
status --short`; `MM` means both index and working tree differ):

```text
M  .env.example
M  README.md
MM backend/app/ai/api.py
A  backend/app/ai/evaluation.py
A  backend/app/ai/models.py
M  backend/app/ai/provider.py
M  backend/app/ai/schemas.py
A  backend/app/ai/service.py
A  backend/app/ai/tools.py
M  backend/app/config.py
M  backend/app/customers/models.py
M  backend/app/jobs.py
A  backend/app/knowledge/__init__.py
A  backend/app/knowledge/api.py
A  backend/app/knowledge/chunking.py
A  backend/app/knowledge/embedding.py
A  backend/app/knowledge/models.py
A  backend/app/knowledge/schemas.py
A  backend/app/knowledge/service.py
A  backend/app/knowledge/setup_model.py
A  backend/app/knowledge/worker.py
M  backend/app/main.py
 M backend/app/rate_limit.py
M  backend/app/retention.py
M  backend/app/semantic/models.py
M  backend/app/semantic/service.py
M  backend/migrations/env.py
A  backend/migrations/versions/0010_knowledge_base.py
A  backend/migrations/versions/0011_ai_runs.py
M  backend/requirements-dev.txt
M  backend/requirements.in
M  backend/requirements.txt
M  backend/tests/conftest.py
A  backend/tests/fixtures/copilot_eval.json
A  backend/tests/fixtures/knowledge_retrieval.json
A  backend/tests/fixtures/knowledge_retrieval.py
A  backend/tests/test_ai_evaluation.py
A  backend/tests/test_ai_live_model.py
M  backend/tests/test_ai_provider.py
A  backend/tests/test_ai_runs.py
A  backend/tests/test_knowledge.py
A  backend/tests/test_knowledge_embedding.py
M  backend/tests/test_postgres.py
 M backend/tests/test_rate_limit.py
M  backend/tests/test_semantic.py
A  backend/tools/evaluate_knowledge_retrieval.py
M  compose.yaml
AM docs/ai-copilot.md
A  docs/ai-evaluation.md
M  docs/architecture.md
M  docs/data-model.md
M  docs/local-ai.md
MM docs/m10-implementation.md
M  docs/release-readiness.md
M  docs/roadmap.md
M  docs/semantic-retrieval.md
A  frontend/e2e/copilot.spec.ts
A  frontend/e2e/knowledge.spec.ts
M  frontend/src/Sidebar.tsx
A  frontend/src/ai/CopilotPanel.tsx
M  frontend/src/api.ts
A  frontend/src/knowledge/KnowledgeBase.tsx
M  frontend/src/styles.css
M  frontend/src/support/SupportApp.tsx
M  frontend/src/support/Tickets.tsx
```

### Work and verification at pause

- M10.1 is committed; M10.2–M10.6 implementation and documentation are in the working
  tree. The final review found and added global (120/hour) and organization (30/hour)
  Redis-backed AI generation caps alongside the existing client/organization cap
  (6/hour). The new rate-limit scope unit test passed in the focused run.
- Full PostgreSQL-backed suite on the final code: **201 passed, 1 intentional skip**.
  The single skip is the opt-in live-model test; that test separately passed against the
  cached local model and synthetic test database (**1 passed in 18.63 seconds**). No
  database-dependent tests were skipped in the full run.
- Desktop/mobile Playwright suite: **20 passed**. It was run using an isolated Redis
  logical database because prior legitimate registration limits had accumulated in the
  shared test Redis DB 0. No rate-limit settings were weakened and no keys were deleted.
- Previously recorded checks: Ruff and formatting passed; mypy passed for 68 application
  files; `pip check` passed; frontend production build passed; `alembic check` reported no
  new operations; Compose rebuild/startup and PostgreSQL, pgvector, Redis, API, frontend,
  Celery worker, AI worker, and Mailpit health checks passed. API liveness/readiness were
  healthy, migration head was `0011_ai_runs`, and four synthetic KB chunks reached ready.
- The earlier `pip-compile` failure (`WinError 10013`) came from the restricted command
  environment denying outbound access. Retrying with network-enabled execution succeeded;
  `pip index versions --index-url https://pypi.org/simple pydantic` returned package
  metadata, and `pip-compile --dry-run --no-upgrade --generate-hashes requirements.in`
  completed with “Dry-run, so nothing updated.” The lock is unchanged and `pip check`
  reports no broken requirements.
- Held-out synthetic Copilot answer-quality acceptance targets were not met. Results and
  limits are recorded above and in `docs/ai-evaluation.md`; keep the feature described as
  experimental and human-reviewed.
- The Compose stack was gracefully stopped with `docker compose stop --timeout 30`.
  All seven project services plus the migration container reported stopped. No container
  or volume was removed, and no database reset or model/cache deletion occurred. The
  earlier Compose command's process/session was no longer available to poll; the resulting
  service state was directly inspected before stopping. A follow-up `docker compose ps`
  returned no running containers. A temporary E2E override at
  `%TEMP%\supportflow-m10-e2e2-compose.yaml` was used earlier; it is outside the repository
  and has not been removed.

### Verification continuation and remaining publication steps

After the checkpoint snapshot above, verification was rerun:

- Full PostgreSQL-backed backend suite: **201 passed, 1 intentional opt-in skip** in
  **321.07 seconds**. The live-model smoke was separately enabled and passed: **1 passed
  in 37.85 seconds**.
- Ruff check passed; Ruff format check passed (**107 files**); mypy passed (**68 source
  files**); `pip check` passed; frontend production build/typecheck passed.
- Docker Compose rebuilt and started all services successfully. API liveness returned
  `ok`, readiness returned `ready`, the database/Redis/workers/frontend/Mailpit were
  healthy, and `alembic check` reported no new upgrade operations. The local PostgreSQL
  port remained `15432:5432`.
- Playwright desktop/mobile suite passed **20/20** using the disposable `_test` database
  and isolated Redis logical DB 3. The normal Compose configuration was restored afterward
  and now points back to the development database and Redis DB 0. The temporary override
  created for this verification was removed; the earlier `%TEMP%\supportflow-m10-e2e2-compose.yaml`
  file was preserved.
- Direct package-index metadata access and the dry-run lock consistency check both passed.
- `git diff --check` and `git diff --cached --check` passed. A high-confidence scan of
  staged and unstaged added lines found no private-key headers or common provider tokens.
  `gitleaks`, `trufflehog`, and GitHub CLI are not installed. `.env`, virtualenv,
  `node_modules`, `frontend/dist`, and Playwright result artifacts are ignored.
- M10 commit `5fae663f1dc18f82c7b27b0c1d5beeee6e1adb4a` was pushed to `origin/main`; the
  public ref was verified equal to local HEAD. GitHub Actions run [45646613287](https://github.com/ree1o/SupportFlowAI/actions/runs/45646613287)
  completed successfully: [backend](https://github.com/ree1o/SupportFlowAI/actions/runs/45646613287/job/139319206196),
  [frontend](https://github.com/ree1o/SupportFlowAI/actions/runs/45646613287/job/139319205689),
  and [compose-smoke](https://github.com/ree1o/SupportFlowAI/actions/runs/45646613287/job/139319205952).

M10 release verification and publication are complete. The only known limits are the missed
synthetic answer-grounding/abstention targets documented above and that `gitleaks`,
`trufflehog`, and GitHub CLI were unavailable locally; a high-confidence staged diff scan,
CI, and all required local checks passed. Do not start M11 without explicit approval.

## Runtime corrective maintenance — AI run transaction under forced RLS

On 2026-11-25, a live grounded-reply run exposed a transaction-ordering defect in the
restricted PostgreSQL runtime role. After a successful embedding and model response,
`process_run` dirtied its identity-mapped `AIRun` by assigning the model digest after a
commit. `_finish_run` then executed transaction-local tenant setup. SQLAlchemy autoflush
ran the pending update before the RLS tenant setting had been established, so forced RLS
matched zero rows and raised `StaleDataError`. The generic error handler attempted another
query in the invalidated transaction and raised `PendingRollbackError`; consequently the
run did not immediately reach a terminal state.

The correction establishes tenant GUCs inside `Session.no_autoflush`, removes the
unnecessary pre-completion ORM mutation, and makes each AI run transition helper begin
from a rolled-back, clean transaction and reload the locked run with
`populate_existing=True`. Completion, stale, provider-failure, and generic-failure paths
now guard against overwriting a run that a concurrent recovery or cancellation has already
transitioned. Generic failures roll back before recording `generation_failed`; this does
not suppress the original error or bypass RLS.

Regression coverage runs the grounded completion and a failed-completion recovery with
the actual `supportflow_runtime` database role and forced `ai_runs` RLS in isolated
PostgreSQL migration schemas. Another PostgreSQL test verifies an exhausted interrupted
run becomes terminal as `worker_interrupted`. The frontend Playwright scenario advances
the polling timer deterministically and verifies polling stops after the API reports a
terminal failure.

Verification on the local Windows/Docker Desktop setup:

- Complete backend suite against the new dedicated disposable database
  `supportflow_m10fix_20261125_test`: **204 passed, 1 intentional opt-in skip**. No
  PostgreSQL integration tests were skipped. The skip is the separately enabled live-model
  smoke test; all three live AI actions were instead exercised through the running Docker
  API and AI worker using the already-installed local Ollama model.
- Local Compose API, database, Redis, regular worker, AI worker, frontend, and Mailpit
  rebuilt or remained healthy; API readiness was `ready`. AI was enabled and Ollama
  reported the configured model available. No volume was removed.
- A synthetic workspace, ticket, and published knowledge article were created through the
  API. The article indexed successfully. Reply, summary, and agentic generation each
  reached `completed`; observed latencies were 16,511 ms, 3,369 ms, and 11,259 ms. The
  reply returned one verified citation; the bounded agent used only
  `search_knowledge_base` and returned one citation. These are single-run functional
  checks, not benchmark claims.
- The existing database contained one old reply run which the recovery scheduler had
  already terminalized as `failed / worker_interrupted` after its configured attempt
  limit. There are no queued or running AI runs. The existing recovery task was invoked
  under application configuration and found zero additional stale runs to redispatch.
  No run, ticket, article, or other user data was deleted or manually rewritten.
- Frontend production build passed. The full desktop/mobile Playwright suite passed
  (**20/20**), including terminal-success and terminal-failure polling cessation. The first
  full-suite attempt had 7 passes and 13 failures because earlier browser runs had consumed
  the shared Redis DB 0 registration limits; each failed setup displayed the expected
  “Too many requests” response. The suite passed on an isolated Redis logical DB 3 without
  changing limiter settings or deleting keys. The standard Compose configuration was
  restored to DB 0 afterward.

The standard Compose configuration remains running with AI enabled for continued manual
testing. The only remaining opt-in skip is intentional; it avoids requiring Ollama in
standard backend CI. The real local-model paths were verified separately in the running
Compose application.

## Final release polish continuation — checkpoint before Docker recovery — 2026-11-26

This is a follow-up polish pass over the completed M10 implementation, not a new milestone.
At this checkpoint the changes below were present locally but still awaited PostgreSQL,
Docker and browser verification. The verification record that supersedes the checkpoint is
at the end of this document.

### Implemented in the current working tree

- Conversation summarization now uses a separate structured summary response and public
  conversation only. It no longer requires a Knowledge Base citation or inherits reply
  abstention behavior. A regression test asserts internal notes are excluded.
- Agentic analysis now supports typed ticket, SLA, related-ticket, and Knowledge Base
  assessments, with four explicit read-only tools and bounded distinct calls. Read-only tool
  database transactions close before another local inference request begins.
- AI quotas are reserved atomically across configurable global, organization, and
  staff-account/organization scopes, after authentication, authorization, ticket-version,
  idempotency, and active-run checks. Duplicate requests do not spend quota, failed
  persistence refunds the reservation, and the API reports effective remaining quota. A
  new unit test caught and corrected a retry-after TTL slot parsing error in denied quota
  reservations.
- The ticket UI disables new generation while a run is queued/running, only offers
  cancellation while queued, displays structured agentic findings and the effective quota,
  retries transient status polling errors, and uses a safe generic failure message.
- The customer-only/no-staff-account route redirects from staff pages to the customer portal.
- The System Status page reports API, database/migration, Redis, worker, embedding-model,
  and optional local-generation status. The API receives the shared model cache read-only
  so its model-file diagnostic checks the same persistent artifacts as the workers.
- Knowledge Base articles now have an escaped Markdown preview/renderer. It renders no raw
  HTML and permits only HTTP(S) links. The editor clarifies draft and published revision
  numbers.

### Verification performed for this continuation

- Backend non-integration checks: **61 passed, 1 intentional database-only test skipped**;
  147 integration tests were deselected by the explicit `-m "not integration"` selector.
- A full `pytest` invocation collected 209 tests: **63 passed, 146 skipped**. Skip reasons
  were the missing dedicated PostgreSQL `TEST_DATABASE_URL` for database-dependent tests
  and the separately documented opt-in local-model test. This is not a complete backend
  verification.
- Ruff checks and format checks: passed for the complete backend `app` and `tests` trees.
- Mypy: passed for all 68 backend application modules.
- `pip check`: passed.
- Frontend TypeScript check and production build: passed.
- Python compilation: passed.
- PostgreSQL integration, migrations, Compose, live model, and Playwright checks have not
  run in this continuation. Port 15432 did not accept a connection. The launched Docker
  Desktop UI is responsive, but `docker info` still reports that the configured Linux engine
  named pipe is missing, and `wsl -l -v` reports both Ubuntu and `docker-desktop` stopped.
  `com.docker.service` is not installed in this per-user Docker Desktop setup. No Docker
  reset, volume change, database reset, or model/cache deletion was attempted.

### Exact next steps

1. Start the Linux engine from the open Docker Desktop window and wait until `docker info`
   reports a Linux server. If Docker Desktop shows a WSL startup failure, resolve that
   through its ordinary WSL/backend troubleshooting path; do not factory-reset Docker or
   remove distributions/volumes.
2. Verify the existing `postgres_data` volume is present, then start the normal stack with
   `docker compose up -d` (never `down -v`). Confirm API, PostgreSQL, Redis, both workers,
   frontend, and Mailpit are healthy. Keep host PostgreSQL port `15432`.
3. Derive a fresh dedicated database name ending in `_test` from the existing migration URL,
   create that database without modifying `supportflow`, and set `TEST_DATABASE_URL` only for
   the test process.
4. Run full PostgreSQL backend tests, including the new summary, active-run, rate quota,
   tool-contract, and existing forced-RLS regression cases; check migrations and fresh
   install; then run full desktop/mobile Playwright.
5. With local Ollama/model available, verify reply, summary, and structured tool-assisted
   runs, polling terminal behavior, new quota feedback, staff/customer route handling, and
   System Status diagnostics. Do not report model quality as a verified metric from these
   functional checks.
6. Review the full diff and secrets, then commit locally only after required verification
   passes. Do not push or publish without explicit user permission.

## Final completion verification — 2026-11-26

Docker Desktop's Linux engine was restored. Docker CLI commands report Docker **29.9.3**;
Compose builds and starts successfully. `wsl -l -v` reports the `docker-desktop` WSL2
distribution Running (the separate Ubuntu distribution is stopped). PostgreSQL **16.12**
was healthy on the existing `127.0.0.1:15432` mapping; the normal stack migration completed
successfully.

### Database and regression verification

- Created/used two dedicated disposable databases, `supportflow_m10final_20261126_test`
  for Compose/browser/live-model exercises and `supportflow_m10verify_20261126_test` for
  the final clean regression/performance run. Both used existing configured database
  credentials and are distinct from `supportflow`. Test fixtures used random schemas and
  dropped only the schemas created by each test.
- Final full backend suite on `supportflow_m10verify_20261126_test`: **208 passed, 1
  intentional opt-in local-model skip**, zero PostgreSQL integration skips. The opt-in
  test was run independently against the first `_test` database and passed (**1 passed in
  118.23 s**) using the real installed Ollama model.
- After CI action versions were refreshed, the complete backend suite was rerun on a newly
  created disposable `supportflow_m10release_20261126_test` database: **208 passed, 1
  intentional opt-in local-model skip, zero unexpected skips** (202.90 s). The test
  fixture created and removed only its own random schemas.
- PostgreSQL migration/integration tests passed as part of the full suite, including fresh
  schema migration, M8-to-M9 upgrade behavior, forced-RLS access under the restricted
  runtime role, tenant isolation, and database constraints. The public test database was
  separately migrated to Alembic head for Compose-based verification.
- The first full-suite run exposed one stale test assertion checking only the final tool
  message despite the workflow calling all four read-only tools. The assertion now checks
  the complete tool sequence. The full suite passed after that correction.
- Ruff check and format checks passed; Mypy and dependency consistency passed; Python
  compilation passed. Frontend TypeScript and production build passed.
- Desktop and mobile Playwright: **20/20 passed**. Two initial attempts exposed stale
  expectations for the safe generic Copilot failure text and the expanded System Status
  component list; those assertions were updated to verify the intended user-facing behavior
  and required core services. A subsequent attempt reused Redis limiter counters and
  correctly received HTTP 429; the successful run used a fresh Redis logical DB, without
  deleting any Redis keys or weakening the configured limits.

### Compose and live-model verification

- Built and started the Compose stack. PostgreSQL, Redis, API, ticket worker, AI worker,
  frontend and Mailpit were healthy; migration exited successfully. Liveness and readiness
  returned `ok` and `ready`. The component status endpoint reported core dependencies,
  local embedding models, workers, and local Ollama available.
- Reproduced the CI database-outage behavior against the running Compose stack: while the
  PostgreSQL container was stopped, liveness remained HTTP 200 and readiness returned HTTP
  503; after starting the same service, readiness returned HTTP 200. The named volume was
  retained, and all services were healthy again afterward.
- For browser and live API checks, the API and workers were temporarily configured to use
  only the disposable `_test` database. Container PostgreSQL used `db:5432`; the original
  host-side `127.0.0.1:15432` URL failed inside the first temporary override and was
  corrected before tests proceeded. The standard Compose configuration was restored after
  verification, without removing volumes. The `postgres_data` and model-cache volumes,
  `.env`, and existing development database were retained.
- The installed local model was `qwen3.5:4b-q4_K_M` (Ollama reported a 3.3 GB artifact,
  digest prefix `d8b0f5e9760c`). It loaded with a 4096-token context and 100% GPU placement.
  The host has an Intel Core i5-12500H (12 cores/16 threads) and RTX 3060 Laptop GPU with
  6 GiB dedicated VRAM. A read-only snapshot after live inference showed about 2.2 GiB GPU
  memory free. Docker Desktop reported 16 CPUs and a 7.6 GiB Linux VM limit; one container
  memory snapshot totaled about 1.7 GiB. The C: drive had about 69 GiB free. These are
  point-in-time readings, not capacity benchmarks. Host RAM values remain those captured
  in the prior hardware observation in `docs/local-ai.md`.
- Through the real Docker API, outbox, Celery AI worker and local Ollama model, a synthetic
  published article indexed successfully. Grounded reply completed in **23,512 ms** with
  one validated article citation; public-conversation summary completed in **3,913 ms**;
  bounded read-only tool analysis completed in **90,772 ms**, using `get_sla_status`.
  These are one-off functional observations, not model-quality or production performance
  claims. No confidential or customer data was used.
- A live API quota check with a temporary one-request test limit returned **202** for the
  first request and **429** plus a 3600-second retry interval for the next request from the
  same user/organization. The stack was restored to the normal configuration afterward.
  Unit/integration tests cover safe provider failure states, job recovery, quota reservation,
  and the browser verifies that polling stops on both terminal success and failure.
- Existing AI answer-grounding and abstention evaluation targets remain unmet. No threshold
  was changed to conceal this result; generated replies remain optional, source-checked and
  human-reviewed. See `docs/ai-evaluation.md`.
- The performance harness was rerun with synthetic data on the fresh
  `supportflow_m10verify_20261126_test` database; current p50/p95 values, resource snapshots
  and scope limitations are recorded in `docs/performance-report.md`. Alembic upgrade and
  schema consistency checks then passed on that database. No concurrent-load or
  production-capacity claim is made.

### Final state and remaining release boundary

- The normal Compose stack is running with AI enabled and Ollama available. PostgreSQL stays
  mapped to host port `15432`; no volume or user data was reset or deleted.
- At the time the local verification was first recorded, changes had not yet been committed
  or pushed. The CI workflow was updated from Node 20-era action majors to current Node
  24-compatible majors (`checkout@v7`, `setup-python@v7`, `setup-node@v7`). Publication
  results are recorded below.
- No further M10 implementation work is pending. M10 remains experimental in answer quality;
  no production-readiness claim is made. Do not begin another milestone without approval.

### Publication verification — 2026-11-26

- Commit `e62a007486af01b0c0823aebec17dad159c1fe1a`
  (`feat(ai): improve grounded assistance and release diagnostics`) was pushed to the
  existing `origin/main` with a normal fast-forward push. Local and remote `main` tips
  matched after the push.
- GitHub Actions run
  [45821032643](https://github.com/ree1o/SupportFlowAI/actions/runs/45821032643) completed
  successfully for that commit: backend, frontend, and compose-smoke all passed.
- After CI completed, the Compose API, database, Redis, both workers, frontend, and Mailpit
  remained running; API liveness and readiness returned HTTP 200 and component status was
  operational.
