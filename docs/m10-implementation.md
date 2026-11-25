# M10 implementation checkpoint

Updated: 2026-11-25 (Asia/Qyzylorda)

## Current status

M10.1 was previously committed and pushed. M10.2–M10.6 implementation and release
verification changes are present in the working tree and remain uncommitted. No M11 work has
started. The current branch is `main`; the latest commit is
`181a6b9f53720ec406e3ae3077935a51558777e4` (`feat(backend): add optional local generation provider`).
At the last inspection, `origin` was `https://github.com/ree1o/SupportFlowAI.git`, and local
HEAD matched `origin/main`. Recheck the live Git state and remote before publication.

The full M10 code is implemented and local verification passed, with the important
qualification that the synthetic generation quality targets below were not met. Do not
present the Copilot as production-ready or its answers as reliably grounded. It is opt-in,
experimental, and human-review-only. No commit/push for M10.2–M10.6 has been made. GitHub
Actions for those changes have therefore not run.

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
- GitHub Actions for the uncommitted M10 changes are pending; no commit or push has occurred.

## Modified/untracked M10 scope

Inspect `git status --short --branch` for the authoritative path list. Changes are confined
to M10: configuration/Compose, `backend/app/ai/`, `backend/app/knowledge/`, semantic model
version compatibility, Celery/outbox and retention wiring, migrations `0010_knowledge_base`
and `0011_ai_runs`, tests/fixtures/evaluation tooling, Knowledge Base/Copilot React UI,
README, architecture/data-model/roadmap/local-AI/semantic docs, and this progress document.
Frontend Playwright artifacts and model caches are ignored generated/local files, not part
of the intended commit. No unrelated changes are known.

## Exact remaining steps

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
- At this point no M10 commit or push has been made. The last confirmed `origin/main` tip
  was `181a6b9f53720ec406e3ae3077935a51558777e4`; recheck before publishing. GitHub Actions
  for M10 have not yet run.

Remaining steps: finish the staged-diff/code and documentation review; stage only the
post-staging M10 edits; verify the staged diff and secrets scan; commit using the repository's
Conventional Commit rules; push normally to `origin/main`; verify remote/local HEAD equality;
and monitor backend, frontend, and compose-smoke Actions. If documentation is updated after
the first push with CI results, commit and push that documentation change normally and
verify its resulting Actions run too. Do not start M11.
