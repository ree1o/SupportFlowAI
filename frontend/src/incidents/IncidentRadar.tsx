import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Member, type Organization } from "../api";
import { ErrorText, message } from "../auth";
import { useRemote } from "../support/remote";
import type { Page, Ticket } from "../support/types";

type TicketRef = Pick<Ticket, "id" | "title" | "status" | "priority" | "category_id" | "category_name" | "requester_name" | "created_at">;
type Evidence = {
  detector_version: string;
  rule: string;
  threshold: number;
  shared_terms: string[];
  category_matches: { name: string; ticket_count: number }[];
  ticket_count: number;
  distinct_contact_count: number;
  window_minutes: number;
  observed_span_minutes: number;
  ticket_refs: string[];
  interpretation: string;
};
type Proposal = {
  id: string;
  title: string;
  summary: string;
  score: number;
  status: "pending" | "confirmed" | "rejected";
  evidence: Evidence;
  ticket_ids: string[];
  tickets: TicketRef[];
};
type ProposalPage = Page<Proposal>;
type RadarRun = {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  attempts: number;
  proposal_count: number;
  failure_type: string | null;
  created_at: string;
  finished_at: string | null;
};
type RunPage = Page<RadarRun>;
type Incident = {
  id: string;
  title: string;
  description: string;
  status: "investigating" | "monitoring" | "resolved";
  severity: "sev1" | "sev2" | "sev3" | "sev4";
  owner_membership_id: string | null;
  owner_name: string | null;
  version: number;
  ticket_count: number;
  created_at: string;
  resolved_at: string | null;
};
type IncidentEvent = {
  id: string;
  actor_name: string;
  action: string;
  note: string | null;
  changes: Record<string, unknown>;
  created_at: string;
};
type IncidentDetail = Incident & {
  tickets: (TicketRef & { linked_at: string })[];
  events: IncidentEvent[];
};
type IncidentPage = Page<Incident>;
type ReviewResult = { proposal: Proposal; incident: Incident | null };

const severityValues = ["sev1", "sev2", "sev3", "sev4"] as const;
const incidentStates = ["investigating", "monitoring", "resolved"] as const;

