import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, type CurrentUser } from "../api";
import { ErrorText, message, useAuth } from "../auth";

type CustomerOrganization = {
  id: string;
  organization_id: string;
  organization_name: string;
  organization_slug: string;
  contact_id: string;
};
type CustomerProfile = {
  id: string;
  email: string;
  full_name: string;
  organizations: CustomerOrganization[];
};
type CustomerTicket = {
  id: string;
  organization_id: string;
  title: string;
  status: "open" | "pending" | "resolved" | "closed";
  priority: string;
  version: number;
  created_at: string;
  updated_at: string;
};
type TicketPage = { items: CustomerTicket[]; total: number };
type Message = { id: string; author_name: string; body: string; created_at: string };
type TicketDetail = CustomerTicket & { messages: Message[]; next_after_version: number | null };

export function CustomerAccount({ register = false }: { register?: boolean }) {
  const { current, loading, setCurrent } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [query] = useSearchParams();
  if (loading) return <main aria-busy="true">Loading your account…</main>;
  if (current) return <Navigate to="/customer" replace />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      if (register) {
        const result = await api<{ message: string }>("/customer/register", "POST", {
          email: data.get("email"),
          full_name: data.get("name"),
          password: data.get("password"),
          organization_slug: data.get("organization"),
        });
        setNotice(result.message);
      } else {
        const user = await api<CurrentUser>("/auth/login", "POST", {
          email: data.get("email"),
          password: data.get("password"),
        });
        setCurrent(user);
        const invite = query.get("invite");
        if (!invite) await api<CustomerProfile>("/customer/me");
        navigate(invite ? `/customer/invitation?token=${encodeURIComponent(invite)}` : "/customer", { replace: true });
      }
    } catch (err) {
      setError(
        register
          ? message(err)
          : err instanceof ApiError && err.status === 404
            ? "This account has no active customer access. Use the staff sign-in if you manage a workspace."
            : message(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="account-page">
      <Link to="/customer" className="wordmark"><span className="brand-mark" aria-hidden="true">S</span>SupportFlow</Link>
      <section className="card account-card customer-account">
        <p className="eyebrow">Customer support</p>
        <h1>{register ? "Create customer access" : "Your support requests"}</h1>
        <p className="muted">{register ? "Use the organization address and email already on your customer record. We will send a verification link." : "Sign in to view requests linked to your verified customer record."}</p>
        {notice && <p className="success" role="status">{notice}</p>}
        <ErrorText text={error} />
        <form onSubmit={submit}>
          {register && <label>Your name<input name="name" autoComplete="name" maxLength={100} required /></label>}
          <label>Email<input name="email" type="email" autoComplete="email" maxLength={254} required /></label>
          {register && <label>Organization address<input name="organization" autoComplete="organization" minLength={2} maxLength={60} required /></label>}
          <label>Password<input name="password" type="password" autoComplete={register ? "new-password" : "current-password"} minLength={12} maxLength={128} required /></label>
          <button disabled={busy}>{busy ? "Please wait…" : register ? "Request verification email" : "Sign in"}</button>
        </form>
        <p className="account-link"><Link to={register ? "/customer/login" : "/customer/register"}>{register ? "Already have access? Sign in" : "Need customer access? Create an account"}</Link></p>
        {!register && <p className="account-link"><Link to="/customer/recover">Forgot your password?</Link></p>}
        <p className="account-link"><Link to="/login">Staff sign in</Link></p>
      </section>
    </main>
  );
}

export function CustomerVerify() {
  const [query] = useSearchParams();
  const [state, setState] = useState("Verifying your email…");
  useEffect(() => {
    const token = query.get("token");
    if (!token) { setState("This verification link is incomplete."); return; }
    void api("/customer/verify", "POST", { token })
      .then(() => setState("Email verified. You can now sign in to your support portal."))
      .catch((err) => setState(message(err)));
  }, [query]);
  return <main className="account-page"><section className="card"><h1>Email verification</h1><p role="status">{state}</p><Link to="/customer/login">Continue to sign in</Link></section></main>;
}

export function CustomerRecovery() {
  const [query] = useSearchParams();
  const token = query.get("token");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true); setError(""); setNotice("");
    try {
      if (token) {
        await api("/customer/password-recovery/complete", "POST", { token, password: data.get("password") });
        setNotice("Password updated. Sign in with your new password.");
      } else {
        const result = await api<{ message: string }>("/customer/password-recovery", "POST", { email: data.get("email") });
        setNotice(result.message);
      }
    } catch (err) { setError(message(err)); }
    finally { setBusy(false); }
  }
  return <main className="account-page"><section className="card account-card"><h1>{token ? "Choose a new password" : "Recover your account"}</h1><p className="muted">{token ? "Recovery links are single use and expire." : "If an eligible account exists, we will send a recovery link."}</p><ErrorText text={error} />{notice && <p className="success" role="status">{notice}</p>}<form onSubmit={submit}>{token ? <label>New password<input name="password" type="password" minLength={12} maxLength={128} autoComplete="new-password" required /></label> : <label>Email<input name="email" type="email" maxLength={254} required /></label>}<button disabled={busy}>{busy ? "Please wait…" : token ? "Update password" : "Send recovery email"}</button></form><p className="account-link"><Link to="/customer/login">Return to sign in</Link></p></section></main>;
}

