# SLA and operational reporting

M5 adds one organization-level SLA policy. Owners and Administrators can create and
edit it; Agents can view it. Every actual policy change appends an immutable version,
including target, activation, and name changes. Activation and deactivation affect future
ticket creation only.
Every new ticket created while a policy is active gets an immutable target snapshot
containing the policy version, creation priority, durations, and UTC deadlines. M1-M4
tickets and tickets created while no active policy exists have no snapshot and are not
assigned inferred SLA outcomes.

Targets use continuous elapsed UTC time (24 hours per day, seven days per week). Waiting
status does not pause a deadline. Priority changes do not change a ticket's snapshot.
First response is the first public human staff reply; intake text recorded by staff,
customer replies, internal notes, and automated notifications do not count. First
resolution is the first transition to `resolved`; reopening clears the current
`resolved_at` but retains `first_resolved_at` and does not restart the SLA cycle.
An event at exactly its deadline is on time. A missing event is overdue only when
`now > deadline`. An event recorded after its deadline is immediately marked late.
A ticket resolved without a staff reply leaves the response obligation outstanding and
that obligation can breach.

The Celery Beat worker scans due outstanding obligations once per minute. PostgreSQL
row locks with `SKIP LOCKED` serialize competing scans; breach timestamps and stable
in-app notification/outbox keys make retries idempotent. The scanner sets inferred
breach time to the deadline, so worker downtime does not hide historical lateness. It
notifies active Owners and Administrators plus the assigned active agent, if any. Email
delivery uses the existing M4 outbox worker, separate from ticket transactions.

## Report definitions

The API accepts an explicit UTC `[period_start, period_end)` interval up to one year.
Created/status/priority counts and average response/resolution durations include tickets
created in this interval. Status and priority distributions use current values. Average
durations include only tickets with the corresponding first event. The unresolved cohort
count includes current `open` and `pending` tickets created in the interval.

SLA compliance includes only tickets in that cohort with an M5 ticket snapshot. The
response denominator includes tickets with a first staff response or a response deadline
strictly before report `as_of`; the numerator is responses at or before the snapshotted
deadline. Resolution uses the corresponding first-resolution event and deadline. Open,
not-yet-due obligations are excluded, never treated as success. Missing events after a
passed deadline are denominator failures. Tickets without snapshots are excluded.
Currently overdue counts outstanding response or resolution obligations across all
snapshotted tickets at `as_of`; completed late obligations are historical failures but
are not currently overdue. Workload is a current count of assigned open/pending tickets
per active staff membership, not an employee ranking.

## API

- `GET /api/organizations/{organization_id}/sla/policy`
- `PUT /api/organizations/{organization_id}/sla/policy`
- `GET /api/organizations/{organization_id}/reports/operations?period_start=...&period_end=...`

The ticket list/detail response includes a read-only optional `sla` projection. Old
tickets return `null`.
