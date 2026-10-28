# M2 — Identity and tenant boundaries

M2 adds global accounts, server-side sessions, organizations and staff memberships.
Identity owns four business tables. [M3](tickets.md) adds five helpdesk tables and
staff ticket workflows. RLS, email verification/invitations, password recovery,
customer access and AI remain later work.

## API contract

All paths are under `/api`. Responses use typed OpenAPI models and the existing safe
error envelope with a request ID. The cookie scheme is documented in OpenAPI.

| Method | Path | Access / purpose |
| --- | --- | --- |
| GET | `/auth/csrf` | Get an anonymous or session-bound CSRF token |
| POST | `/auth/register` | Create account + organization + owner + new session atomically |
| POST | `/auth/login` | Validate credentials, rehash if needed, issue a fresh session |
| GET | `/auth/me` | Current profile, active memberships, selection and expiry |
| POST | `/auth/logout` | Revoke current session and clear cookies |
| POST | `/auth/renew` | Rotate token, revoke old session, extend within absolute lifetime |
| GET | `/organizations` | List only this account's active organizations |
| POST | `/organizations` | Authenticated account creates another organization as owner |
| GET | `/organizations/{id}` | Any active member |
| PATCH | `/organizations/{id}` | Owner / Administrator; update name, slug stays immutable |
| POST | `/organizations/{id}/select` | Verify membership and persist this session's selection |
| GET | `/organizations/{id}/memberships` | Any active member; includes disabled memberships |
| POST | `/organizations/{id}/memberships` | Add an existing active account by normalized email |
| PATCH | `/organizations/{id}/memberships/{membership_id}` | Change role and active state under role policy |
| DELETE | `/organizations/{id}/memberships/{membership_id}` | Soft-remove membership; same permissions and owner checks |

All POST/PATCH/DELETE operations require an exact allowed `Origin` and `X-CSRF-Token`.
This includes registration/login: protecting only authenticated operations would leave
login CSRF possible. Non-browser clients must also send Origin explicitly.

Signup JSON:

```json
{
  "email": "person@example.com",
  "password": "a unique long passphrase",
  "full_name": "Example Person",
  "organization": {"name": "Example Company", "slug": "example-company"}
}
```

Do not reuse documentation passphrases. Requests forbid unknown fields: signup cannot
inject a role, organization ID, or another user's identity. Passwords are 12–128
characters and are not trimmed. Emails are validated, stripped, normalized to ASCII
domain form and lowercase, then protected by database uniqueness and a lowercase check.
Internationalized local parts are deliberately unsupported initially.

## Session and CSRF lifecycle

- Generate 256 bits of randomness; only a SHA-256 digest is stored in PostgreSQL.
- Host-only `supportflow_session` cookie, `HttpOnly`, `SameSite=Lax`, path `/api`.
- Secure cookies are the application default. Local HTTP requires the explicit
  `SUPPORTFLOW_COOKIE_SECURE=false` development setting (Compose sets it for local use).
- Production rejects insecure cookies and non-HTTPS trusted origins. Origins are exact
  strings, with no wildcard, path, credentials or inferred Host-header trust.
- Default lifetime is eight hours, with a seven-day absolute limit. Renewal is explicit
  through the UI/API, creates a fresh token and revokes the old record in one transaction.
  Renewal cannot move the original absolute deadline. GET requests do not silently extend sessions.
- Login and successful onboarding replace an existing cookie session. Logout revokes
  the database record. Invalid/expired/revoked sessions or disabled users get 401 and
  cookie cleanup; membership rights are always checked against current database state.
- Authenticated CSRF tokens are HMAC-SHA256 derived from the random session token with
  a fixed purpose string. They are returned by `/auth/csrf`; the raw session token is
  never returned in JSON. Rotation invalidates the previous CSRF token too.
- Anonymous CSRF uses an independent random 15-minute HttpOnly bootstrap cookie plus
  the response/header token. Exact Origin validation and the same-origin policy are
  mandatory additional controls; there is no permissive CORS configuration.
