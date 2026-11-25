# Semantic ticket retrieval (M8)

## Scope and model

Semantic retrieval is a staff-only, read-only aid on ticket details. It returns nearby
ticket records for human inspection. A similarity score is a retrieval signal, not proof
of a common cause. M7's deterministic Incident Radar code and thresholds remain separate
and unchanged.

The original M8 deployment used FastEmbed 0.7.3 with
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, a multilingual English /
Russian-capable MiniLM sentence model. It emits 384-dimensional vectors and is Apache-2.0
licensed. FastEmbed runs the quantized ONNX model with ONNX Runtime on CPU; there is no
GPU or hosted inference requirement. The model card describes a 0.1B-parameter model;
FastEmbed's registered artifact is approximately 0.22 GB on disk. Plan for at least 1 GB
RAM for a worker running this model; that is a deployment budget estimate, not a measured
peak. Inference memory depends on runtime and batch size; the worker limits each embedding
call to one document and two ONNX threads. Sources: [model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2),
[FastEmbed](https://github.com/qdrant/fastembed), and
[FastEmbed 0.7.3 model registry](https://github.com/qdrant/fastembed/blob/v0.7.3/fastembed/text/onnx_text_model.py).

M10.2 upgraded the pinned runtime to FastEmbed 0.8.1 and ONNX Runtime 1.32.0 so the
separate Knowledge Base could use Qwen3 Embedding Q. Ticket index identity now includes
the adapter/runtime version. Existing 0.7.3 ticket vectors are retained as a compatible
legacy model version while 0.8.1 rows are indexed; query and candidate vectors are always
selected from the same version. A bounded backfill can reindex rows, and the old version
remains available for rollback. Never compare or mix vectors between versions.

The model ID and version are stored on each embedding. The ONNX artifact repository is
`qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` at immutable revision
[`faf4aa4225822f3bc6376869cb1164e8e3feedd0`](https://huggingface.co/qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/tree/faf4aa4225822f3bc6376869cb1164e8e3feedd0);
setup downloads only the five model/tokenizer files FastEmbed needs at that revision.
FastEmbed 0.8.1 is pinned for the current build. Model setup is explicit; application startup, migrations,
and tests do not download it. The worker's cache is a named Compose volume and weights
are never committed.

From the repository root, with the Compose worker image built and stack services available:

```powershell
docker compose run --rm worker python -m app.semantic.setup_model
```

The setup command downloads the model once to the worker cache. Without a cached model,
indexing reports retryable failures and related-ticket reads return an empty result with
the row's `pending` or `failed` status. Ticket creation and customer workflows remain
independent of model availability. After installing the model, failed rows can be
re-enqueued by an Owner or Administrator using the bounded indexing backfill endpoint.
The worker can also recover missing indexes in batches of 50.
Compose sets `HF_HOME` inside the writable model-cache volume so Hugging Face/Xet does not
try to write under the non-root container user's home directory.

## Source data, privacy and freshness

The indexed document consists only of the ticket title and its initial public message
(ticket message version 1). Later public conversation and all internal notes are excluded.
Category is not embedded, so category edits cannot make the source vector stale. Common
email addresses, URLs, phone-like values, bearer tokens, and key/value password/token/secret/
API-key patterns are masked before hashing and embedding. This is best-effort minimization,
not a guarantee that arbitrary personal or secret text is removed. Avoid putting sensitive
customer details in ticket titles or initial public messages.

Each row stores organization and ticket IDs, model version, SHA-256 source digest, ticket
version at enqueue, status, retry metadata, and indexing times. The unique current row is
scoped by organization, ticket and model. An older worker job compares the current safe
source digest before and after inference; changed or deleted sources are not published.
Current M3 has no title/body edit endpoint, but digest validation also guards future edits.

Rows have `pending`, `processing`, `ready`, `failed`, or `stale` state. New ticket
transactions add an embedding work row, transactional outbox event and background job in
the same PostgreSQL transaction. Existing ticket indexing is discovered periodically in
bounded batches. Celery uses the existing M4 Redis/outbox infrastructure; inference happens
outside ticket transactions. Retry attempts store the exception class only. Duplicate
outbox delivery reuses the one durable embedding row and completed job marker.

The only retrieval route is staff-authenticated and checks current organization
membership and the existing assignment visibility policy. The organization filter is in
the SQL vector query. Customer routes cannot call it. The source ticket is excluded;
closed candidates are excluded; agents see unassigned or self-assigned tickets, while
Owners and Administrators see their organization's tickets. Result previews are taken
only from initial public messages and masked again. No internal note, audit event, or
incident investigation field enters the model or response.

## PostgreSQL storage and retrieval

Migration `0008_semantic_retrieval` installs the pgvector extension in `public` and adds
`ticket_embeddings` with `vector(384)`, tenant-composite ticket FK, unique digest and
partial unique current-row constraints. The existing PostgreSQL 16 data volume and
loopback host port `15432:5432` are retained. The Compose image is the pgvector build for
PostgreSQL 16; do not remove the named volume to change images.

Queries use exact cosine distance (`<=>`) over ready/current rows filtered by organization,
model and ticket visibility. There is deliberately no approximate HNSW/IVFFlat index in
M8: the organization/status B-tree narrows the candidate set and exact scoring gives
deterministic top-K recall without approximate filtered-index recall trade-offs. Exact
work grows with the number of eligible vectors in one organization. Measure plans before
adding ANN; any later ANN plan must test filtered recall and tenant isolation.

## API and local demo

- `GET /api/organizations/{organization_id}/tickets/{ticket_id}/related?limit=5`
  returns the model/index status and up to 10 authorized ticket previews.
- `POST /api/organizations/{organization_id}/semantic-index/backfill` queues at most one
  configured batch for an Owner/Administrator and reports whether more eligible tickets
  remain.

On a fresh local setup, migrate, download the model into the worker cache using the command
above, sign in as staff, and create two requests such as:

1. Title: “Cannot sign in after resetting my password”; initial public text: “After the
   reset, authentication sends me back to the login screen.”
2. Title: “Authentication fails after password change”; initial public text: “My updated
   credentials are rejected and I cannot access the account.”

Open the first ticket's Related tickets panel. Once its outbox task finishes, the second
request should appear if it ranks above the documented 0.35 cosine-similarity floor. The
panel explains that the score is not a root-cause determination. The threshold is a
configuration default, not a quality claim.

## Evaluation

The M8 baseline fixture `backend/tests/fixtures/semantic_retrieval_heldout.json` contains
16 English/Russian queries and 32 candidates. M10.2 added an independent multilingual
retrieval evaluation with 36 candidate documents, 12 topics, three languages, and 48
held-out queries (plus a separate development set). Labels are read only by the harness,
not passed into the embedder. Every method ranks the same corpus. Methods are token
Jaccard, an M7 lexical-v1 adaptation, M8 MiniLM vectors under FastEmbed 0.8.1, and Qwen3
Embedding Q vectors for the Knowledge Base. Exact pgvector queries filter by organization
and current published revision. The harness reports P@5, Recall@5, MRR@5, indexing and
query latency, and storage. These are separate measurements for two different model
spaces; ticket and Knowledge Base vectors are never combined.

Run it from `backend` after creating the dedicated disposable test database and preparing
the local model cache:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://supportflow:supportflow_dev_only@127.0.0.1:15432/supportflow_test'
..\.venv\Scripts\python.exe -m app.semantic.evaluation
```

### Recorded local run

On 2026-10-31, the harness ran against PostgreSQL 16 with pgvector 0.8.1 on the local
Windows/Docker Desktop environment, using the pinned FastEmbed ONNX model on CPU. Warm
model query encoding and SQL timings below exclude one-time model startup/download. Hardware
specification was not captured, so these are reproducibility evidence for this run, not
capacity claims. Timing is environment-sensitive and is not a performance guarantee.

| Retrieval method | Relevant docs in top 5 / 240 | Queries with any hit / 48 | P@5 | Recall@5 | MRR@5 | Timing |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Unicode lexical Jaccard | 42 | 36 | 0.1750 | 0.2917 | 0.5938 | 2.31 ms total ranking |
| M7 lexical-v1 adaptation | 42 | 34 | 0.1750 | 0.2917 | 0.5729 | token scoring |
| M8 MiniLM, FastEmbed 0.8.1 | 63 | 36 | 0.2625 | 0.4375 | 0.5622 | index 36: 0.556 s; mean 8.51 ms; p95 10.99 ms |
| Qwen3 Embedding Q, KB | 86 | 42 | 0.3583 | 0.5972 | 0.8115 | index 36: 51.911 s; mean 2,243.71 ms; p95 2,965.85 ms |

For the 48 held-out queries, the Qwen Knowledge Base vector ranker placed 86 relevant
documents in the top-five result slots and had at least one relevant result for 42 queries,
compared with 63 documents/36 queries for M8 MiniLM, 42/36 for Jaccard, and 42/34 for M7
lexical-v1. The corpus has 144 labeled relevant query-document pairs. P@5 divides relevant
documents returned by 240 result slots; Recall@5 divides them by 144 relevant pairs; MRR@5
is mean reciprocal rank of the first relevant item (zero for a miss). Qwen had the strongest
ranking metrics in this fixture but was materially slower to index and query on CPU. These
one-run synthetic results are not a production-accuracy claim.

Semantic ranking helped on cross-language wording and synonym-heavy topics. Lexical
ranking remained competitive on literal overlap and some domain-specific terms. The exact
per-query ranks and benchmark code are stored in the fixture and harness for reproduction.

This compact synthetic set demonstrates implementation behavior only; it does not
establish production retrieval quality or generalization. Lexical search can win when exact
product terms, error codes or rare names matter. Semantic search can win when a paraphrase
uses different words or language.
Unclear language, domain-specific shorthand, near-duplicate generic complaints, and model
artifact quantization can still rank poorly; staff judgment remains required.

## Known limits

- One multilingual general-purpose model, one model version and one fixed-dimensional
  index; no per-tenant fine-tuning or model migration UI.
- FastEmbed 0.8.1 reports that this model mapping uses mean pooling in place of its earlier
  CLS behavior. The model package, artifact revision, evaluation and stored model version
  are pinned together; changing pooling behavior requires a new model version and reindex.
- Best-effort pattern masking is not PII detection or a secrets scanner.
- Exact cosine queries are simple and precise but may need ANN after measured growth.
- A missing local model leaves semantic results unavailable until cache setup and retry.
- Evaluation labels are synthetic and small. Do not generalize their results to customer
  data or production behavior.
- Semantic retrieval is not Incident Radar, root-cause analysis, or generated advice.
