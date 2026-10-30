import { useEffect, useState, type FormEvent } from "react";
import type { Organization } from "../api";
import { api } from "../api";
import { ErrorText, message } from "../auth";
import { useRemote } from "./remote";
import type { Contact, Page } from "./types";

type CustomerGrant = {
  id: string;
  contact_id: string;
  email: string;
  user_name: string;
  is_active: boolean;
};

export function Contacts({ org }: { org: Organization }) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [archived, setArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const root = `/organizations/${org.id}/contacts`;
  const remote = useRemote<Page<Contact>>(
    `${root}?${new URLSearchParams({ q, page: String(page), include_inactive: String(archived) })}`,
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Customer records</p>
          <h1>Contacts</h1>
          <p className="muted">
            People your team supports, kept separate from staff accounts.
          </p>
        </div>
        <button
          onClick={() => {
            setCreating(true);
            setSelected(null);
          }}
        >
          New contact
        </button>
      </div>
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setQ(String(new FormData(event.currentTarget).get("q")));
          setPage(1);
        }}
      >
        <label>
          Search contacts
          <input name="q" placeholder="Name or email" maxLength={200} />
        </label>
        <button className="secondary">Search</button>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={archived}
            onChange={(event) => {
              setArchived(event.target.checked);
              setPage(1);
            }}
          />
          Include archived
        </label>
      </form>
      <ErrorText text={remote.error} />
      {(creating || selected) && (
        <ContactEditor
          key={selected ?? "new"}
          root={root}
          organizationId={org.id}
          id={selected}
          manager={org.role !== "agent"}
          done={() => {
            remote.reload();
            setCreating(false);
            setSelected(null);
          }}
          cancel={() => {
            setCreating(false);
            setSelected(null);
          }}
        />
      )}
      <section className="panel">
        <div className="section-heading">
          <h2>Customer directory</h2>
          <span className="muted">{remote.data?.total ?? 0} contacts</span>
        </div>
        {remote.loading && !remote.data ? (
          <p aria-busy="true">Loading contacts…</p>
        ) : remote.data?.items.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Access</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {remote.data.items.map((contact) => (
                  <tr key={contact.id}>
                    <td>
                      <strong>{contact.name}</strong>
                    </td>
                    <td>{contact.email ?? "—"}</td>
                    <td>{contact.is_active ? "Active" : "Archived"}</td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => {
                          setCreating(false);
                          setSelected(contact.id);
                        }}
                      >
                        View contact
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-state">
            No contacts match. Create a customer record to open a ticket.
          </p>
        )}
        <div className="pagination">
          <button
            className="secondary"
            disabled={page <= 1}
            onClick={() => setPage((value) => value - 1)}
          >
            Previous
          </button>
          <span>Page {page}</span>
          <button
            className="secondary"
            disabled={!remote.data || page * 20 >= remote.data.total}
            onClick={() => setPage((value) => value + 1)}
          >
            Next
          </button>
        </div>
      </section>
    </>
  );
}

function ContactEditor({
  root,
  organizationId,
  id,
  manager,
  done,
  cancel,
}: {
  root: string;
  organizationId: string;
  id: string | null;
  manager: boolean;
  done: () => void;
  cancel: () => void;
}) {
  const remote = useRemote<Contact>(id ? `${root}/${id}` : null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [inviteNotice, setInviteNotice] = useState("");
  const [grants, setGrants] = useState<CustomerGrant[]>([]);
  const contact = id ? remote.data : undefined;
  useEffect(() => {
    let active = true;
    if (!manager || !contact) {
      setGrants([]);
      return;
    }
    void api<CustomerGrant[]>(`/organizations/${organizationId}/customer-access`)
      .then((value) => {
        if (active) setGrants(value.filter((grant) => grant.contact_id === contact.id));
      })
      .catch(() => {
        if (active) setGrants([]);
      });
    return () => {
      active = false;
    };
  }, [manager, organizationId, contact?.id]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api(id ? `${root}/${id}` : root, id ? "PATCH" : "POST", {
        name: data.get("name"),
        email: data.get("email") || null,
      });
      done();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  async function archive() {
    setBusy(true);
    setError("");
    try {
      await api(`${root}/${id}`, "PATCH", { is_active: !contact?.is_active });
      done();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  async function invite() {
    if (!contact) return;
    setBusy(true);
    setError("");
    setInviteNotice("");
    try {
      const result = await api<{ message: string }>(
        `/organizations/${organizationId}/customer-access/invitations`,
        "POST",
        { contact_id: contact.id },
      );
      setInviteNotice(result.message);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  async function revokeAccess(accessId: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/organizations/${organizationId}/customer-access/${accessId}`, "DELETE");
      setGrants((value) =>
        value.map((grant) =>
          grant.id === accessId ? { ...grant, is_active: false } : grant,
        ),
      );
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel editor-panel">
      <h2>{id ? "Contact details" : "New customer contact"}</h2>
      <ErrorText text={error || (id ? remote.error : "")} />
      {id && !contact ? (
        <p>{remote.loading ? "Loading contact…" : "Contact unavailable."}</p>
      ) : (
        <form
          key={contact?.updated_at ?? "new"}
          className="inline-form"
          onSubmit={save}
        >
          <label>
            Contact name
            <input
              name="name"
              defaultValue={contact?.name}
              maxLength={100}
              required
            />
          </label>
          <label>
            Contact email
            <input
              name="email"
              type="email"
              defaultValue={contact?.email ?? ""}
              maxLength={254}
            />
          </label>
          <button disabled={busy}>Save contact</button>
          <button type="button" className="secondary" onClick={cancel}>
            Cancel
          </button>
        </form>
      )}
      {contact && (
        <div className="contact-meta">
          {inviteNotice && <p className="success" role="status">{inviteNotice}</p>}
          {grants.map((grant) => (
            <p className="muted" key={grant.id}>
              Portal access: {grant.user_name} ({grant.email}) — {grant.is_active ? "active" : "revoked"}
              {grant.is_active && manager && (
                <button className="text-button" disabled={busy} onClick={() => void revokeAccess(grant.id)}>
                  Revoke access
                </button>
              )}
            </p>
          ))}
          <p className="muted">
            Created {new Date(contact.created_at).toLocaleString()} ·{" "}
            {contact.is_active
              ? "Active customer record"
              : "Archived customer record"}
          </p>
          {manager && (
            <div className="contact-actions">
              <button
                className="text-button"
                disabled={busy || !contact.is_active || !contact.email}
                onClick={() => void invite()}
              >
                Invite to customer portal
              </button>
              <button
                className="text-button"
                disabled={busy}
                onClick={() => void archive()}
              >
                {contact.is_active ? "Archive contact" : "Restore contact"}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
