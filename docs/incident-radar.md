# Manual incidents and deterministic Incident Radar (M7)

Incident Radar finds groups of recent tickets whose stored title and initial public message have repeatable lexical signals. A proposal is a lead for staff to review; it does not establish that the tickets share a root cause. It never changes ticket status, SLA commitments, or customer conversations.

## Lifecycle and permissions

Incidents have `investigating`, `monitoring`, and `resolved` states. Allowed transitions are `investigating -> monitoring|resolved`, `monitoring -> investigating|resolved`, and `resolved -> investigating` (reopen). Severity is `sev1` through `sev4`. Changes require the current incident version; stale writes return 409. Every mutation and internal note is appended to activity history in the same transaction.

| Action | Owner | Administrator | Agent | Customer |
| --- | --- | --- | --- | --- |
| View organization incidents, proposals, and evidence | Yes | Yes | Yes | No |
| Queue or retry a detection run | Yes | Yes | Yes | No |
| Create or edit a confirmed incident | Yes | Yes | No | No |
| Link/unlink incident tickets | Yes | Yes | No | No |
| Confirm/reject proposals | Yes | Yes | No | No |
| Add internal investigation notes | Yes | Yes | Yes | No |

Every API call checks active organization membership. Composite foreign keys bind incidents, proposals, runs, memberships, and ticket links to one organization. Customer APIs have no incident routes or internal incident data.

An incident requires at least one ticket. Linking/unlinking never mutates tickets. A resolved incident must be reopened before adding tickets. The final linked ticket cannot be removed.

## Detector (`lexical-v1`)

Runs inspect at most the newest 500 non-closed tickets per organization from the previous 72 hours. One bounded query reads each ticket, category, requester reference, and initial public message. Inverted token postings limit pair generation to 50,000 pairs. A frequent term appearing in more than `max(3, 25% of the window)` is ignored. Groups contain at most eight tickets and need at least two distinct contacts.

Text is Unicode NFKC normalized and case-folded. The baseline removes common English, Spanish, German, and Russian generic terms and words shorter than three characters. There is no stemming or translation. Each pair must fall within 360 minutes, share at least two significant terms, and have title/body Jaccard similarity >= 0.30, or share a category and at least two title terms. Pair score is `0.65 * Jaccard + 0.20 * category_match + 0.15 * temporal_proximity`, rounded to four decimals. Temporal proximity is `1 - seconds_apart / configured_window`. Groups use complete-link membership (every pair qualifies), need a term in at least half the group, and distinct contacts. Category/time alone never creates a group. Validated `SUPPORTFLOW_RADAR_*` settings configure thresholds/bounds; each run records its detector version.

Evidence stores shared terms, category matches, ticket IDs and timestamps, pair signal contributions, time span, and detector version. It does not copy ticket bodies or claim root cause. Evidence remains understandable if ticket text later changes.

## Review and suppression

Owners and Administrators can edit the proposed title/summary, deselect false-positive tickets (at least two must remain), and confirm. Incident creation, selected links, proposal review state, activity events, and suppression state commit atomically. Rejecting records reviewer, time, optional note, stable group identity, and input fingerprint. The unique organization/group/fingerprint constraint prevents duplicate proposals across repeated or concurrent scans. The fingerprint covers ticket IDs, significant terms, categories, and creation times; changed evidence can be reconsidered. Groups already linked together to one confirmed incident are omitted. A reviewed proposal cannot be reviewed again.

## Background work and recovery

The existing Celery/Redis worker and Beat scheduler are reused. A manual request commits a pending run before dispatch. Beat schedules eligible organizations every 15 minutes; recovery every 30 seconds redispatches available pending or expired leases. Each worker claims a run with a short lease, computes from a bounded snapshot outside a long transaction, then inserts proposals idempotently and marks the run complete. Failures store only the exception class, retry with bounded backoff, and become visible after five attempts for manager retry. Redis downtime does not erase PostgreSQL run state.

## Evaluation

`backend/tests/fixtures/radar_pairs.json` is a small synthetic set of labeled related and unrelated pairs. Its deterministic regression test compares lexical-v1 with a baseline accepting one shared significant term:

| Method | TP | FP | FN | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| lexical-v1 | 3 | 0 | 1 | 1.00 | 0.75 |
| One shared term baseline | 4 | 2 | 0 | 0.67 | 1.00 |

These results cover only eight synthetic pairs, not production accuracy. The missed positive is a Spanish paraphrase without enough shared words, demonstrating the lack of stemming, synonyms, and translation. Future evaluation should use reviewed tenant-safe examples split by incident and time, and report precision/recall, false proposals, and latency.

## Reproducible demo

1. Register an organization Owner and create three distinct customer contacts.
2. Create two tickets describing the same specific payment/checkout timeout from two contacts, then one unrelated password-reset ticket.
3. Open **Incident Radar**, run detection, and inspect terms/category/time evidence. The unrelated ticket should be absent.
4. Keep both related tickets, edit the incident title, and confirm. The incident timeline records the review; linked ticket states and SLA deadlines remain unchanged.
5. Create another obvious pair, reject its proposal, and rerun. The unchanged evidence stays suppressed.
6. Sign in as an Agent to add an investigation note; verify management controls are unavailable. Switch organizations and verify isolation.

Limits: lexical overlap misses paraphrases, multilingual morphology, and delayed reports; this version uses initial public messages, excludes closed or older tickets, and caps each run. Detection is bounded and explainable, not real-time or a root-cause system. Embeddings are a separate M8 decision.
