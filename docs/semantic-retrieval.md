# Semantic ticket retrieval (M8)

## Scope and model

Semantic retrieval is a staff-only, read-only aid on ticket details. It returns nearby
ticket records for human inspection. A similarity score is a retrieval signal, not proof
of a common cause. M7's deterministic Incident Radar code and thresholds remain separate
and unchanged.

The pinned local adapter uses FastEmbed 0.7.3 with
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

The model ID and version are stored on each embedding. The ONNX artifact repository is
`qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` at immutable revision
[`faf4aa4225822f3bc6376869cb1164e8e3feedd0`](https://huggingface.co/qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/tree/faf4aa4225822f3bc6376869cb1164e8e3feedd0);
setup downloads only the five model/tokenizer files FastEmbed needs at that revision.
FastEmbed 0.7.3 is also pinned. Model setup is explicit; application startup, migrations,
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

`backend/tests/fixtures/semantic_retrieval_heldout.json` is a separate synthetic held-out
set: 16 queries, 32 candidate tickets, one labeled relevant candidate per query. It
contains English and Russian paraphrases, cross-language wording, generic-word negatives,
same-category and close-time distractors. Labels are used only by the evaluation harness,
not embedding inputs. Every method sees the same candidate set. The three rankers are
simple token Jaccard, a documented lexical-v1 adaptation using M7 significant terms,
category and time, and exact pgvector cosine retrieval. The harness reports P@5, Recall@5,
MRR@5, ranking/embedding/SQL query latency, vectors/second and PostgreSQL table size.
It inserts a foreign-tenant nearest vector and asserts the organization-filtered query
never returns it. Temporary vectors use a random schema in a database whose name ends in
`_test`, and the schema is removed in `finally`.

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

| Retrieval method | Relevant in top 5 / 16 | P@5 | Recall@5 | MRR@5 | Mean ranking / query time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Simple token Jaccard | 14 | 0.1750 | 0.8750 | 0.5146 | 0.235 ms ranking |
| M7 lexical-v1 adaptation | 7 | 0.0875 | 0.4375 | 0.2469 | 0.273 ms ranking |
| Semantic exact pgvector | 15 | 0.1875 | 0.9375 | 0.7344 | 6.84 ms query embedding + 1.844 ms SQL |

Semantic retrieval ranked 15 of the 16 relevant items into the same top-five candidate
window, compared with 14 for simple lexical and 7 for the M7-style lexical adaptation.
The indexing run embedded 32 documents in 0.262 seconds (121.97 documents/second), stored
49,152 raw vector bytes, and measured 106,496 bytes for the temporary PostgreSQL table
(heap and primary key, including the additional foreign-tenant isolation-check row).
The tenant filter check passed. The dataset has one relevant label per query, so Precision@5
is relevant hits divided by 80 result slots; Recall@5 is hits divided by 16 relevant
labels; MRR@5 is mean reciprocal rank, with zero for a miss.

Representative per-query results show the trade-off. On q06, the Russian query for an
empty exported CSV report ranked its English paraphrase at rank 1 semantically versus rank
8 with token overlap. On q16, the Russian old-delivery-address complaint ranked at 1
semantically and 4 lexically. Lexical search ranked q10 (subscription auto-renewal did not
stop before the next charge) at rank 2 while semantic retrieval missed its relevant result
in the top five; q01 ranked 3 lexically versus 4 semantically. The harness prints every
query's relevant rank per method for reproducibility.

This compact synthetic set demonstrates implementation behavior only; it does not
establish production retrieval quality or generalization. Lexical search can win when exact
product terms, error codes or rare names matter. Semantic search can win when a paraphrase
uses different words or language.
Unclear language, domain-specific shorthand, near-duplicate generic complaints, and model
artifact quantization can still rank poorly; staff judgment remains required.

## Known limits

- One multilingual general-purpose model, one model version and one fixed-dimensional
  index; no per-tenant fine-tuning or model migration UI.
- FastEmbed 0.7.3 reports that this model mapping uses mean pooling in place of its earlier
  CLS behavior. The model package, artifact revision, evaluation and stored model version
  are pinned together; changing pooling behavior requires a new model version and reindex.
- Best-effort pattern masking is not PII detection or a secrets scanner.
- Exact cosine queries are simple and precise but may need ANN after measured growth.
- A missing local model leaves semantic results unavailable until cache setup and retry.
- Evaluation labels are synthetic and small. Do not generalize their results to customer
  data or production behavior.
- Semantic retrieval is not Incident Radar, root-cause analysis, or generated advice.
