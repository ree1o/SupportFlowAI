# Portfolio release walkthrough and deployment checklist

## Reproducible local demonstration

Use local synthetic data, generated passwords, and Compose defaults. There are no
preseeded demo credentials. The demonstration does not require paid APIs or a public
endpoint.

1. Start Docker Compose and open the frontend. Register an organization owner, then
   create an administrator and agent through the existing membership flow.
2. Create a customer contact, invite the customer, accept the message from Mailpit,
   and sign in to the customer portal.
3. Create a customer ticket. As staff, post a public response and an internal note;
   show the customer sees the response but not the note.
4. Configure a short SLA target, create a ticket, and run the scheduled deadline scan
   to demonstrate the breach notification. Restore a normal target afterward.
5. Create a contact-bound integration source and use the signed example in
   `docs/integrations.md` to submit one ticket. Replay the same event and show its
   receipt returns the original outcome.
6. Create multiple tickets with a shared specific symptom and one unrelated ticket.
   In Incident Radar, inspect the deterministic evidence, reject one proposal, and
   confirm another after reviewing the group.
7. Create and publish a synthetic Knowledge Base procedure. Prepare the local embedding
   cache and optional Ollama model using `docs/local-ai.md`; wait for indexing to show
   `indexed`, then generate a reply, inspect its versioned citation, insert it into the
   public reply editor, edit it, and submit through the ordinary staff workflow. Use the
   read-only tool action to inspect the current ticket's SLA and published guidance.
8. Create differently worded but semantically related tickets. Show the staff Related
   Tickets panel returns same-organization candidates. Explain that retrieval and AI
   output are advisory; the [M10 evaluation](ai-evaluation.md) missed its proposed
   citation and abstention targets, so verify every source and draft manually.
9. Sign in as a second organization and show that ticket, incident, Knowledge Base,
   semantic retrieval, and Copilot run results stay tenant-scoped. Use
   `/api/health/live` and `/api/health/ready` to explain process and database readiness
   separately.

Do not describe an SLA notification or model result as guaranteed until the worker has
actually processed it. Do not use screenshots unless captured from the running app.

## Small-scale deployment checklist

- [ ] HTTPS termination; exact trusted origin; secure cookies enabled.
- [ ] Explicit, network-constrained reverse proxy topology; configure
      `SUPPORTFLOW_FORWARDED_ALLOW_IPS` for exact trusted proxy IPs/CIDRs and ensure the
      proxy replaces client-supplied forwarding headers. Never trust `*`.
- [ ] Unique token-encryption key and database credentials stored in a secret manager.
- [ ] Separate migration principal and `supportflow_runtime` login; verify runtime is
      not superuser, table owner, or `BYPASSRLS`.
- [ ] Apply migrations as a reviewed one-shot operation; confirm RLS policies and
      runtime grants after upgrade.
- [ ] Private PostgreSQL 16 + pgvector and Redis networks; no public DB/broker ports.
- [ ] Persistent database storage; encrypted, access-controlled, off-host backups;
      perform an isolated restore rehearsal.
- [ ] SMTP credentials in a secret store; delivery failure and outbox retry process
      monitored; no Mailpit in production.
- [ ] Durable local model cache/storage and enough CPU/RAM for the pinned ONNX model;
      model setup is explicit and never downloads on migration.
- [ ] Worker and Beat lifecycle, Redis recovery, pending outbox, failed jobs, and
      rate-limit Redis availability have an operator procedure.
- [ ] Health probes, request IDs, structured log redaction, log retention, alert
      routing, and resource limits are configured.
- [ ] Legal retention requirements, privacy notices, incident response, and customer
      data deletion obligations are reviewed.
- [ ] Run the complete CI workflow and desktop/mobile browser suite on the release
      candidate.

Recommended small deployment: one private application host running API, frontend
static hosting, and Celery worker processes; managed/private PostgreSQL 16 with
pgvector; private Redis; external SMTP; HTTPS reverse proxy; encrypted object-free
database backups. Keep the worker on private egress except to SMTP/model artifact
source during an explicit model setup. This is a recommended topology, not a deployment
or a production-readiness claim. No external server has been provisioned.

## Release evidence index

- M7 detector methodology and synthetic fixture results: [Incident Radar](incident-radar.md).
- M8 retrieval dataset, model, lexical comparisons and measured evaluation: [Semantic
  retrieval](semantic-retrieval.md).
- M10 local model setup, Knowledge Base and Copilot behavior: [local AI](local-ai.md),
  [Copilot](ai-copilot.md), and [synthetic evaluation](ai-evaluation.md).
- M9 runtime privilege matrix, RLS coverage, limits and retention: [Security hardening](security-hardening.md).
- Backup/recovery instructions: [Backup runbook](backup-recovery.md).
- Reproducible local request and detector measurements: [Performance report](performance-report.md).
- M9 local verification record and scope limits: [Verification record](m9-verification.md).
- CI and runtime test commands: [Verification](verification.md).

The performance report records its exact environment, command, data sizes, and
limitations. This repository does not claim production throughput, SLOs, or a tested
RPO/RTO.
