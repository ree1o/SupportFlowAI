# Incremental release roadmap

Approved scope, 2026-10-31. M1-M8 are complete; the next milestone requires separate
approval.
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

## Release 0.2 - Reliable Backend (M4-M6)

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

### M5: SLA monitoring and reporting (L; completed)

- Simple policy snapshots, response/resolution deadlines, overdue scans, and reporting.
- Define precise time semantics and demonstrate deadlines at boundaries.
- Inbound webhooks are a separate future decision.
- Completed: [SLA semantics and metric definitions](sla.md).

### M6: Secure inbound integrations and webhooks (L; completed)

Independent demo: an Owner creates a source bound to one customer contact; a signed,
retryable event creates a ticket; duplicate delivery returns the same result; changing
the payload under the same event ID is rejected; source revocation stops further use.

- HMAC-SHA256 signatures over timestamp, event ID and raw JSON bytes with five-minute tolerance.
- Encrypted one-time credentials, versioned rotation, disable and terminal revoke.
- PostgreSQL unique receipts committed atomically with ticket/message, audit, optional SLA
  snapshot and notification outbox records. Explicit integration provenance; public messages only.
- Receipt metadata omits raw payload, signature and credential.
- Details and sender example: [inbound integration guide](integrations.md).

## Release 0.3 - Incident Radar (M7; completed)

Independent demo: related and unrelated complaints yield reviewable evidence; a manager
confirms or rejects a proposal and manages the resulting incident.

- Manual incidents, deterministic lexical time-window evidence, distinct contacts,
  rejection suppression, audit, and versioned detection inputs.
- Detector evaluation on labeled fixture pairs, including hard negatives; results are
  limited to the fixture and do not claim production accuracy.
- No embeddings or generative provider. Similar symptoms are not proof of a shared cause.

## M8 - Evaluated semantic retrieval (completed)

Independent demo: staff searches for a ticket with a differently worded but related
problem, reviews the similarity signal, and sees only tickets permitted by current
organization and assignment rules.

- CPU-only multilingual FastEmbed ONNX model with immutable artifact revision, local cache,
  explicit setup, PII/credential pattern masking, and no external inference requests.
- pgvector `vector(384)` storage in PostgreSQL 16, tenant-composite constraints, exact
  cosine ranking, transactional outbox indexing, digest-based staleness protection, retry
  state, and bounded existing-ticket backfill.
- Separate held-out bilingual retrieval evaluation against simple lexical and an M7 lexical
  adaptation. Its synthetic results are documented with counts, timings, storage, and limits
  in [semantic retrieval](semantic-retrieval.md); they do not change M7 behavior.

## Release 1.0 / M9 - Portfolio-ready hardening (L; later)

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

Critical path: M1 -> M2 -> M3 -> M4 customer portal/durable work -> M5 SLA -> M6
integrations -> M7 Incident Radar -> M8 evaluated semantic search -> M9 hardening ->
M10 AI Copilot. Only explicitly approved scope may be built.

No billing, inbound email ticket creation, uploads, business-hours calendars,
configurable roles, generic workflow builder, Kafka, Kubernetes, or microservices in
the initial system. Do not create future module skeletons or tables prematurely.