export function CustomerInvitation() {
  const [query] = useSearchParams();
  const token = query.get("token") ?? "";
  const { current, setCurrent } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await api("/customer/invitations/accept", "POST", {
        token,
        ...(data.get("name") ? { full_name: data.get("name") } : {}),
        ...(data.get("password") ? { password: data.get("password") } : {}),
      });
      setCurrent(await api<CurrentUser>("/auth/me"));
      await api<CustomerProfile>("/customer/me");
      navigate("/customer", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "account_sign_in_required") {
        setError("An account already exists for this invitation. Sign in to accept it.");
      } else setError(message(err));
    } finally { setBusy(false); }
  }
  if (!token) return <main className="account-page"><section className="card"><h1>Invitation unavailable</h1><p>Ask the support team to send a new invitation.</p></section></main>;
  return <main className="account-page"><section className="card account-card"><p className="eyebrow">Customer invitation</p><h1>Access your support requests</h1><p className="muted">{current ? "Accept this invitation for the signed-in account." : "Create a secure account using this one-time email invitation."}</p><ErrorText text={error} />{!current && error.includes("already exists") && <Link to={`/customer/login?invite=${encodeURIComponent(token)}`}>Sign in to accept</Link>}<form onSubmit={submit}>{!current && <><label>Your name<input name="name" autoComplete="name" maxLength={100} required /></label><label>Password<input name="password" type="password" minLength={12} maxLength={128} autoComplete="new-password" required /></label></>}<button disabled={busy}>{busy ? "Please wait…" : "Accept invitation"}</button></form></section></main>;
}

export function CustomerPortal() {
  const { current, loading, setCurrent } = useAuth();
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [error, setError] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  useEffect(() => {
    if (!current) return;
    let active = true;
    void api<CustomerProfile>("/customer/me")
      .then((value) => { if (active) { setProfile(value); setOrganizationId(value.organizations[0]?.organization_id ?? ""); } })
      .catch((err) => { if (active) setError(message(err)); });
    return () => { active = false; };
  }, [current?.user.id]);
  if (loading) return <main aria-busy="true">Loading your account…</main>;
  if (!current) return <Navigate to="/customer/login" replace />;
  if (error && !profile) return <main className="account-page"><section className="card"><h1>Customer access unavailable</h1><ErrorText text={error} /><p><Link to="/app/tickets">Open the staff workspace</Link></p></section></main>;
  if (!profile) return <main aria-busy="true">Loading your support requests…</main>;
  const organization = profile.organizations.find((item) => item.organization_id === organizationId);
  async function logout() { await api("/auth/logout", "POST"); setCurrent(null); }
  return <div className="customer-shell">
    <header className="customer-header"><Link className="wordmark" to="/customer"><span className="brand-mark" aria-hidden="true">S</span>SupportFlow</Link><nav aria-label="Customer navigation"><Link to="/customer">My requests</Link><Link to="/customer/tickets/new">New request</Link></nav><div className="customer-user"><span>{profile.full_name}</span>{current.organizations.length > 0 && <Link to="/app/tickets">Staff workspace</Link>}<button className="text-button" onClick={() => void logout()}>Sign out</button></div></header>
    <main className="customer-main">
      {profile.organizations.length > 1 && <label className="customer-org-select">Organization<select aria-label="Customer organization" value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}>{profile.organizations.map((item) => <option key={item.id} value={item.organization_id}>{item.organization_name}</option>)}</select></label>}
      <ErrorText text={error} />
      {organization ? <Routes>
        <Route index element={<CustomerDashboard profile={profile} org={organization} />} />
        <Route path="tickets/new" element={<CustomerNewTicket org={organization} />} />
        <Route path="tickets/:id" element={<CustomerTicketPage org={organization} />} />
        <Route path="*" element={<Navigate to="/customer" replace />} />
      </Routes> : <section className="panel"><h1>No customer organization is linked</h1><p>Ask the support team to invite the email address on your account.</p></section>}
    </main>
  </div>;
}

