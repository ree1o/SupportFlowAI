import { useState, type FormEvent } from "react";
import { api, type Organization } from "../api";
import { ErrorText, message } from "../auth";
import { useRemote } from "./remote";
import type { Contact, Page } from "./types";

type Source = {
  id: string;
  contact_id: string;
  name: string;
  enabled: boolean;
  credential_version: number;
  revoked_at: string | null;
  created_at: string;
};
type Receipt = {
  id: string;
  source_id: string;
  external_event_id: string;
  event_type: string;
  received_at: string;
  status: "succeeded" | "rejected";
  ticket_id: string | null;
  error_category: string | null;
};
type ReceiptPage = Page<Receipt>;
type Issued = { source: Source; credential: string };

export function Integrations({ org }: { org: Organization }) {
  const base = `/organizations/${org.id}/integrations`;
  const sources = useRemote<Source[]>(base);
  const contacts = useRemote<Page<Contact>>(
    `/organizations/${org.id}/contacts?page=1&page_size=100`,
  );
  const receipts = useRemote<ReceiptPage>(`${base}/receipts?page=1&page_size=20`);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const canManage = org.role === "owner" || org.role === "administrator";

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await api<Issued>(base, "POST", {
        name: String(form.get("name")),
        contact_id: String(form.get("contact_id")),
      });
      setIssued(result);
      sources.reload();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function action(path: string, method: string, body?: unknown) {
    setBusy(true);
    setError("");
    try {
      if (method === "POST" && path.endsWith("rotate-credential")) {
        setIssued(await api<Issued>(path, method, body));
      } else {
        await api(path, method, body);
      }
      sources.reload();
      receipts.reload();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function copyCredential() {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.credential);
      setCopied(true);
    } catch {
      setError("Clipboard access was denied. Select and copy the credential manually.");
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{org.name} · Secure intake</p>
          <h1>Integrations</h1>
          <p className="muted">
            Receive signed customer requests from trusted systems. Each source is bound to one
            contact in this organization.
          </p>
        </div>
      </div>
      <ErrorText text={error || sources.error || receipts.error} />
      {!canManage ? (
        <section className="panel"><h2>Management access required</h2><p>Only Owners and Administrators can manage integration credentials.</p></section>
      ) : (
        <section className="panel">
          <h2>Add an integration source</h2>
          <p className="muted">Choose the exact customer contact this source is authorized to represent.</p>
          {contacts.error && <ErrorText text={contacts.error} />}
          <form className="inline-form integration-form" onSubmit={(event) => void create(event)}>
            <label>Source name<input name="name" required minLength={1} maxLength={100} placeholder="Billing platform" /></label>
            <label>Authorized contact<select name="contact_id" required disabled={contacts.loading}>
              <option value="">{contacts.loading ? "Loading contacts…" : "Select a contact"}</option>
              {contacts.data?.items.filter((contact) => contact.is_active).map((contact) =>
                <option key={contact.id} value={contact.id}>{contact.name}{contact.email ? ` · ${contact.email}` : ""}</option>,
              )}
            </select></label>
            <button disabled={busy || contacts.loading || !contacts.data?.items.some((contact) => contact.is_active)}>{busy ? "Creating…" : "Create source"}</button>
          </form>
        </section>
      )}
      {issued && (
        <section className="panel credential-panel" aria-live="polite">
          <div className="section-heading"><div><h2>Save this credential now</h2><p className="muted">It is shown once. Copy it into the trusted system's secret store.</p></div>
            <button className="secondary" onClick={() => { setIssued(null); setCopied(false); }}>Dismiss</button></div>
          <label>Signing credential<input aria-label="New signing credential" readOnly value={issued.credential} onFocus={(event) => event.currentTarget.select()} /></label>
          <button className="secondary" onClick={() => void copyCredential()}>{copied ? "Copied" : "Copy credential"}</button>
        </section>
      )}
      <section className="panel">
        <div className="section-heading"><div><h2>Configured sources</h2><p className="muted">Credentials are encrypted at rest and never included in list responses.</p></div>
          <button className="secondary" onClick={sources.reload}>Refresh</button></div>
        {sources.loading ? <p aria-busy="true">Loading integration sources…</p> : sources.data?.length ? (
          <div className="table-scroll"><table><thead><tr><th>Source</th><th>Authorized contact</th><th>Status</th><th>Credential</th><th>Actions</th></tr></thead><tbody>
            {sources.data.map((source) => <tr key={source.id}>
              <td><strong>{source.name}</strong><br /><small>Version {source.credential_version}</small></td>
              <td>{contacts.data?.items.find((contact) => contact.id === source.contact_id)?.name ?? "Contact"}</td>
              <td>{source.revoked_at ? "Revoked" : source.enabled ? "Enabled" : "Disabled"}</td>
              <td>Version {source.credential_version}</td>
              <td className="integration-actions">
                {!source.revoked_at && canManage && <>
                  <button className="secondary" disabled={busy} onClick={() => {
                    const name = window.prompt("Integration source name", source.name);
                    if (name !== null && name.trim()) void action(`${base}/${source.id}`, "PATCH", { name: name.trim() });
                  }}>Rename</button>
                  <button className="secondary" disabled={busy} onClick={() => void action(`${base}/${source.id}`, "PATCH", { enabled: !source.enabled })}>{source.enabled ? "Disable" : "Enable"}</button>
                  <button className="secondary" disabled={busy} onClick={() => void action(`${base}/${source.id}/rotate-credential`, "POST")}>Rotate key</button>
                  <button className="text-button danger-text" disabled={busy} onClick={() => { if (window.confirm(`Revoke ${source.name}? Existing credentials will stop working.`)) void action(`${base}/${source.id}`, "DELETE"); }}>Revoke</button>
                </>}
              </td>
            </tr>)}
          </tbody></table></div>
        ) : <p>No integration sources yet.</p>}
      </section>
      <section className="panel">
        <div className="section-heading"><div><h2>Recent webhook receipts</h2><p className="muted">Payloads and signatures are not retained. Event ID and digest provide replay diagnostics.</p></div>
          <button className="secondary" onClick={receipts.reload}>Refresh</button></div>
        {receipts.loading ? <p aria-busy="true">Loading receipts…</p> : receipts.data?.items.length ? (
          <div className="table-scroll"><table><thead><tr><th>Received</th><th>Source</th><th>Event</th><th>Type</th><th>Outcome</th></tr></thead><tbody>
            {receipts.data.items.map((receipt) => <tr key={receipt.id}><td>{new Date(receipt.received_at).toLocaleString()}</td><td>{sources.data?.find((source) => source.id === receipt.source_id)?.name ?? "Source"}</td><td><code>{receipt.external_event_id}</code></td><td>{receipt.event_type}</td><td>{receipt.status === "succeeded" ? "Processed" : `Rejected · ${receipt.error_category ?? "unknown"}`}</td></tr>)}
          </tbody></table></div>
        ) : <p>No webhook deliveries have been received.</p>}
      </section>
      <section className="panel">
        <h2>Request signing</h2>
        <p>Send <code>X-SupportFlow-Event-Id</code>, <code>X-SupportFlow-Timestamp</code> (Unix seconds), and <code>X-SupportFlow-Signature</code> with value <code>v1=&lt;hex HMAC-SHA256&gt;</code>.</p>
        <p>Sign <code>timestamp + "." + event ID + "." + raw request body</code>. Timestamps are accepted for five minutes. Reuse the same event ID only for an exact retry of the same payload.</p>
        <p>See the <a href="https://github.com/ree1o/SupportFlowAI/blob/main/docs/integrations.md" target="_blank" rel="noreferrer">integration guide</a> for payload examples and a Python sender.</p>
      </section>
    </>
  );
}
