# Customer portal and durable processing (M4)

M4 adds a customer-facing path alongside staff membership. A global user may be staff
in one organization and a customer in another; access is granted by explicit rows in
`customer_access`, each tied to an organization and contact. Email equality alone never
creates that relationship.

## Customer account lifecycle

Public registration returns the same accepted response for eligible and ineligible
requests. Eligibility requires an active organization contact with the normalized email.
The user remains unverified and receives no customer access until using a random
single-use verification token. Existing accounts do not gain contact access by email;
an organization manager must invite the exact contact. Invitations can create a new
account or attach an existing account only after the user proves access to the email and
matches the invitation identity. Revoke access from the staff organization interface.

Verification, invitation and recovery tokens are random, expire, are single-use and are
stored as hashes. Email content with action links is encrypted before it is persisted in
the outbox. Password recovery gives the same accepted response whether the address is
eligible. Completing recovery revokes the user's active sessions. The M2 session cookie,
server-side hashed sessions and CSRF checks continue to apply.

## Authorization boundary

Customer access is independent of Owner/Administrator/Agent staff memberships. Customer
handlers resolve current session, active customer grants, organization, contact and
requester ticket on every request. Ticket UUIDs and organization IDs are identifiers,
not proof of access. A customer can create requests, read their own requests and public
messages, and append public replies. Closed tickets cannot be replied to; stale versions
return the existing conflict response. Customer endpoints do not expose staff activity,
audit, assignments, internal notes, incidents or organization administration.

Staff ticket schemas retain full history. Customer-specific schemas and SQL queries
filter to public messages before serialization, so internal note bodies are not fetched
into customer responses. Notifications are similarly separated by recipient and
audience. Staff reply events go to the related contact's verified customer account;
internal notes create no customer notification.

## API surface

All paths are under `/api`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/customer/register` | Privacy-preserving customer registration request |
| POST | `/customer/verify` | Consume verification token |
| POST | `/customer/password-recovery` | Privacy-preserving recovery request |
| POST | `/customer/password-recovery/complete` | Set password and revoke sessions |
| POST | `/customer/invitations/accept` | Accept an organization contact invitation |
| GET | `/customer/me` | Current customer profile and authorized organizations |
| GET | `/customer/organizations` | Authorized organizations |
| GET | `/customer/organizations/{org}/tickets` | Own tickets, optional status/page filters |
| POST | `/customer/organizations/{org}/tickets` | Create a support request |
| GET | `/customer/organizations/{org}/tickets/{ticket}` | Detail and public conversation |
| POST | `/customer/organizations/{org}/tickets/{ticket}/replies` | Append a public reply with expected version |
| GET/POST | `/customer/notifications` and `/{id}/read` | Customer inbox/read state |
| GET | `/organizations/{org}/customer-access` | Staff list of explicit customer grants |
| POST/DELETE | `/organizations/{org}/customer-access/invitations`, `/{access}` | Owner/Administrator invite or revoke |
| GET | `/organizations/{org}/notifications` | Staff inbox for the selected organization |
| GET/POST | `/organizations/{org}/background-jobs/failed`, `/{job}/retry` | Manager recovery of failed work |

## Durable job processing

Domain changes and required outbox records commit together in PostgreSQL. API requests
do not depend on Redis availability. A single Celery worker process runs one Beat
scheduler that periodically polls the outbox and claims jobs with short database
transactions and `FOR UPDATE SKIP LOCKED`. A claim has an expiry, so a dead worker's
job becomes eligible again. SMTP occurs after the claim transaction commits.

Retry delays are bounded and attempts terminate in an observable failed state. Owners
and Administrators can inspect and retry failed jobs. The relay is the recovery source;
broker messages are only a wake-up/scheduling mechanism and may be duplicated. Unique
outbox event keys and email-delivery markers prevent routine task redelivery from
creating multiple logical delivery records. SMTP can accept mail and then time out, so
the system cannot promise exactly-once receipt; this external ambiguity remains a
documented limitation.

No arbitrary task body, SMTP exception text, email token, message content or credentials
are written to logs. Job records keep event type, attempt count, safe error class,
timestamps and organization scope. Local Compose uses Mailpit at `localhost:8025`, with
no paid provider or real SMTP credentials.

## M4 schema additions

Migration `0004_m4` is additive. It adds `customer_access`, `customer_invitations`,
`account_tokens`, `outbox_events`, `background_jobs`, `email_deliveries`, and
`notifications`, along with verification and customer-author references on existing
identity/ticket/audit records. Composite organization foreign keys keep customer grants,
contacts, tickets, messages and audit references in one tenant. Unique token hashes,
event keys, active user/contact grants and delivery keys enforce important invariants.
Migrations preserve M1-M3 rows; RLS remains deferred to M9.

## Local demonstration

Start the Compose environment and open the frontend. Staff can create a contact, then
invite it from Contact management. Mailpit at <http://localhost:8025> shows the
synthetic invitation link. Accept it in a private browser window, submit a customer
request, and then return to the staff session to add an internal note and a public
reply. The customer's detail page must show the public reply but never the internal
note. Reply as the customer and verify the staff notification.

To inspect processing, use `docker compose logs worker mailpit` and the authorized
failed-job API. Stop Redis or the worker, create a new request that queues a
notification, and restart the worker; the database outbox remains committed and the
relay recovers it. Avoid destroying the local database volume unless intentionally
resetting disposable data.

## Limitations

Local HTTP disables Secure cookies only in Compose. Production needs HTTPS, a unique
Fernet key, SMTP provider configuration, rate limiting and deployment-specific secret
management. Email delivery is SMTP only; there is no inbound email, threading, attachment
handling, customer profile editor, billing, or invitation password reset flow.
Advanced RLS, SLA, webhook integration, incidents and AI are later milestones.
