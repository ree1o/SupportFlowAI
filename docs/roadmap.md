# Incremental release roadmap

Approved scope, 2026-10-28. M1–M3 are implemented; stop after M3 and wait for M4 approval.
Release 0.1 now has a demonstrable staff helpdesk; no public deployment is implied.
Complexities S/M/L are relative, not delivery-date commitments.

## Release 0.1 — Working Helpdesk (M1–M3)

Independent demo: staff from two organizations handle separate ticket queues, assign,
reply, add internal notes, and receive explicit stale-update conflicts.
Agent-entered customer contacts suffice here; customer self-service follows in 0.2.
Infrastructure: API, frontend, PostgreSQL only.

### M1: Reproducible Application Baseline (S–M; authorized)
- Backend: FastAPI factory/lifespan, settings, sync SQLAlchemy sessions, health/readiness,
  structured logs and safe error contract.
- Frontend: React/TypeScript availability page, same-origin proxy, retry/error state.
- Database: Alembic baseline, no business tables.
- Delivery: pinned dependencies/locks, Docker Compose, pytest, Ruff, GitHub Actions, Windows README.
- Tests: configuration, health/outage behavior, logging, session cleanup, PostgreSQL migration
  upgrade/downgrade and transactional rollback; frontend typecheck/build; Compose smoke in CI.
- Done: reproducible startup and checks, limitations reported honestly.
- Dependency: architecture approval. Risks: Windows networking, dependency compatibility.

### M2: Identity and tenant boundaries (L; completed)
- Backend: users, organizations, fixed roles, secure password/session lifecycle, CSRF,
  tenant-scoped authorization. No advanced RLS yet.
- Frontend: login/logout, organization selection.
- Database: users, sessions, organizations, memberships, uniqueness and ownership constraints.
- Tests: role matrix, session expiry/revocation, CSRF, cross-tenant access, final-owner protection.
- Done: separate organizations cannot access each other's resources.
- Depends on M1. Risks: credential/session handling and permission scoping.

### M3: Ticket workflow (L; completed)
- Backend: contacts, tickets, assignment, priority/category, workflow, messages, audit, version checks.
- Frontend: queue/filter/detail, clearly distinct public reply/internal-note actions.
- Database: tenant composite FKs, ticket/message/audit tables and measured indexes.
- Tests: invalid transitions, internal visibility, rollback, competing assignment/status writes.
- Done: usable staff helpdesk; explicit conflicts and no tenant leaks.
- Depends on M2. Risks: scattered business rules and lost updates.
- Evidence: [ticket design](tickets.md), [M3 verification](m3-verification.md).

## Release 0.2 — Reliable Backend (L)

Independent demo: customer submits and reads a reply; repeated webhook creates one
ticket; pause workers/broker and observe recovery; demonstrate SLA deadline notification.
Add Redis/Celery/one Beat scheduler only when durable jobs are implemented.
The capabilities below are release goals, not automatically authorized milestones.
M4 is next for customer portal/account access; its delivery dependencies and final
scope must be reviewed before development. M3 creates no future-release tables.

1. Durable work: outbox, operation IDs, retries, claims, recovery, terminal failures.
2. In-app notifications and SMTP adapter/local Mailpit for account flows.
3. Customer access/portal, verification, invitation, recovery.
4. Simple SLA policy snapshots and overdue scans; small operational metrics.
5. One signed inbound webhook with duplicate receipt handling.

Backend/database: outbox/job/notification records, account/customer tables, SLA tables,
integration source/receipt tables. Frontend: portal, invitation flows, deadlines,
notifications, minimal failed-work inspection. Tests: replay, worker/broker failure,
customer visibility, signed payloads, deadline boundaries and overlapping scans.
Done: committed work survives interruptions and reporting matches documented SLA rules.
Depends on 0.1. Risks: external delivery ambiguity, token linking, time semantics.

## Release 0.3 — Incident Radar (L)

Independent demo: related and unrelated complaints produce reviewable evidence; manager
confirms/rejects; related-ticket retrieval compares lexical and semantic results.

1. Manual incidents and role-enforced management.
2. Deterministic time-window/error-term evidence with distinct-customer support.
3. Review, rejection suppression, audit and input/configuration versioning.
4. Local embeddings + pgvector and exact tenant-filtered similar-ticket retrieval.
5. Held-out evaluation; enable semantic candidate expansion only when justified.

Backend/database: incident/proposal/link/detection/embedding records. Frontend:
incident details, evidence review, related-ticket panel and unavailable-model fallback.
Tests: concurrent confirmation, tenant isolation, hard negatives, stale embeddings,
retrieval precision@k/MRR and detector precision/recall. Done: reproducible baseline
comparison and useful manual operations without AI. Depends on 0.2. Risks: false groups,
dataset leakage, local model memory. No generative provider is required.

## Release 1.0 — Portfolio-ready hardening (L)

Independent demo: complete 0.1–0.3 workflow, failure recovery, evaluation report and
security/operational evidence on a reproducible release image.

- Add PostgreSQL RLS, separate DB privileges, transaction-local context and pool/worker tests.
- Complete rate limits, retention/anonymization, backup/restore, security review and UX errors.
- Full permission/isolation matrix, browser smoke tests, real-worker failure suite.
- Representative load tests with hardware/dataset/methodology; no invented performance claims.
- Migration upgrade verification, versioned image builds, deployment runbook and release checks.
- README, architecture/ER diagrams, decision records, evaluation report, short demo video.

Frontend: polish accessibility, errors/conflicts, empty/loading states. Database: policies,
privileges, evidence-driven indexes. Done: security and reliability acceptance criteria
pass and limitations are explicit. Depends on 0.3. Risks: configuration-only security
assumptions, unverifiable claims. Production deployment target requires a separate decision.

## Release 1.1 — AI Copilot, knowledge base and grounded RAG (L)

Independent demo: published knowledge revision yields a reviewable draft with verifiable
citations; unavailable provider falls back to manual work/search.

- Text articles with immutable revisions, authorized chunk retrieval and indexing.
- Separate embedding/generation adapters, disabled and test providers, optional hosted/local LLM.
- Summaries and drafts with input versions, provenance and human approval before sending.
- Evaluate retrieval, factuality, citation support, abstention and edit effort.
- Validate outputs, reject invented citation IDs, test injection and cross-tenant leakage.

Frontend: article editor, source display, approve/edit/reject panel. Database: knowledge,
AI run/suggestion/feedback tables. Done: measured assistance, source access rechecks,
no autonomous actions. Depends on 1.0. Risks: unsupported answers, private information
disclosure, model cost/latency. No mandatory paid API; fakes are never presented as real AI.

## Scope guardrails and critical path

M1 → M2 → M3 → durable work → SLA/customer workflows → incidents/retrieval → hardening → Copilot.
Each slice ends with a working demo and tests. Do not create future module skeletons or
tables just to mirror this roadmap. No billing, CRM, inbound email threading, uploads,
business-hours calendars, configurable roles, workflow builder, Kafka or Kubernetes initially.
Outgoing webhooks and async network batches are optional later extensions, not M1 work.
