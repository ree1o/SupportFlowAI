# Knowledge Base and staff AI Copilot (M10)

## Scope and operating model

The Knowledge Base stores organization-owned articles with immutable revisions. Owners and
Administrators create/edit/publish/archive articles; all active staff can read current
published content. Publishing creates a PostgreSQL outbox event; the isolated Celery `ai`
worker chunks and indexes the published revision using the separately versioned 1024D
Qwen3 Embedding Q model. Drafts are never retrieved for generation. Reindexing is explicit,
bounded, retryable and version-aware. A failed or missing model leaves ticket handling and
article editing usable.

The Copilot is a staff assistance feature embedded in ticket detail. Its three actions are:

- **Grounded reply:** retrieves up to four current published article chunks with a fixed
  similarity floor. It returns an editable draft and source citations or abstains.
- **Summary:** summarizes up to 12 recent public messages. It does not read internal notes
  and does not make operational policy claims.
- **Analyze with read-only tools:** runs a bounded model-selected tool loop for published
  knowledge search and the current ticket's SLA state. It returns private guidance, not a
  customer message.

The human may insert a reply draft into the existing public reply editor, then edit and
submit it through the normal ticket workflow. The model cannot post messages. Related-ticket
results are not used as policy sources and no cross-customer conversation is sent to the
model.

## HTTP contract

All endpoints require an active staff membership in the path organization. State-changing
requests require the existing session cookie and CSRF token.

| Method and route | Behavior |
| --- | --- |
| `GET /api/organizations/{organization_id}/ai/status` | Authenticated local model state and configured model identity. |
| `POST /api/organizations/{organization_id}/ai/tickets/{ticket_id}/runs` | `202`; requires `Idempotency-Key` and `{ "action": "reply" | "summary" | "agentic", "expected_ticket_version": N }`. |
| `GET /api/organizations/{organization_id}/ai/runs/{run_id}` | Current authorized run state, draft/summary, verified citations, and tool names. |
| `POST /api/organizations/{organization_id}/ai/runs/{run_id}/cancel` | Cancels a queued run; a running local inference returns `409`. |

Runs progress through `queued`, `running`, then `completed`, `failed`, `stale`, or
`cancelled`. The frontend polls every two seconds while active. A stale ticket version or
revoked membership prevents a result from being returned as current. Duplicate requests
with the same requester and idempotency key return the existing run; reusing that key with
different request content is rejected. Generation is limited to six new runs per hour per
client/organization rate-limit identity, 30 per organization per hour, and 120 globally per
hour. Redis applies each cap atomically. Runs are also bounded by the configured
prompt/token/provider time limits.

## Data sent to the local model

The provider is Ollama on the developer's host (`host.docker.internal:11434` from Compose).
There is no hosted provider, paid API, or automatic cloud fallback. The model endpoint is
fixed to an explicit local allowlist, the model ID is operator-selected from reviewed
choices, and inference is opt-in. The existing local model cache is external to Git and the
PostgreSQL volume.

The generation context contains only what each action needs:

- Ticket title, status, priority and category label.
- At most 12 public messages, at most 1,400 characters each. Author names are omitted.
- For reply generation, up to four retrieved chunks from the current published article
  versions. Contact email, phone and other contact profile fields are not selected.
- For tool-assisted analysis, bounded published article search results or current SLA
  deadline/status fields for the ticket already bound to the run.

Internal notes, customer portal account data, staff audit metadata, private incident notes,
other tickets' message bodies, webhook payloads, credentials and secrets are excluded.
Retrieved article text and customer messages are untrusted data; prompt text is only one
defense. Server-side allowlists, typed arguments, tenant-scoped queries, RLS, source checks,
and post-inference authorization rechecks enforce the security boundary.

## Citation validation and abstention

For a reply, the server assigns source IDs to the retrieved published chunks. The model can
cite only these short IDs. Pydantic validates the response structure; the application
rejects unknown source IDs, and the final transaction checks that every cited chunk is still
ready and belongs to an article whose current published revision matches. Run reads repeat
the current-publication and authorization checks. If a source was archived or superseded,
the run is returned as stale without the stored answer.

