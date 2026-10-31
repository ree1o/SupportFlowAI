import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { api, type CurrentUser } from "./api";
import { ErrorText, message, useAuth } from "./auth";

export function Sidebar({
  busy = false,
  onSelect,
  onCreate,
}: {
  busy?: boolean;
  onSelect?: (id: string) => Promise<void>;
  onCreate?: () => void;
}) {
  const { current, setCurrent } = useAuth();
  const navigate = useNavigate();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  if (!current) return null;
  const selected = current.organizations.find((org) => org.id === current.selected_organization_id);
  async function act(action: () => Promise<void>) {
    setPending(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(message(err));
    } finally {
      setPending(false);
    }
  }
  async function select(id: string) {
    if (onSelect) return onSelect(id);
    await act(async () => {
      setCurrent(await api<CurrentUser>(`/organizations/${id}/select`, "POST"));
      navigate("/app/tickets");
    });
  }
  return (
    <aside className="sidebar">
      <Link className="wordmark" to="/app/tickets">
        <span className="brand-mark" aria-hidden="true">
          S
        </span>
        SupportFlow AI
      </Link>
      <p className="eyebrow">Workspace</p>
      <label>
        Select organization
        <select
          aria-label="Select organization"
          disabled={busy || pending}
          value={current.selected_organization_id ?? ""}
          onChange={(event) => void select(event.target.value)}
        >
          <option value="" disabled>
            Select a workspace
          </option>
          {current.organizations.map((org) => (
            <option key={org.id} value={org.id}>
              {org.name}
            </option>
          ))}
        </select>
      </label>
      <button
        className="secondary"
        onClick={onCreate ?? (() => navigate("/app?new=1"))}
      >
        + New workspace
      </button>
      <nav aria-label="Staff navigation">
        <NavLink to="/app/tickets">Tickets</NavLink>
        <NavLink to="/app/contacts">Contacts</NavLink>
        <NavLink to="/app/categories">Categories</NavLink>
        <NavLink to="/app/operations">SLA & operations</NavLink>
        <NavLink to="/app/incidents">Incident Radar</NavLink>
        {selected && selected.role !== "agent" && <NavLink to="/app/integrations">Integrations</NavLink>}
        <NavLink to="/app" end>
          Organization & team
        </NavLink>
        <Link to="/status">System status ↗</Link>
      </nav>
      <ErrorText text={error} />
      <div className="identity">
        <strong>{current.user.full_name}</strong>
        <span>{current.user.email}</span>
        <button
          className="text-button"
          disabled={busy || pending}
          onClick={() =>
            void act(async () => {
              await api("/auth/logout", "POST");
              setCurrent(null);
            })
          }
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}
