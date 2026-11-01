# SupportFlow AI

**A smarter, self-hosted helpdesk for teams that want to resolve issues, not just track them.**

[![CI](https://github.com/ree1o/SupportFlowAI/actions/workflows/ci.yml/badge.svg)](https://github.com/ree1o/SupportFlowAI/actions/workflows/ci.yml)

SupportFlow AI brings customer conversations, team workflows, SLA tracking, and incident management into one workspace. It also helps agents discover similar tickets using **local multilingual semantic search** — without sending ticket content to a hosted AI provider.

## What it does

- **Keep support organized.** Create, assign, prioritize, and resolve tickets with a complete activity history, public replies, and private internal notes.
- **Give customers a portal.** Let customers access their own tickets, reply to agents, and receive notifications.
- **Stay ahead of deadlines.** Set SLA targets by priority, monitor response and resolution times, and get breach alerts and operational reports.
- **Connect other systems.** Receive tickets and messages through signed webhooks with contact-scoped access, key rotation, and duplicate-event protection.
- **Spot recurring problems.** Incident Radar groups related reports into explainable proposals that staff review before creating an incident.
- **Find useful context faster.** Related Tickets uses local embeddings and PostgreSQL `pgvector` to surface semantically similar requests, including across languages.
- **Protect each workspace.** Organization isolation, role-based permissions, PostgreSQL row-level security, CSRF protection, audit trails, and targeted rate limits.

> **AI, with human control:** Incident Radar uses explainable rules, while Related Tickets uses semantic similarity. Neither automatically diagnoses a root cause or sends AI-generated replies.

## Tech stack

| Layer | Technologies |
| --- | --- |
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI, SQLAlchemy, Alembic |
| Data | PostgreSQL 16, pgvector, Redis |
| Background jobs | Celery, transactional outbox |
| Semantic search | FastEmbed, local CPU embeddings |
| Development & quality | Docker Compose, Mailpit, pytest, Playwright, GitHub Actions |

## Run locally

**Requirements:** Docker Desktop with WSL 2 and Linux containers (Windows), or Docker Engine with Compose on Linux/macOS.

From a fresh clone, in **PowerShell**:

```powershell
git clone https://github.com/ree1o/SupportFlowAI.git
cd SupportFlowAI
Copy-Item .env.example .env
docker compose up --build -d --wait --wait-timeout 240
```

For semantic ticket suggestions, download the local embedding model once:

```powershell
docker compose run --rm worker python -m app.semantic.setup_model
```

| Open | URL |
| --- | --- |
| SupportFlow AI | http://localhost:5173 |
| Email inbox (Mailpit) | http://localhost:8025 |
| API documentation | http://localhost:8000/api/docs |

**Try it:** Register an account and workspace, add a contact, and create a ticket. Post a public reply and an internal note; then explore **SLA & operations**, **Incident Radar**, and **Related Tickets**.

To stop the stack safely:

```powershell
docker compose down
```

The PostgreSQL volume is preserved by `docker compose down`. **Do not use `down -v`** unless you intentionally want to erase local data. Keep your existing `.env` when restarting.

> **Deployment note:** The provided Compose setup is for local development and demonstration, with ports bound to `127.0.0.1`. Public deployment requires production configuration, TLS, and appropriately managed secrets.

## Tested and documented

The verified release includes **164 passing backend tests**, **16 Playwright browser scenarios**, and successful GitHub Actions checks for the backend, frontend, and Docker Compose smoke test.

**Explore the details:** [Architecture](docs/architecture.md) · [Customer portal](docs/customer-portal.md) · [SLA](docs/sla.md) · [Integrations](docs/integrations.md) · [Incident Radar](docs/incident-radar.md) · [Semantic search](docs/semantic-retrieval.md) · [Security](docs/security-hardening.md) · [Backup & recovery](docs/backup-recovery.md) · [Verification](docs/m9-verification.md)
