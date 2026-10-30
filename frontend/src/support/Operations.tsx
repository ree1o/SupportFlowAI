import { useState, type FormEvent } from "react";
import { api, type Organization } from "../api";
import { ErrorText, message } from "../auth";
import { useRemote } from "./remote";

type Policy = {
  id: string;
  active: boolean;
  name: string;
  current_version: number;
  targets: Record<string, number>;
};
type Report = {
  tickets_created: number;
  status_counts: Record<string, number>;
  priority_counts: Record<string, number>;
  unresolved_tickets: number;
  average_first_response_seconds: number | null;
  average_first_resolution_seconds: number | null;
  response_sla_compliance_rate: number | null;
  resolution_sla_compliance_rate: number | null;
  currently_overdue_tickets: number;
  workload: { membership_id: string; agent_name: string; open_tickets: number }[];
};
type Notice = { id: string; ticket_id: string; title: string; created_at: string; read_at: string | null };
const minutes = (value: number | undefined, fallbackHours: number) =>
  Math.max(1, Math.round((value ?? fallbackHours * 3600) / 60));

export function Operations({ org }: { org: Organization }) {
  const base = `/organizations/${org.id}`;
  const policy = useRemote<Policy | null>(`${base}/sla/policy`);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const query = new URLSearchParams({
    period_start: start.toISOString(),
    period_end: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1)).toISOString(),
  });
  const report = useRemote<Report>(`${base}/reports/operations?${query}&r=${refresh}`);
  const notices = useRemote<Notice[]>(`${base}/notifications?r=${refresh}`);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const body: Record<string, unknown> = {
      name: String(values.get("name")),
      active: values.get("active") === "on",
      ...(policy.data ? { expected_version: policy.data.current_version } : {}),
    };
    for (const priority of ["low", "normal", "high", "urgent"])
      for (const kind of ["response", "resolution"])
        body[`${priority}_${kind}_seconds`] = Number(values.get(`${priority}_${kind}_minutes`)) * 60;
    setBusy(true);
    setError("");
    try {
      await api<Policy>(`${base}/sla/policy`, "PUT", body);
      policy.reload();
      setRefresh((value) => value + 1);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  const metric = (value: number | null) =>
    value === null ? "No eligible tickets" : `${Math.round(value * 100)}%`;
  async function markRead(id: string) {
    try {
      await api<void>(`${base}/notifications/${id}/read`, "POST");
      notices.reload();
    } catch (err) {
      setError(message(err));
    }
  }
  return (
    <>
      <div className="page-heading">
        <div><p className="eyebrow">{org.name} · Operations</p><h1>SLA & operations</h1>
          <p className="muted">Continuous UTC targets. Waiting tickets continue to accrue time.</p></div>
      </div>
      <ErrorText text={error || policy.error || report.error} />
      <section className="panel">
        <h2>SLA policy</h2>
        {policy.loading ? <p aria-busy="true">Loading policy…</p> : org.role === "agent" ? (
          policy.data ? <p>{policy.data.name} · Version {policy.data.current_version} · {policy.data.active ? "Active" : "Inactive"}</p> : <p>No SLA policy is configured.</p>
        ) : (
          <form className="sla-form" onSubmit={(event) => void save(event)}>
            <label>Policy name<input name="name" required maxLength={100} defaultValue={policy.data?.name ?? "Default SLA"} /></label>
            <label className="checkbox-label"><input type="checkbox" name="active" defaultChecked={policy.data?.active ?? true} /> Apply to new tickets</label>
            <p className="muted">{policy.data ? `Policy version ${policy.data.current_version}` : "New policy · version 1"}</p>
            <div className="table-scroll"><table><thead><tr><th>Priority</th><th>First response (minutes)</th><th>First resolution (minutes)</th></tr></thead><tbody>
              {(["low", "normal", "high", "urgent"] as const).map((level) => <tr key={level}><th>{level}</th>
                {(["response", "resolution"] as const).map((kind) => <td key={kind}><label className="sr-only" htmlFor={`${level}-${kind}`}>{level} {kind} minutes</label><input id={`${level}-${kind}`} name={`${level}_${kind}_minutes`} type="number" min={1} max={525600} required defaultValue={minutes(policy.data?.targets[`${level}_${kind}_seconds`], kind === "response" ? 4 : 24)} /></td>)}
              </tr>)}
            </tbody></table></div>
            <button disabled={busy || policy.loading}>{busy ? "Saving…" : policy.data ? "Save policy" : "Create policy"}</button>
          </form>
        )}
      </section>
      <section className="panel">
        <h2>Staff notifications</h2>
        <ErrorText text={notices.error} />
        {notices.loading ? <p aria-busy="true">Loading notifications…</p> : notices.data?.length ? (
          <ul className="staff-notices">{notices.data.map((notice) => <li key={notice.id}>
            <span><strong>{notice.title}</strong><br /><a href={`/app/tickets/${notice.ticket_id}`}>Open ticket</a> · {new Date(notice.created_at).toLocaleString()}</span>
            {!notice.read_at && <button className="secondary" onClick={() => void markRead(notice.id)}>Mark read</button>}
          </li>)}</ul>
        ) : <p>No staff notifications.</p>}
      </section>
      <section className="panel">
        <div className="section-heading"><div><h2>Operational overview</h2><p className="muted">Tickets created this UTC month; workload and overdue counts are current.</p></div><button className="secondary" onClick={() => setRefresh((value) => value + 1)}>Refresh</button></div>
        {report.loading ? <p aria-busy="true">Loading report…</p> : report.data ? <>
          <div className="metric-grid">
            <Metric label="Created" value={report.data.tickets_created} />
            <Metric label="Unresolved" value={report.data.unresolved_tickets} />
            <Metric label="Currently overdue" value={report.data.currently_overdue_tickets} />
            <Metric label="Response compliance" value={metric(report.data.response_sla_compliance_rate)} />
            <Metric label="Resolution compliance" value={metric(report.data.resolution_sla_compliance_rate)} />
            <Metric label="Avg. first response" value={duration(report.data.average_first_response_seconds)} />
            <Metric label="Avg. first resolution" value={duration(report.data.average_first_resolution_seconds)} />
          </div>
          <h3>Tickets by status</h3><p>{Object.entries(report.data.status_counts).map(([key, value]) => `${key}: ${value}`).join(" · ") || "No tickets"}</p>
          <h3>Tickets by priority</h3><p>{Object.entries(report.data.priority_counts).map(([key, value]) => `${key}: ${value}`).join(" · ") || "No tickets"}</p>
          <h3>Assigned open workload</h3>{report.data.workload.length ? <ul>{report.data.workload.map((agent) => <li key={agent.membership_id}>{agent.agent_name}: {agent.open_tickets}</li>)}</ul> : <p>No active staff.</p>}
        </> : <p>No report available.</p>}
      </section>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong></article>;
}
function duration(seconds: number | null) {
  return seconds === null ? "No data" : seconds < 3600 ? `${Math.round(seconds / 60)} min` : `${(seconds / 3600).toFixed(1)} hr`;
}