export function IncidentRadar({ org }: { org: Organization }) {
  const base = `/organizations/${org.id}`;
  const canManage = org.role === "owner" || org.role === "administrator";
  const proposals = useRemote<ProposalPage>(`${base}/radar/proposals?status=pending&page_size=50`);
  const incidents = useRemote<IncidentPage>(`${base}/incidents?page_size=50`);
  const runs = useRemote<RunPage>(`${base}/radar/runs?page_size=10`);
  const members = useRemote<Member[]>(`${base}/memberships`);
  const tickets = useRemote<Page<Ticket>>(`${base}/tickets?page=1&page_size=100`);
  const [selected, setSelected] = useState("");
  const detail = useRemote<IncidentDetail>(selected ? `${base}/incidents/${selected}` : null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedTickets, setSelectedTickets] = useState<Record<string, string[]>>({});

  function reload() {
    proposals.reload();
    incidents.reload();
    runs.reload();
    if (selected) detail.reload();
  }

  async function mutate(action: () => Promise<unknown>, success: string) {
    setWorking(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(success);
      reload();
    } catch (err) {
      setError(err instanceof Error && "status" in err && err.status === 409
        ? "This incident or proposal changed while you were working. Reload the current record and review it again."
        : message(err));
    } finally {
      setWorking(false);
    }
  }

  function proposalSelection(proposal: Proposal) {
    return selectedTickets[proposal.id] ?? proposal.ticket_ids;
  }

  async function confirm(proposal: Proposal, form: HTMLFormElement) {
    const data = new FormData(form);
    const kept = proposalSelection(proposal);
    if (kept.length < 2) {
      setError("Keep at least two tickets to confirm an incident.");
      return;
    }
    const title = String(data.get("title"));
    if (!window.confirm(`Create incident “${title}” with ${kept.length} selected tickets?`)) return;
    await mutate(
      () => api<ReviewResult>(`${base}/radar/proposals/${proposal.id}/confirm`, "POST", {
        ticket_ids: kept,
        title: String(data.get("title")),
        description: String(data.get("description")),
        severity: String(data.get("severity")),
      }),
      "Proposal confirmed and incident created.",
    );
  }

  async function reject(proposal: Proposal) {
    if (!window.confirm("Reject this candidate group? The same ticket evidence will be suppressed.")) return;
    const reason = window.prompt("Optional review note") ?? "";
    await mutate(
      () => api(`${base}/radar/proposals/${proposal.id}/reject`, "POST", { reason }),
      "Proposal rejected. It will not reappear unless its evidence materially changes.",
    );
  }

  async function createManual(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const ticketIds = Array.from((form.elements.namedItem("ticket_ids") as HTMLSelectElement).selectedOptions).map((option) => option.value);
    if (!ticketIds.length) {
      setError("Select at least one ticket for the incident.");
      return;
    }
    await mutate(
      () => api<IncidentDetail>(`${base}/incidents`, "POST", {
        title: String(data.get("title")),
        description: String(data.get("description")),
        severity: String(data.get("severity")),
        owner_membership_id: String(data.get("owner_membership_id")) || null,
        ticket_ids: ticketIds,
      }),
      "Incident created.",
    );
    form.reset();
  }

  const currentDetail = detail.data;
  return (
    <>
      <div className="page-heading incident-page-heading">
        <div>
          <p className="eyebrow">{org.name} · Operations intelligence</p>
          <h1>Incident Radar</h1>
          <p className="muted">Review repeated customer symptoms. A proposal is evidence for human review, not a root-cause finding.</p>
        </div>
        <button disabled={working} onClick={() => void mutate(() => api(`${base}/radar/runs`, "POST"), "Detection run queued.")}>Run detection</button>
      </div>
      <ErrorText text={error || proposals.error || incidents.error || runs.error} />
      {notice && <p className="success-banner" role="status">{notice}</p>}

      <section className="radar-run-strip" aria-label="Recent detection runs">
        {(runs.data?.items ?? []).slice(0, 3).map((run) => (
          <article className={`radar-run radar-${run.status}`} key={run.id}>
            <span className="radar-dot" aria-hidden="true" />
            <span><strong>{run.status}</strong><small>{new Date(run.created_at).toLocaleString()}</small></span>
            <span>{run.status === "completed" ? `${run.proposal_count} new proposals` : run.failure_type ?? `Attempt ${run.attempts}`}</span>
            {run.status === "failed" && canManage && <button className="text-button" onClick={() => void mutate(() => api(`${base}/radar/runs/${run.id}/retry`, "POST"), "Retry queued.")}>Retry</button>}
          </article>
        ))}
        {runs.loading && <p aria-busy="true">Loading detection runs…</p>}
        {!runs.loading && !runs.data?.items.length && <p className="muted">No detection runs yet.</p>}
      </section>

      <div className="radar-layout">
        <div className="radar-main-column">
          <section className="panel">
            <div className="section-heading"><div><p className="eyebrow">Human review required</p><h2>Pending proposals <span className="count-pill">{proposals.data?.total ?? "—"}</span></h2></div><button className="secondary" onClick={reload}>Refresh</button></div>
            {proposals.loading ? <p aria-busy="true">Finding proposals…</p> : proposals.data?.items.length ? (
              <div className="proposal-list">
                {proposals.data.items.map((proposal) => (
                  <article className="proposal-card" key={proposal.id}>
                    <div className="proposal-card-heading">
                      <div><span className="proposal-label">Possible correlation</span><h3>{proposal.title}</h3><p>{proposal.summary}</p></div>
                      <div className="relevance" aria-label={`Deterministic relevance ${Math.round(proposal.score * 100)} percent`}><strong>{Math.round(proposal.score * 100)}%</strong><small>relevance</small></div>
                    </div>
                    <div className="evidence-box">
                      <strong>Why these tickets were grouped</strong>
                      <p>Shared terms: {proposal.evidence.shared_terms.length ? proposal.evidence.shared_terms.map((term) => <span className="term-chip" key={term}>{term}</span>) : <span>none</span>}</p>
                      {proposal.evidence.category_matches.length > 0 && <p>Category overlap: {proposal.evidence.category_matches.map((item) => `${item.name} (${item.ticket_count})`).join(", ")}</p>}
                      <p>{proposal.evidence.ticket_count} tickets from {proposal.evidence.distinct_contact_count} customers in {proposal.evidence.observed_span_minutes} minutes · {proposal.evidence.detector_version}</p>
                      <small>{proposal.evidence.interpretation}</small>
                    </div>
                    <form className="proposal-review" onSubmit={(event) => { event.preventDefault(); void confirm(proposal, event.currentTarget); }}>
                      <fieldset>
                        <legend>Review ticket group</legend>
                        {proposal.tickets.map((ticket) => {
                          const checked = proposalSelection(proposal).includes(ticket.id);
                          return <label className="proposal-ticket" key={ticket.id}>
                            <input type="checkbox" checked={checked} onChange={(event) => setSelectedTickets((values) => ({ ...values, [proposal.id]: event.target.checked ? [...proposalSelection(proposal), ticket.id] : proposalSelection(proposal).filter((id) => id !== ticket.id) }))} />
                            <span><Link to={`/app/tickets/${ticket.id}`}>{ticket.title}</Link><small>{ticket.requester_name} · {ticket.category_name ?? "Uncategorized"} · {new Date(ticket.created_at).toLocaleString()}</small></span>
                          </label>;
                        })}
                      </fieldset>
                      {canManage && <div className="proposal-edit-fields">
                        <label>Incident title<input name="title" required maxLength={200} defaultValue={proposal.title} /></label>
                        <label>Severity<select name="severity" defaultValue="sev3">{severityValues.map((value) => <option key={value}>{value}</option>)}</select></label>
                        <label className="proposal-description">Investigation summary<textarea name="description" maxLength={5000} defaultValue={proposal.summary} /></label>
                      </div>}
                      <div className="proposal-actions">
                        {canManage ? <>
                          <button disabled={working || proposalSelection(proposal).length < 2}>Confirm incident</button>
                          <button className="secondary danger-button" type="button" disabled={working} onClick={() => void reject(proposal)}>Reject proposal</button>
                        </> : <p className="muted">An Owner or Administrator reviews and confirms proposals.</p>}
                      </div>
                    </form>
                  </article>
                ))}
              </div>
            ) : <div className="empty-state"><span aria-hidden="true">✓</span><h3>No pending proposals</h3><p>Run Radar after new tickets arrive. Existing incidents remain available below.</p></div>}
          </section>

          {canManage && <section className="panel">
            <h2>Create a manual incident</h2>
            <p className="muted">Use this when staff have confirmed a shared issue without waiting for a Radar proposal.</p>
            <ErrorText text={tickets.error} />
            <form className="incident-create-form" onSubmit={(event) => void createManual(event)}>
              <label>Title<input name="title" required maxLength={200} /></label>
              <label>Description<textarea name="description" maxLength={5000} /></label>
              <div className="incident-form-row">
                <label>Severity<select name="severity" defaultValue="sev3">{severityValues.map((value) => <option key={value}>{value}</option>)}</select></label>
                <label>Incident owner<select name="owner_membership_id"><option value="">Unassigned</option>{members.data?.filter((member) => member.is_active).map((member) => <option key={member.id} value={member.id}>{member.full_name} · {member.role}</option>)}</select></label>
              </div>
              <label>Link ticket(s)<select name="ticket_ids" multiple size={Math.min(6, Math.max(3, tickets.data?.items.length ?? 3))} disabled={tickets.loading}>
                {tickets.data?.items.map((ticket) => <option value={ticket.id} key={ticket.id}>{ticket.title} · {ticket.requester_name} · {ticket.status}</option>)}
              </select></label>
              <small className="muted">Use Ctrl or Command to select multiple tickets. At least one is required.</small>
              <button disabled={working || tickets.loading}>{working ? "Creating…" : "Create incident"}</button>
            </form>
          </section>}
        </div>

        <aside className="radar-side-column">
          <section className="panel">
            <div className="section-heading"><div><p className="eyebrow">Organization incidents</p><h2>Active and resolved</h2></div></div>
            {incidents.loading ? <p aria-busy="true">Loading incidents…</p> : incidents.data?.items.length ? <div className="incident-list">
              {incidents.data.items.map((incident) => <button className={`incident-list-row ${incident.id === selected ? "is-selected" : ""}`} key={incident.id} onClick={() => setSelected(incident.id)}>
                <span className={`severity-mark ${incident.severity}`}>{incident.severity}</span><span className="incident-row-copy"><strong>{incident.title}</strong><small>{incident.status} · {incident.ticket_count} tickets</small></span>
              </button>)}
            </div> : <p className="muted">No incidents have been confirmed.</p>}
          </section>
          {selected && <IncidentDetails
            base={base}
            detail={currentDetail}
            loading={detail.loading}
            error={detail.error}
            canManage={canManage}
            members={members.data ?? []}
            tickets={tickets.data?.items ?? []}
            working={working}
            onMutate={mutate}
          />}
        </aside>
      </div>
      <p className="radar-footnote">Detector: deterministic lexical-v1 · evidence is based on saved ticket data · No content is sent to an external provider.</p>
    </>
  );
}

