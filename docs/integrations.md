# M6 inbound integrations

An integration source is organization-owned and bound to one active customer contact.
It can create a ticket for that contact or append a public customer message to a ticket
requested by that same contact. It cannot create internal notes, assign staff, change
ticket workflow state, supply an organization ID, or choose a different requester.
Owners and Administrators manage sources; an Agent cannot rotate or revoke credentials.

Staff management endpoints use the existing session cookie, organization membership,
role checks and Origin/CSRF protections:

| Method | Path suffix under `/api/organizations/{org}/integrations` | Purpose |
| --- | --- | --- |
| `GET` | organization integrations root | List sources without credentials |
| `POST` | organization integrations root | Create a source; return a credential once |
| `PATCH` | `/{source_id}` | Rename or enable/disable |
| `POST` | `/{source_id}/rotate-credential` | Invalidate old key and return replacement once |
| `DELETE` | `/{source_id}` | Terminal revocation |
| `GET` | `/receipts` | Paginated receipt history; optional `source_id` and `status` filters |

The external signed endpoint is `POST /api/integrations/{source_id}/events`; it uses
the event signature instead of a browser session or CSRF token.

## Create a source

1. In the staff application, open **Integrations**.
2. Select a descriptive name and the exact active contact this source is authorized to
   represent.
3. Create the source and immediately save the returned signing credential in the
   upstream system's secret manager. The credential is displayed once. It is encrypted
   at rest with `SUPPORTFLOW_TOKEN_ENCRYPTION_KEY` and never returned by list/read APIs.
4. Use the source UUID in the endpoint URL. A source cannot change its contact mapping.

The key can be rotated from the source list. Rotation invalidates the previous key
immediately and reveals the replacement once. Disabling stops webhook operations but
preserves the record. Revocation is terminal. Protect the credential like a password;
do not place it in source control, URLs, logs, or error reports.

## Signature protocol

Send these headers:

| Header | Value |
| --- | --- |
| `X-SupportFlow-Event-Id` | Stable upstream event identity, 1–128 ASCII letters, digits, `.`, `_`, `:`, or `-` |
| `X-SupportFlow-Timestamp` | Unix timestamp in whole seconds |
| `X-SupportFlow-Signature` | `v1=` followed by lowercase hexadecimal HMAC-SHA256 |
| `Content-Type` | `application/json` |

Sign these exact bytes (UTF-8 for the text components):

```text
timestamp + "." + event_id + "." + raw_request_body
```

The HMAC key is the one-time credential string. The body is the exact byte sequence
transmitted; reserializing JSON after signing changes the signature. Timestamps more
than five minutes from server time are rejected. Comparisons use constant-time
comparison. Requests are limited to 64 KiB. The signature also binds the event ID, so
an event ID cannot be substituted independently of its body.

## Supported events

Create a ticket:

```json
{
  "type": "ticket.created",
  "title": "Card payment failed",
  "message": "Checkout returned a payment timeout.",
  "priority": "high"
}
```

Append a public customer message to a ticket already associated with the source's
contact:

```json
{
  "type": "ticket.public_message",
  "ticket_id": "00000000-0000-0000-0000-000000000000",
  "message": "The payment still fails after retrying."
}
```

The server supplies organization and requester from the source configuration. A
message from an integration is attributed to `<source name> integration`; it is not a
human staff reply and does not satisfy the first-human-response SLA. Public message
notifications and new-ticket alerts use the existing staff notification and
transactional outbox flow; delivery stays asynchronous. Ticket creation uses the
ordinary SLA snapshot rules.

A public integration reply to a `resolved` ticket reopens it to `open` in the same
versioned transaction, matching customer-portal behavior. The first-resolution
timestamp and original SLA snapshot are preserved. A `closed` ticket rejects replies;
an authorized staff member must reopen it first.

## Python sender example

```python
import hashlib
import hmac
import json
import time
import urllib.request
import uuid

base_url = "http://localhost:8000"
source_id = "replace-with-source-uuid"
credential = "paste-the-one-time-issued-credential-here"
event_id = str(uuid.uuid4())  # persist/reuse this ID when retrying the event
timestamp = str(int(time.time()))
body = json.dumps(
    {
        "type": "ticket.created",
        "title": "Card payment failed",
        "message": "Checkout returned a payment timeout.",
        "priority": "high",
    },
    separators=(",", ":"),
).encode("utf-8")
signed = timestamp.encode() + b"." + event_id.encode() + b"." + body
digest = hmac.new(credential.encode(), signed, hashlib.sha256).hexdigest()
request = urllib.request.Request(
    f"{base_url}/api/integrations/{source_id}/events",
    data=body,
    method="POST",
    headers={
        "Content-Type": "application/json",
        "X-SupportFlow-Event-Id": event_id,
        "X-SupportFlow-Timestamp": timestamp,
        "X-SupportFlow-Signature": f"v1={digest}",
    },
)
with urllib.request.urlopen(request) as response:
    print(response.status, response.read().decode())
```

This is a local example only. In a real integration, read the credential from the
upstream secret store and persist the event ID with the upstream event before sending.

## Idempotency, receipts, and errors

The database uniquely identifies `(source_id, external_event_id)`. A matching retry
with the same raw-body digest returns the original ticket/message result and includes
`X-SupportFlow-Duplicate: true`. Reusing an event ID with different bytes returns
`409 event_id_reused`. Concurrent exact retries wait on the unique constraint; one
transaction creates the domain operation and commits its receipt atomically. If the
process or database transaction fails before commit, neither is visible, and a retry
can safely execute. A valid signed operation rejected by ticket/contact/workflow rules
records a rejected receipt and repeats return the same safe rejection. Bad signatures,
stale timestamps, malformed JSON before event identification, and oversized requests
are rejected before a receipt is written.

Common outcomes are `401 invalid_signature` / `signature_expired`, `400 invalid_json`,
`413 payload_too_large`, `409 event_id_reused`, `404 not_found` for a ticket outside the
source's authorized contact scope, and `422 invalid_transition` or `invalid_payload`.
Errors use the standard safe response envelope and never include credentials, the
signature, or message body.

Owners and Administrators can inspect paginated receipt metadata in the Integrations
screen or request:

```text
GET /api/organizations/{organization_id}/integrations/receipts?page=1&page_size=20
```

Metadata includes source, event ID, SHA-256 payload digest, timestamps, status, target
IDs and a safe error category. Raw payloads and signature material are not retained.

## Local manual test

Start the local Docker Compose stack using the README instructions, create an Owner,
organization and contact, then create a source in **Integrations**. Save the one-time
credential and follow the Python sender above after replacing its placeholders. Confirm
the new ticket appears in the queue and its receipt is marked processed. Repeat the
same request with the same event ID and body: it must return the same ticket ID. Change
the body without changing the event ID: it must return 409. Rotate the key and verify
the old signature fails. Never paste a real credential into shell history or commit a
local script containing it.