- CSRF header comparison is constant-time. The frontend fetches a fresh CSRF value
  before mutations and keeps it only for the request, not localStorage.

Session selection is a convenience, not authorization. Every organization URL checks
the caller's active membership independently. Removing access makes the selected
organization disappear from `/auth/me`; it never grants access through a stale cookie.
Multiple sessions have independent selections. Concurrent renewal of one cookie may
reject the losing request; the UI offers explicit renewal rather than silent concurrent refreshes.

## Permissions

| Operation | Owner | Administrator | Agent |
| --- | --- | --- | --- |
| View organization and member list | Yes | Yes | Yes |
| Select an organization they belong to | Yes | Yes | Yes |
| Create a separate organization | Yes | Yes | Yes |
| Rename organization | Yes | Yes | No |
| Add Agent | Yes | Yes | No |
| Add Owner / Administrator | Yes | No | No |
| Change roles | Yes | No | No |
| Disable / remove / reactivate Agent | Yes | Yes | No |
| Disable / remove / reactivate Owner or Administrator | Yes | No | No |
| Demote or disable final active Owner | No | No | No |

Owners can demote/disable themselves only when another active owner remains. This is
an authorized ownership transfer, not self-escalation. Administrators cannot promote
themselves or any Agent. Agents cannot mutate memberships. Frontend affordances mirror
these rules; the service layer remains authoritative.

All membership mutations acquire the organization row lock, refresh actor permissions,
then inspect/update the scoped target. The final-owner count and mutation happen in
the same transaction. This serializes conflicting owner changes and rejects stale
administrator authority after waiting for a lock. Out-of-band privileged SQL changes
can bypass these application invariants and are not an administrative API.

## Database and migration behavior

Migration `0002_identity` upgrades `0001_baseline` without dropping or modifying existing
tables. UUID PKs, timezone-aware timestamps, foreign keys and unique indexes reinforce
identity. `(organization_id, user_id)` is unique per membership. Sessions reference
that pair for their selected organization, preventing selections without a membership
even through an accidental direct database write. A digest-format check rejects raw
session tokens. Membership removal is a soft disable, preserving that relationship.

Tests use a new random schema per test inside an explicitly configured disposable
PostgreSQL database ending `_test`. Upgrade/downgrade tests and schema cleanup affect
only that test's schema. There is no application startup `create_all` or destructive
database reset. Application authorization is in place; advanced RLS remains release 1.0.

## Manual browser demonstration

1. Start the updated Compose stack and open `http://localhost:5173/register`.
2. Register an account and organization. The ticket queue opens automatically;
   choose **Organization & team** for the management screens below.
3. Create another workspace; switch between them in the selector.
4. In a private browser window, register a second account with its own organization.
5. In the first window, add the second account's email as Agent. Refresh the second
   window and select the shared organization; management controls are absent.
6. Change that membership to Administrator from the owner window; refresh the second
   window to see Agent-management controls. It still cannot grant Owner/Administrator.
7. Disable the second membership. Its next request is denied; selection clears on refresh.
8. Try demoting/disabling the sole owner; expect a clear final-owner conflict.
9. Renew the session, sign out, and sign in again. An expired/invalid session returns
   the frontend to login on its next authenticated request.
10. Open `/status` to verify the preserved M1 health UI.

## Known limits

Email ownership verification, invitations, recovery, MFA, session inventory/revoke-all,
distributed rate limiting, expired-session retention cleanup and RLS are not in M2.
Adding an existing account is an explicit manager decision; no email address is claimed
to be verified. Use synthetic accounts until later onboarding/security hardening.
There is no global account directory or user-disable API; disabled-user checks support
administrative database intervention, which must preserve organizational ownership.
This local Compose setup remains an HTTP development environment, not a public HTTPS deployment.

## References

- [Argon2id hashing and rehashing](https://argon2-cffi.readthedocs.io/en/stable/howto.html)
- [CSRF prevention and Origin controls](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
