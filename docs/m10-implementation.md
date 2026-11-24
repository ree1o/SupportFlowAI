# M10 implementation progress

## Approved scope

Implement M10.1 through M10.6 as an optional, local-first Knowledge Base and staff
Copilot extension. Preserve M1-M9 behavior, tenant isolation, PostgreSQL RLS, the
transactional outbox, and the existing M8 ticket embedding index. Do not send customer
data to cloud inference services or automatically send generated replies.

## Baseline

- Branch: `main`
- Starting commit: `2237f9905672bce6a2eb64099c4dc39d8f888c00`
- Starting tree: clean; `origin` is `https://github.com/ree1o/SupportFlowAI.git`.
- Available prior M9 verification: 164 backend tests, 16 Playwright scenarios, and
  passing backend/frontend/Compose CI checks. This milestone does not repeat the full
  M1-M9 suite before making progress.
- Existing implementation: FastAPI modular monolith, synchronous SQLAlchemy,
  PostgreSQL 16/pgvector, Redis/Celery/outbox, React/TypeScript, forced RLS, and local
  384D MiniLM ticket retrieval.

## Phase status

| Phase | Status | Evidence / next gate |
|---|---|---|
| M10.1 Local LLM infrastructure and benchmark | Complete | Local Ollama provider, optional settings, authenticated staff status route, fake-provider tests, local model smoke test, Docker host connectivity, and documentation verified. Dedicated PostgreSQL test database run: 174 backend tests passed, zero skipped; Ruff, format, mypy, dependency checks, Compose build/start/readiness passed. |
| M10.2 Knowledge Base and multilingual retrieval | In progress | Qwen3-Embedding-Q trial on the existing 16-query synthetic M8 held-out set: recall@5 16/16, MRR@5 0.8021; MiniLM: 15/16, 0.7344; lexical Jaccard: 14/16, 0.5146. Trial is indicative only; evaluate a separate 60-scenario Knowledge Base corpus and memory/latency behavior before adoption. |
| M10.3 Grounded RAG and durable AI runs | Pending | Requires M10.1 provider and M10.2 published-source retrieval. |
| M10.4 Staff Copilot UI | Pending | Requires M10.3 contracts. |
| M10.5 Bounded tool calling | Pending | Requires M10.3 run lifecycle and M10.2 search. |
| M10.6 Evaluation and release hardening | Pending | Freeze configuration before held-out evaluation; final regression, migration, Docker, and CI gates. |

## Decisions and evidence log

- Keep native Windows Ollama with the NVIDIA GPU and use `host.docker.internal` from
  Compose. The actual container could reach the host's local Ollama version endpoint;
  no network/firewall changes were needed. Ollama stays outside the app health gate.
- Primary model is `qwen3.5:4b-q4_K_M`, digest
  `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`, 3,324,173,934
  bytes. Host smoke measurements and caveats are documented in `docs/local-ai.md`.
- A separate Qwen3-Embedding-Q evaluation environment verified FastEmbed 0.8.1 and
  ONNX Runtime 1.32 on CPU. It achieved 16/16 recall@5 on the existing 16-query M8
  synthetic test versus MiniLM's 15/16, but mean query inference was 1.76 s versus
  13.59 ms. M8's live model/table/configuration remain unchanged; no project dependency
  or embedding model has been upgraded yet.
- FastEmbed 0.7.3 does not register either `intfloat/multilingual-e5-small` or
  `Qwen/Qwen3-Embedding-0.6B-Q`. FastEmbed 0.8.1 registers the Q model (1024D, Q ONNX);
  the pinned Qdrant artifact revision used for the local trial is
  `af95f2c416ffe9379369ad64f9113e865db6112c` (1,120,604,114-byte model file).
- Comparing the existing M8 quantized MiniLM adapter across FastEmbed 0.7.3 / ONNX
  Runtime 1.31.1 and FastEmbed 0.8.1 / ONNX Runtime 1.32 produced identical normalized
  vector hashes for three synthetic English, Russian, and Kazakh inputs. This small
  compatibility check is not sufficient by itself to justify a runtime upgrade.
- Docker Desktop Linux engine is healthy. The existing `supportflow_postgres_data` and
  `supportflow_semantic_model_cache` volumes were retained. A separate
  `supportflow_m10_20261124_test` database was created; the full current backend suite
  completed there with 174 passed and zero skipped.
- Retain the M8 MiniLM ticket embedding model, 384D schema, and retrieval behavior.
- Knowledge Base embeddings will have a separate model/version namespace and vector
  schema; no vectors from different models will be mixed.
- Keep local inference optional. A missing Ollama service must not make API readiness
  fail or interrupt ordinary helpdesk operations.
- Use a typed custom Python provider and bounded orchestration; no framework or cloud
  provider is approved for the initial implementation.
- Model digest, artifact size, license, short warm/cold smoke timings, and initial
  hardware observations are recorded in `docs/local-ai.md`. The smoke probe is too
  short to establish useful output latency or quality. Representative generation
  quality/concurrency, full KB evaluation, and final migration/test/CI evidence remain
  outstanding; do not claim them until observed.

## Outstanding issues / next steps

1. Build the separate 60-scenario KB retrieval fixture (12 dev / 48 held-out), and
   compare lexical, MiniLM, and Qwen3-Embedding-Q with identical organization-filtered
   corpora, task-specific query instructions, and explicit relevance labels.
2. Measure CPU indexing/latency and resource use under Compose load; decide whether the
   Qwen candidate's retrieval gain justifies the added worker memory/latency.
3. Implement the KB with a separate, versioned 1024D vector space only after the
   evaluation gate; keep M8 ticket embeddings and retrieval unchanged.
4. Record phase verification, commits, CI results, and next actions here.