No source over the retrieval floor means the service records an insufficient-evidence
result without asking the model to improvise a reply. A model that abstains or cites no
verified source does not produce an insertable draft. Model-generated relevance values are
retrieval scores, not calibrated confidence probabilities.

## Bounded tool calling

Tool calling uses a small custom Python orchestrator over Ollama's local `/api/chat` tool
format. LangChain, LangGraph and MCP are deferred; two fixed read-only calls cover the
demonstrated use case without another abstraction framework or runtime.

| Tool | Typed input | Scope and output |
| --- | --- | --- |
| `search_knowledge_base` | One query string, 3-400 characters, no extra fields. | Up to four current published chunks in the run organization; sanitized title/excerpt, revision and source reference. |
| `get_sla_status` | Empty object only. | Response/resolution deadline and current state for the run's already-authorized ticket. |

Tool arguments cannot choose an organization, ticket, article, SQL statement or endpoint.
Each call rechecks the requester's active membership, ticket visibility and expected ticket
version. The executor independently validates argument shape and allowlists tool names.
Only one call is accepted per model turn, at most four model turns are made, duplicate tool
names are rejected, and total tool output is bounded. Errors return a safe rejection result;
the model never receives shell, SQL, mutation, arbitrary HTTP or permission-management
tools. The current API records successful tool names but not model arguments or hidden
reasoning.

## Background reliability and retention

Creating a run, its outbox event and its background job is one PostgreSQL transaction.
Celery/Redis dispatch is recoverable from PostgreSQL; inference and embeddings happen
outside business transactions. Duplicate delivery sees the durable run state and cannot
create duplicate drafts. Worker interruption recovers stale queued/running runs. Provider
errors are recorded with safe codes and bounded retries; no prompt or provider response is
written to logs. A run queued but not yet started can be cancelled; in-flight inference is
not interruptible in the initial implementation.

`ai_runs` is tenant-owned and uses forced PostgreSQL RLS with composite foreign keys to
organization/ticket/requesting membership. The runtime role has select/insert/update but no
delete. Generated answer text and citations are replaced with a redaction marker after the
configurable `SUPPORTFLOW_AI_RESULT_RETENTION_DAYS` period (30 days by default). Run state,
model digest, timing, requester and idempotency metadata remain for operational history.
Prompts, article text and tool arguments are not retained in the run row.

## Local setup and demo

1. Start the normal Docker Compose stack and install the Knowledge Base embedding model
   once with `docker compose run --rm worker-ai python -m app.knowledge.setup_model`.
2. Install/start Ollama on Windows, pull `qwen3.5:4b-q4_K_M`, and restrict host access to
   the local Docker network. Set `SUPPORTFLOW_AI_ENABLED=true` in ignored `.env` and keep
   `SUPPORTFLOW_AI_OLLAMA_URL=http://host.docker.internal:11434`.
3. Restart Compose, register an organization, create a staff ticket, and create/publish an
   internal article with a safe recovery or payment procedure.
4. Wait until the article's indexing state is `indexed`. Open the ticket, generate a
   grounded reply, inspect its article revision citation, then insert and edit the draft.
5. Add a public customer message containing a request to reveal internal notes and a
   separate internal staff note. Summarize and analyze the ticket; verify the internal
   note is absent and no generated content is sent automatically.
6. Disable or stop Ollama and confirm tickets, articles and the customer portal still work.

## Limitations

- Local quality and latency depend on model version, prompt length and available resources.
- The current CPU Qwen Knowledge Base embedder has materially higher latency than M8
  MiniLM; indexing stays off request paths and may be slow on small machines.
- Prompt-injection resistance is defense in depth, not a proof that generated content is
  safe or correct. A human must review drafts and cited articles.
- Tool calling is read-only and bounded; no automatic ticket mutation or customer message
  delivery exists.
- Cancellation after a worker starts, language quality across all dialects, and
  production-scale concurrency remain unverified.
- See [the synthetic multilingual evaluation](ai-evaluation.md) for measured retrieval,
  citation, abstention, and latency results. The proposed answer-grounding and abstention
  targets were not met; treat generation as experimental and review each draft.