function CustomerDashboard({ profile, org }: { profile: CustomerProfile; org: CustomerOrganization }) {
  const [page, setPage] = useState<TicketPage | null>(null);
  const [notifications, setNotifications] = useState<{ id: string; title: string; created_at: string; read_at: string | null }[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const query = new URLSearchParams(); if (status) query.set("status", status);
    void Promise.all([
      api<TicketPage>(`/customer/organizations/${org.organization_id}/tickets?${query}`),
      api<typeof notifications>("/customer/notifications"),
    ]).then(([tickets, notes]) => { if (active) { setPage(tickets); setNotifications(notes); } })
      .catch((err) => { if (active) setError(message(err)); });
    return () => { active = false; };
  }, [org.organization_id, status]);
  return <>
    <div className="page-heading"><div><p className="eyebrow">{org.organization_name}</p><h1>Your support requests</h1><p className="muted">Track updates from the support team and reply when you need more help.</p></div><Link className="button-link" to="/customer/tickets/new">Create a request</Link></div>
    <ErrorText text={error} />
    <section className="panel"><div className="section-heading"><h2>Requests</h2><label className="customer-status-filter">Filter by status<select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option><option value="open">Open</option><option value="pending">Pending</option><option value="resolved">Resolved</option><option value="closed">Closed</option></select></label></div>
      {!page ? <p aria-busy="true">Loading requests…</p> : page.items.length ? page.items.map((ticket) => <Link className="ticket-row" key={ticket.id} to={`/customer/tickets/${ticket.id}`}><span className="ticket-summary"><strong>{ticket.title}</strong><span className="muted">Updated {new Date(ticket.updated_at).toLocaleString()}</span></span><span className="ticket-row-meta"><span className={`status-pill status-${ticket.status}`}>{ticket.status}</span><span className={`priority priority-${ticket.priority}`}>{ticket.priority} priority</span></span></Link>) : <p className="empty-state">You have no support requests yet. Create one to get started.</p>}
    </section>
    <section className="panel"><h2>Updates</h2>{notifications.length ? notifications.map((item) => <p key={item.id} className="notification-row"><span>{item.title}</span><time>{new Date(item.created_at).toLocaleString()}</time></p>) : <p className="empty-state">New replies from support will appear here.</p>}</section>
    <p className="muted">Signed in as {profile.email}</p>
  </>;
}

function CustomerNewTicket({ org }: { org: CustomerOrganization }) {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy(true); setError("");
    try { const ticket = await api<CustomerTicket>(`/customer/organizations/${org.organization_id}/tickets`, "POST", { title: data.get("title"), message: data.get("message") }); navigate(`/customer/tickets/${ticket.id}`); }
    catch (err) { setError(message(err)); } finally { setBusy(false); }
  }
  return <><Link className="back-link" to="/customer">← My requests</Link><section className="panel"><p className="eyebrow">{org.organization_name}</p><h1>New support request</h1><p className="muted">Include the details our support team needs to help.</p><ErrorText text={error} /><form onSubmit={submit}><label>Subject<input name="title" maxLength={200} required /></label><label>How can we help?<textarea name="message" rows={7} maxLength={10000} required /></label><div className="form-actions"><button disabled={busy}>{busy ? "Submitting…" : "Submit request"}</button><Link to="/customer">Cancel</Link></div></form></section></>;
}

function CustomerTicketPage({ org }: { org: CustomerOrganization }) {
  const { id: ticketId = "" } = useParams();
  const [params] = useSearchParams();
  const id = params.get("id");
  const resolvedTicketId = id ?? ticketId;
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => { let active = true; void api<TicketDetail>(`/customer/organizations/${org.organization_id}/tickets/${resolvedTicketId}`).then((value) => { if (active) setTicket(value); }).catch((err) => { if (active) setError(message(err)); }); return () => { active = false; }; }, [org.organization_id, resolvedTicketId, revision]);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!ticket) return; const form = event.currentTarget; const data = new FormData(form); setBusy(true); setError(""); setConflict(false); try { await api(`/customer/organizations/${org.organization_id}/tickets/${ticket.id}/replies`, "POST", { expected_version: ticket.version, body: data.get("body") }); form.reset(); setRevision((value) => value + 1); } catch (err) { setError(message(err)); if (err instanceof ApiError && err.status === 409) setConflict(true); } finally { setBusy(false); } }
  return <><Link className="back-link" to="/customer">← My requests</Link>{conflict && <section className="conflict-banner"><strong>This request changed while you were viewing it.</strong><p>Reload the latest conversation before sending your reply.</p><button className="secondary" onClick={() => setRevision((value) => value + 1)}>Reload request</button></section>}<ErrorText text={error} />{!ticket ? <section className="panel" aria-busy="true">Loading request…</section> : <><div className="page-heading ticket-heading"><div><p className="eyebrow">{org.organization_name}</p><h1>{ticket.title}</h1><span className={`status-pill status-${ticket.status}`}>{ticket.status}</span></div></div><div className="customer-ticket-meta"><span>Priority: {ticket.priority}</span><span>Opened {new Date(ticket.created_at).toLocaleString()}</span></div><section className="panel"><h2>Conversation</h2>{ticket.messages.map((item) => <article className="conversation-message" key={item.id}><div className="message-heading"><strong>{item.author_name}</strong><time>{new Date(item.created_at).toLocaleString()}</time></div><p className="message-body">{item.body}</p></article>)}{ticket.status === "closed" ? <p className="empty-state">This request is closed. Create a new request if you need more help.</p> : <form className="composer" onSubmit={submit}><label>Reply to support<textarea name="body" rows={5} maxLength={10000} required /></label><p className="muted">Only public replies are available in this portal.</p><button disabled={busy || conflict}>{busy ? "Sending…" : "Send reply"}</button></form>}</section></>}</>;
}
