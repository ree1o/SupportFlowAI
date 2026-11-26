import { useState, type FormEvent } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { api, type CurrentUser, type Organization } from "./api";
import { ErrorText, message, useAuth } from "./auth";

type IconName =
  | "inbox"
  | "users"
  | "tag"
  | "clock"
  | "radar"
  | "book"
  | "plug"
  | "settings"
  | "activity"
  | "plus"
  | "search"
  | "menu"
  | "close"
  | "chevron";

const iconPaths: Record<IconName, string[]> = {
  inbox: ["M4 4h16v13h-4l-2 3h-4l-2-3H4z", "M4 12h4l2 3h4l2-3h4"],
  users: ["M16 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2", "M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8", "M20 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  tag: ["M20.6 13.4 13 21a2 2 0 0 1-2.8 0L3 13.8V4h9.8l7.8 7.2a1.6 1.6 0 0 1 0 2.2Z", "M7.5 8.5h.01"],
  clock: ["M12 8v4l2.5 2.5", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"],
  radar: ["M12 12 19 5", "M20.5 12a8.5 8.5 0 1 1-2.5-6", "M16.4 12a4.4 4.4 0 1 1-1.3-3.1", "M12 12h.01"],
  book: ["M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H6.5A2.5 2.5 0 0 1 4 17.5z", "M4 16a2.5 2.5 0 0 1 2.5-2.5H20", "M8 7h8"],
  plug: ["M8 3v5", "M16 3v5", "M6 8h12v3a6 6 0 0 1-6 6v4", "M12 21v-4"],
  settings: ["M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z", "m19.4 15 .1.1a1.7 1.7 0 1 1-2.4 2.4l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a1.7 1.7 0 1 1-3.4 0v-.2A1.7 1.7 0 0 0 7.8 17l-.1.1a1.7 1.7 0 1 1-2.4-2.4l.1-.1a1.7 1.7 0 0 0-1.2-2.9H4a1.7 1.7 0 1 1 0-3.4h.2a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a1.7 1.7 0 1 1 2.4-2.4l.1.1a1.7 1.7 0 0 0 2.9-1.2V2a1.7 1.7 0 1 1 3.4 0v.2A1.7 1.7 0 0 0 17 3.4l.1-.1a1.7 1.7 0 1 1 2.4 2.4l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.2a1.7 1.7 0 1 1 0 3.4h-.2a1.7 1.7 0 0 0-1.2 2.9Z"],
  activity: ["M3 12h4l3-8 4 16 3-8h4"],
  plus: ["M12 5v14", "M5 12h14"],
  search: ["M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z", "m21 21-4.35-4.35"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  close: ["m18 6-12 12", "m6 6 12 12"],
  chevron: ["m7 10 5 5 5-5"],
};

export function AppIcon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {iconPaths[name].map((path, index) => <path d={path} key={index} />)}
    </svg>
  );
}

export function ProductMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 36 36" fill="none">
      <path d="M7 8.5A3.5 3.5 0 0 1 10.5 5h15A3.5 3.5 0 0 1 29 8.5v10a3.5 3.5 0 0 1-3.5 3.5H17l-6 5v-5.2A3.5 3.5 0 0 1 7 18.5z" stroke="currentColor" strokeWidth="2" />
      <path d="M13 13h10M13 17h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="27.5" cy="27.5" r="4.5" fill="currentColor" />
      <path d="m25.8 27.6 1.2 1.2 2.3-2.6" stroke="#15131d" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function NavigationItem({ to, label, icon, end, onNavigate }: { to: string; label: string; icon: IconName; end?: boolean; onNavigate: () => void }) {
  return (
    <NavLink to={to} end={end} onClick={onNavigate} aria-label={label === "Inbox" ? "Tickets" : undefined}>
      <AppIcon name={icon} />
      <span>{label}</span>
    </NavLink>
  );
}

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
  const [open, setOpen] = useState(false);
  if (!current) return null;
  const selected = current.organizations.find((org) => org.id === current.selected_organization_id);

  async function act(action: () => Promise<void>) {
    setPending(true);
    setError("");
    try { await action(); }
    catch (err) { setError(message(err)); }
    finally { setPending(false); }
  }
  async function select(id: string) {
    if (onSelect) {
      await onSelect(id);
      setOpen(false);
      return;
    }
    await act(async () => {
      setCurrent(await api<CurrentUser>(`/organizations/${id}/select`, "POST"));
      navigate("/app/tickets");
      setOpen(false);
    });
  }
  function createWorkspace() {
    setOpen(false);
    if (onCreate) onCreate();
    else navigate("/app?new=1");
  }
  async function signOut() {
    await act(async () => {
      await api("/auth/logout", "POST");
      setCurrent(null);
      navigate("/login");
    });
  }
  const closeMenu = () => setOpen(false);

  return (
    <>
      <button
        className="mobile-nav-toggle"
        type="button"
        aria-label={open ? "Close navigation" : "Open navigation"}
        aria-expanded={open}
        aria-controls="staff-sidebar"
        onClick={() => setOpen((value) => !value)}
      >
        <AppIcon name={open ? "close" : "menu"} size={20} />
      </button>
      {open && <button className="sidebar-scrim" type="button" aria-label="Close navigation" onClick={closeMenu} />}
      <aside className={`sidebar${open ? " is-open" : ""}`} id="staff-sidebar" aria-label="Workspace navigation">
        <Link className="wordmark" to="/app/tickets" onClick={closeMenu}>
          <span className="brand-mark"><ProductMark /></span>
          <span><strong>SupportFlow</strong><small>Customer operations</small></span>
        </Link>

        <div className="workspace-picker">
          <span className="sidebar-label">Workspace</span>
          <label className="sr-only" htmlFor="workspace-select">Select organization</label>
          <span className="workspace-select-wrap">
            <select
              id="workspace-select"
              aria-label="Select organization"
              disabled={busy || pending}
              value={current.selected_organization_id ?? ""}
              onChange={(event) => void select(event.target.value)}
            >
              <option value="" disabled>Select a workspace</option>
              {current.organizations.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
            </select>
            <AppIcon name="chevron" size={15} />
          </span>
          {selected && <span className="workspace-role">{selected.role}</span>}
          <button className="new-workspace-button" type="button" aria-label="+ New workspace" onClick={createWorkspace} disabled={busy || pending}>
            <AppIcon name="plus" size={16} /> New workspace
          </button>
        </div>

        <nav aria-label="Staff navigation" className="staff-nav">
          <div className="nav-group">
            <p className="nav-group-title">Support</p>
            <NavigationItem to="/app/tickets" label="Inbox" icon="inbox" onNavigate={closeMenu} />
            <NavigationItem to="/app/contacts" label="Contacts" icon="users" onNavigate={closeMenu} />
            <NavigationItem to="/app/categories" label="Categories" icon="tag" onNavigate={closeMenu} />
          </div>
          <div className="nav-group">
            <p className="nav-group-title">Intelligence</p>
            <NavigationItem to="/app/operations" label="SLA & operations" icon="clock" onNavigate={closeMenu} />
            <NavigationItem to="/app/incidents" label="Incident Radar" icon="radar" onNavigate={closeMenu} />
            <NavigationItem to="/app/knowledge" label="Knowledge Base" icon="book" onNavigate={closeMenu} />
          </div>
          <div className="nav-group">
            <p className="nav-group-title">Workspace</p>
            {selected && selected.role !== "agent" && <NavigationItem to="/app/integrations" label="Integrations" icon="plug" onNavigate={closeMenu} />}
            <NavigationItem to="/app" label="Organization & team" icon="settings" end onNavigate={closeMenu} />
          </div>
        </nav>

        <div className="sidebar-bottom">
          <Link to="/status" className="system-status-link" onClick={closeMenu}>
            <AppIcon name="activity" size={16} /><span>System status</span><span className="service-indicator" aria-label="Service status available" />
          </Link>
          {error && <ErrorText text={error} />}
          <div className="identity">
            <span className="identity-avatar" aria-hidden="true">{current.user.full_name.trim().charAt(0).toUpperCase()}</span>
            <span className="identity-copy"><strong>{current.user.full_name}</strong><small>{current.user.email}</small></span>
            <button className="text-button" disabled={busy || pending} onClick={() => void signOut()}>Sign out</button>
          </div>
        </div>
      </aside>
    </>
  );
}

export function WorkspaceHeader({ organization }: { organization: Organization }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = query.trim();
    navigate(value ? `/app/tickets?q=${encodeURIComponent(value)}` : "/app/tickets");
  }
  return (
    <header className="workspace-topbar">
      <div className="topbar-context"><span className="topbar-product">SupportFlow</span><span className="topbar-divider">/</span><span>{organization.name}</span></div>
      <form className="global-search" role="search" onSubmit={search}>
        <AppIcon name="search" size={17} />
        <label className="sr-only" htmlFor="global-ticket-search">Search tickets in this workspace</label>
        <input id="global-ticket-search" value={query} onChange={(event) => setQuery(event.target.value)} maxLength={200} placeholder="Search tickets…" />
        <kbd>↵</kbd>
      </form>
      <div className="topbar-workspace"><span className="topbar-workspace-dot" /><span>{organization.role}</span></div>
    </header>
  );
}
