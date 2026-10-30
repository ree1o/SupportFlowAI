# Incremental release roadmap

Approved scope, 2026-10-30. M1-M4 are complete; M5 requires separate approval.
Each release has a self-contained demonstration. Completion never authorizes the next
milestone. Complexity estimates are relative, not delivery-date commitments.

## Release 0.1 - Working Helpdesk (M1-M3)

Independent demo: staff in separate organizations manage queues, assign and reply to
tickets, add private notes, and see stale-update conflicts. Infrastructure is API,
frontend, and PostgreSQL.

- M1: reproducible FastAPI/React baseline, pinned dependencies, migrations, Docker,
  health checks, tests, linting, CI, and Windows setup.
- M2: global staff identity, secure sessions/CSRF, fixed roles, organizations, and
  tenant isolation.
- M3: contacts, categories, tickets, assignment, public messages, internal notes,
  append-only audit, and optimistic concurrency.
- Evidence: [identity design](identity.md), [ticket design](tickets.md), and the
  [M3 verification record](m3-verification.md).

## Release 0.2 - Reliable Backend (M4-M5)

### M4: Customer portal and durable background processing (L; completed)

Independent demo: a customer accepts an invitation, creates a request, receives a staff
reply, and responds. Pause Redis or the worker and show that committed outbox work is
recovered. Prove that internal notes never reach customer responses.

- Contact-bound customer access, verification, invitations, password recovery,
  revocation, existing sessions, and CSRF.
- Customer-specific ticket reads and writes; public conversations only.
- PostgreSQL transactional outbox, Celery/Redis worker, one Beat scheduler, retries,
  idempotency markers, expiring claims, recovery, and failed-job inspection.
- Account and public-reply notifications via SMTP; Mailpit captures local mail.
- Tests: token lifecycle, tenant isolation, privacy, replay, worker/broker failures,
  and complete customer-to-agent browser journey.
- Risk: SMTP may accept a message and then time out. Exactly-once email is impossible;
  delivery ambiguity is recorded and replays use stable event keys.

### M5: SLA monitoring and reporting (L; not yet approved)

- Simple policy snapshots, response/resolution deadlines, overdue scans, and reporting.
- Define precise time semantics and demonstrate deadlines at boundaries.
- Requires explicit approval after M4. Inbound webhooks are a separate future decision.

## Release 0.3 - Incident Radar (L; later)

Independent demo: related and unrelated complaints yield reviewable evidence; a manager
confirms or rejects a proposal; lexical and semantic search are compared.

- Manual incidents, deterministic time-window/error-term evidence, distinct contacts,
  rejection suppression, audit, and versioned detection inputs.
- Local embeddings plus pgvector and tenant-filtered related-ticket retrieval.
- Held-out retrieval and detector evaluation, including hard negative examples.
- No generative provider required. Similar symptoms are not proof of a shared cause.

## Release 1.0 - Portfolio-ready hardening (L; later)

Independent demo: complete core workflows, recovery and evaluation evidence on a
reproducible release image.

- PostgreSQL RLS, non-owner runtime privileges, transaction-local tenant context,
  pool reuse and worker tests.
- Rate limits, retention/anonymization, backup/restore, security review, complete
  isolation matrix, browser smoke tests, and real-worker failure scenarios.
- Migration upgrade checks, image builds, deployment runbook, representative load
  tests with disclosed hardware/data/methodology, and polished documentation.
- No performance claims without measured results. Production deployment target needs
  a separate decision.

## Release 1.1 - Knowledge base and grounded AI Copilot (L; later)

Independent demo: a published knowledge revision produces a human-reviewable draft
with verifiable citations; disabled/unavailable providers leave manual workflows usable.

- Immutable knowledge revisions, authorized chunk retrieval, optional embeddings and
  hosted/local provider adapters, with test fakes.
- Summaries and suggested drafts include input versions and provenance; no automatic
  sending. Evaluate retrieval, citations, abstention, injection, leakage, and edit effort.
- No mandatory paid service. Reject invented citation IDs and recheck source access.

## Scope guardrails

Critical path: M1 -> M2 -> M3 -> M4 customer portal/durable work -> M5 SLA -> M7
Incident Radar -> M9 hardening -> M10 AI Copilot. Milestone identifiers may include
separate integration/retrieval releases. Only explicitly approved scope may be built.

No billing, inbound email ticket creation, uploads, business-hours calendars,
configurable roles, generic workflow builder, Kafka, Kubernetes, or microservices in
the initial system. Do not create future module skeletons or tables prematurely.