function IncidentDetails({
  base, detail, loading, error, canManage, members, tickets, working, onMutate,
}: {
  base: string;
  detail?: IncidentDetail;
  loading: boolean;
  error: string;
  canManage: boolean;
  members: Member[];
  tickets: Ticket[];
  working: boolean;
  onMutate: (action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  if (loading) return <section className="panel"><p aria-busy="true">Loading incident detail…</p></section>;
  if (error || !detail) return <section className="panel"><ErrorText text={error || "Incident is unavailable."} /></section>;
  const currentIncident = detail as IncidentDetail;

  async function update(values: Record<string, unknown>) {
    await onMutate(() => api(`${base}/incidents/${currentIncident.id}`, "PATCH", { expected_version: currentIncident.version, ...values }), "Incident updated.");
  }
  async function addNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = note;
    await onMutate(() => api(`${base}/incidents/${currentIncident.id}/notes`, "POST", { expected_version: currentIncident.version, body: value }), "Investigation note added.");
    setNote("");
  }
  async function link(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const ticketId = String(new FormData(event.currentTarget).get("ticket_id"));
    if (!ticketId) return;
    await onMutate(() => api(`${base}/incidents/${currentIncident.id}/tickets`, "POST", { expected_version: currentIncident.version, ticket_id: ticketId }), "Ticket linked to incident.");
  }
  return <section className="panel incident-detail-panel">
    <div className="section-heading"><div><p className="eyebrow">Incident detail · v{currentIncident.version}</p><h2>{currentIncident.title}</h2></div><span className={`severity-mark ${currentIncident.severity}`}>{currentIncident.severity}</span></div>
    <p>{currentIncident.description || "No investigation summary provided."}</p>
    <div className="incident-controls">
      <label>Status<select disabled={!canManage || working} value={currentIncident.status} onChange={(event) => void update({ status: event.target.value })}>{incidentStates.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Severity<select disabled={!canManage || working} value={currentIncident.severity} onChange={(event) => void update({ severity: event.target.value })}>{severityValues.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Owner<select disabled={!canManage || working} value={currentIncident.owner_membership_id ?? ""} onChange={(event) => void update({ owner_membership_id: event.target.value || null })}><option value="">Unassigned</option>{members.filter((member) => member.is_active).map((member) => <option key={member.id} value={member.id}>{member.full_name}</option>)}</select></label>
    </div>
    <h3>Linked tickets ({detail.tickets.length})</h3>
    <ul className="linked-ticket-list">{currentIncident.tickets.map((ticket) => <li key={ticket.id}>
      <span><Link to={`/app/tickets/${ticket.id}`}>{ticket.title}</Link><small>{ticket.requester_name} · {ticket.status} · {ticket.priority}</small></span>
      {canManage && currentIncident.tickets.length > 1 && <button className="text-button danger-text" disabled={working} onClick={() => void onMutate(() => api(`${base}/incidents/${currentIncident.id}/tickets/${ticket.id}?expected_version=${currentIncident.version}`, "DELETE"), "Ticket unlinked.")}>Unlink</button>}
    </li>)}</ul>
    {canManage && currentIncident.status !== "resolved" && tickets.some((ticket) => !currentIncident.tickets.some((linked) => linked.id === ticket.id)) && <form className="incident-link-form" onSubmit={(event) => void link(event)}>
      <label>Add ticket<select name="ticket_id" defaultValue=""><option value="" disabled>Select ticket</option>{tickets.filter((ticket) => !currentIncident.tickets.some((linked) => linked.id === ticket.id)).map((ticket) => <option value={ticket.id} key={ticket.id}>{ticket.title}</option>)}</select></label><button className="secondary" disabled={working}>Link</button>
    </form>}
    <h3>Investigation history</h3>
    <ol className="incident-timeline">{currentIncident.events.map((event) => <li key={event.id}><span className="timeline-marker" /><div><strong>{event.note ? "Internal investigation note" : event.action.replaceAll(".", " ")}</strong><small>{event.actor_name} · {new Date(event.created_at).toLocaleString()}</small>{event.note && <p>{event.note}</p>}</div></li>)}</ol>
    <form className="incident-note-form" onSubmit={(event) => void addNote(event)}>
      <label htmlFor="incident-note">Add internal investigation note</label><textarea id="incident-note" value={note} onChange={(event) => setNote(event.target.value)} maxLength={5000} required /><button disabled={working || !note.trim()}>Add note</button>
    </form>
  </section>;
}
