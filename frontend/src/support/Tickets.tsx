import { useEffect, useState, type FormEvent } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { api, ApiError, type Member, type Organization } from "../api";
import { ErrorText, message, useAuth } from "../auth";
import { useRemote } from "./remote";
import {
  priorities,
  statuses,
  type Activity,
  type Category,
  type Contact,
  type Message,
  type Page,
  type Ticket,
  type Timeline,
} from "./types";

const date = (value: string) => new Date(value).toLocaleString();
const label = (value: string) => value.replaceAll("_", " ");

export function TicketQueue({ org }: { org: Organization }) {
  const [params, setParams] = useSearchParams();
  const categories = useRemote<Category[]>(
    `/organizations/${org.id}/categories?include_inactive=true`,
  );
  const members = useRemote<Member[]>(`/organizations/${org.id}/memberships`);
  const page = Math.max(1, Number(params.get("page")) || 1);
  const query = new URLSearchParams(params);
  const assignment = query.get("assignment") ?? "";
  query.delete("assignment");
  if (assignment === "unassigned") query.set("unassigned", "true");
  else if (assignment) query.set("assigned_to", assignment);
  query.set("page", String(page));
  const remote = useRemote<Page<Ticket>>(
    `/organizations/${org.id}/tickets?${query}`,
  );
  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const [key, value] of data)
      if (String(value)) next.set(key, String(value));
    setParams(next);
  }
  function go(next: number) {
    const updated = new URLSearchParams(params);
    updated.set("page", String(next));
    setParams(updated);
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{org.name} · Support desk</p>
          <h1>Ticket queue</h1>
          <p className="muted">Keep every customer conversation moving.</p>
        </div>
        <Link className="button-link" to="/app/tickets/new">
          New ticket
        </Link>
      </div>
      <form
        className="panel queue-filters"
        key={params.toString()}
        onSubmit={filter}
      >
        <label className="search-field">
          Search tickets
          <input
            name="q"
            defaultValue={params.get("q") ?? ""}
            placeholder="Title, requester name or email"
            maxLength={200}
          />
        </label>
        <label>
          Status
          <select name="status" defaultValue={params.get("status") ?? ""}>
            <option value="">All statuses</option>
            {statuses.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          Priority
          <select name="priority" defaultValue={params.get("priority") ?? ""}>
            <option value="">All priorities</option>
            {priorities.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          Category
          <select
            name="category_id"
            defaultValue={params.get("category_id") ?? ""}
          >
            <option value="">All categories</option>
            {categories.data?.map((value) => (
              <option key={value.id} value={value.id}>
                {value.name}
                {!value.is_active ? " (inactive)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Assignment
          <select name="assignment" defaultValue={assignment}>
            <option value="">All staff</option>
            <option value="unassigned">Unassigned</option>
            {members.data?.map((value) => (
              <option key={value.id} value={value.id}>
                {value.full_name}
                {!value.is_active ? " (disabled)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Sort tickets
          <select
            name="sort"
            defaultValue={params.get("sort") ?? "created_desc"}
          >
            <option value="created_desc">Newest first</option>
            <option value="created_asc">Oldest first</option>
            <option value="updated_desc">Recently updated</option>
            <option value="priority_desc">Highest priority</option>
          </select>
        </label>
        <div className="filter-actions">
          <button>Apply filters</button>
          <button
            className="secondary"
            type="button"
            onClick={() => setParams({})}
          >
            Reset
          </button>
        </div>
      </form>
      <ErrorText text={remote.error || categories.error || members.error} />
      <section className="panel ticket-queue">
        <div className="section-heading">
          <h2>All tickets</h2>
          <span className="muted">{remote.data?.total ?? 0} results</span>
        </div>
        {remote.loading ? (
          <p aria-busy="true">Loading tickets…</p>
        ) : remote.data?.items.length ? (
          <div className="ticket-rows">
            {remote.data.items.map((ticket) => (
              <Link
                to={`/app/tickets/${ticket.id}`}
                className="ticket-row"
                key={ticket.id}
              >
                <div className="ticket-summary">
                  <span className={`status-pill status-${ticket.status}`}>
                    {ticket.status}
                  </span>
                  <strong>{ticket.title}</strong>
                  <span className="muted">
                    {ticket.requester_name} ·{" "}
                    {ticket.category_name ?? "Uncategorized"}
                  </span>
                </div>
                <div className="ticket-row-meta">
                  <span className={`priority priority-${ticket.priority}`}>
                    {ticket.priority}
                  </span>
                  <span>{ticket.assignee_name ?? "Unassigned"}</span>
                  <time dateTime={ticket.updated_at}>
                    {date(ticket.updated_at)}
                  </time>
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <h3>No tickets here yet</h3>
            <p>Try another filter or create the first ticket for your team.</p>
          </div>
        )}
        <div className="pagination">
          <button
            className="secondary"
            disabled={page <= 1 || remote.loading}
            onClick={() => go(page - 1)}
          >
            Previous
          </button>
          <span>Page {page}</span>
          <button
            className="secondary"
            disabled={
              remote.loading || !remote.data || page * 20 >= remote.data.total
            }
            onClick={() => go(page + 1)}
          >
            Next
          </button>
        </div>
      </section>
    </>
  );
}

export function TicketCreate({ org }: { org: Organization }) {
  const navigate = useNavigate();
  const { current } = useAuth();
  const root = `/organizations/${org.id}`;
  const [search, setSearch] = useState("");
  const [requester, setRequester] = useState<Contact | null>(null);
  const contacts = useRemote<Page<Contact>>(
    `${root}/contacts?${new URLSearchParams({ q: search, page_size: "20" })}`,
  );
  const categories = useRemote<Category[]>(`${root}/categories`);
  const members = useRemote<Member[]>(`${root}/memberships`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!requester) {
      setError("Choose a requester contact.");
      return;
    }
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const ticket = await api<Ticket>(`${root}/tickets`, "POST", {
        requester_contact_id: requester.id,
        title: data.get("title"),
        initial_message: data.get("initial_message"),
        priority: data.get("priority"),
        category_id: data.get("category_id") || null,
        assigned_membership_id: data.get("assigned_membership_id") || null,
      });
      navigate(`/app/tickets/${ticket.id}`);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Link to="/app/tickets" className="back-link">
        ← Ticket queue
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Staff intake</p>
          <h1>Create ticket</h1>
          <p className="muted">
            Record a customer request and give your team the context to help.
          </p>
        </div>
      </div>
      <ErrorText
        text={error || contacts.error || categories.error || members.error}
      />
      <form onSubmit={create} className="panel ticket-create">
        <h2>Customer & request</h2>
        <label>
          Find requester
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search name or email"
            maxLength={200}
          />
        </label>
        <div className="contact-choices" aria-label="Requester search results">
          {contacts.loading ? (
            <p>Finding contacts…</p>
          ) : contacts.data?.items.length ? (
            contacts.data.items.map((contact) => (
              <button
                type="button"
                aria-pressed={requester?.id === contact.id}
                className={
                  requester?.id === contact.id
                    ? "contact-choice selected"
                    : "contact-choice"
                }
                key={contact.id}
                onClick={() => setRequester(contact)}
              >
                <strong>{contact.name}</strong>
                <span>{contact.email ?? "No email"}</span>
              </button>
            ))
          ) : (
            <p className="muted">
              No active contacts match.{" "}
              <Link to="/app/contacts">Create a contact first</Link>.
            </p>
          )}
        </div>
        <p className="muted" role="status">
          {requester
            ? `Requester: ${requester.name}`
            : "Choose one contact above. Search to find additional contacts."}
        </p>
        <label>
          Ticket title
          <input
            name="title"
            required
            maxLength={200}
            placeholder="A short summary of the problem"
          />
        </label>
        <label>
          Initial customer request
          <textarea
            name="initial_message"
            required
            maxLength={10000}
            rows={6}
            placeholder="Describe what the customer needs…"
          />
        </label>
        <p className="muted">
          Recorded by you on the customer's behalf. This does not send an email.
        </p>
        <div className="field-grid">
          <label>
            Priority
            <select name="priority" defaultValue="normal">
              {priorities.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Category
            <select name="category_id">
              <option value="">Uncategorized</option>
              {categories.data?.map((value) => (
                <option key={value.id} value={value.id}>
                  {value.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Assign to
            <select name="assigned_membership_id">
              <option value="">Unassigned</option>
              {members.data
                ?.filter(
                  (value) =>
                    value.is_active &&
                    (org.role !== "agent" ||
                      value.user_id === current?.user.id),
                )
                .map((value) => (
                  <option key={value.id} value={value.id}>
                    {value.full_name}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className="form-actions">
          <button disabled={busy || !requester}>
            {busy ? "Creating…" : "Create ticket"}
          </button>
          <Link to="/app/tickets">Cancel</Link>
        </div>
      </form>
    </>
  );
}

export function TicketDetail({ org }: { org: Organization }) {
  const { id } = useParams();
  const { current } = useAuth();
  const root = `/organizations/${org.id}`;
  const path = `${root}/tickets/${id}`;
  const remote = useRemote<Ticket>(path);
  const categories = useRemote<Category[]>(
    `${root}/categories?include_inactive=true`,
  );
  const members = useRemote<Member[]>(`${root}/memberships`);
  const [ticket, setTicket] = useState<Ticket | undefined>();
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState("");
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"reply" | "note">("reply");
  useEffect(() => {
    setTicket(remote.data);
  }, [remote.data]);
  async function mutate(
    suffix: string,
    method: string,
    data: Record<string, unknown>,
    done?: () => void,
  ) {
    if (!ticket || conflict) return;
    setBusy(true);
    setError("");
    try {
      const updated = await api<Ticket>(`${path}${suffix}`, method, {
        ...data,
        expected_version: ticket.version,
      });
      setTicket(updated);
      setRevision((value) => value + 1);
      done?.();
    } catch (err) {
      if (err instanceof ApiError && err.code === "version_conflict")
        setConflict(true);
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  const refresh = () => {
    setConflict(false);
    setError("");
    remote.reload();
    setRevision((value) => value + 1);
  };
  function activityValue(action: string, value: unknown) {
    if (value == null) return "none";
    if (action === "ticket.assignment_changed")
      return (
        members.data?.find((member) => member.id === value)?.full_name ??
        String(value)
      );
    if (action === "ticket.category_changed")
      return (
        categories.data?.find((category) => category.id === value)?.name ??
        String(value)
      );
    return String(value);
  }
  if (!ticket)
    return (
      <>
        <Link to="/app/tickets">← Ticket queue</Link>
        <ErrorText text={remote.error} />
        {remote.loading && <p aria-busy="true">Loading ticket…</p>}
      </>
    );
  const disabled = busy || conflict || remote.loading;
  const self = members.data?.find(
    (value) => value.user_id === current?.user.id,
  );
  return (
    <>
      <Link to="/app/tickets" className="back-link">
        ← Ticket queue
      </Link>
      <div className="page-heading ticket-heading">
        <div>
          <p className="eyebrow">
            Ticket {ticket.id.slice(0, 8)} · Version {ticket.version}
          </p>
          <h1>{ticket.title}</h1>
          <p className="muted">
            {ticket.requester_name}
            {ticket.requester_email ? ` · ${ticket.requester_email}` : ""}
          </p>
        </div>
        <span className={`status-pill status-${ticket.status}`}>
          {ticket.status}
        </span>
      </div>
      <ErrorText
        text={error || remote.error || categories.error || members.error}
      />
      {conflict && (
        <div className="conflict-banner" role="status">
          <strong>Another staff member updated this ticket.</strong>
          <p>
            Your drafts are kept. Reload, review the latest changes, then retry.
          </p>
          <button onClick={refresh} disabled={busy}>
            Reload latest ticket
          </button>
        </div>
      )}
      <div className="ticket-layout">
        <div className="conversation-column">
          <section className="panel">
            <div className="section-heading">
              <h2>Conversation</h2>
              <button className="text-button" disabled={busy} onClick={refresh}>
                Refresh ticket
              </button>
            </div>
            <History<Message>
              path={`${path}/messages`}
              revision={revision}
              render={(item) => (
                <article
                  className={`conversation-message ${item.visibility === "internal" ? "internal-note" : ""}`}
                  key={item.id}
                >
                  <div className="message-heading">
                    <strong>{item.author_name}</strong>
                    <span className="visibility-label">
                      {item.visibility === "internal"
                        ? "Internal note · Staff only"
                        : item.ticket_version === 1
                          ? "Public · Initial request"
                          : "Public reply"}
                    </span>
                  </div>
                  <time dateTime={item.created_at}>
                    {date(item.created_at)}
                  </time>
                  <p className="message-body">{item.body}</p>
                </article>
              )}
            />
          </section>
          <section
            className={`panel composer ${mode === "note" ? "note-composer" : ""}`}
          >
            <h2>Add to conversation</h2>
            <div
              className="composer-tabs"
              role="group"
              aria-label="Message visibility"
            >
              <button
                type="button"
                className={mode === "reply" ? "active" : "secondary"}
                aria-pressed={mode === "reply"}
                onClick={() => setMode("reply")}
              >
                Public reply
              </button>
              <button
                type="button"
                className={mode === "note" ? "active note-tab" : "secondary"}
                aria-pressed={mode === "note"}
                onClick={() => setMode("note")}
              >
                Internal note
              </button>
            </div>
            {mode === "reply" ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void mutate("/replies", "POST", { body: reply }, () =>
                    setReply(""),
                  );
                }}
              >
                <p className="muted">
                  Public conversation record. No email or customer delivery is
                  enabled yet.
                </p>
                <label>
                  Public reply body
                  <textarea
                    rows={5}
                    maxLength={10000}
                    required
                    value={reply}
                    onChange={(event) => setReply(event.target.value)}
                    disabled={!ticket.can_reply}
                  />
                </label>
                <button
                  disabled={disabled || !ticket.can_reply || !reply.trim()}
                >
                  Post public reply
                </button>
                {!ticket.can_reply && (
                  <p className="muted">
                    Reopen this ticket or ask its assigned agent to reply.
                  </p>
                )}
              </form>
            ) : (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void mutate("/notes", "POST", { body: note }, () =>
                    setNote(""),
                  );
                }}
              >
                <p className="note-warning">
                  Staff only. Internal notes are excluded from the public
                  conversation.
                </p>
                <label>
                  Internal note body
                  <textarea
                    rows={5}
                    maxLength={10000}
                    required
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    disabled={!ticket.can_note}
                  />
                </label>
                <button
                  className="note-action"
                  disabled={disabled || !ticket.can_note || !note.trim()}
                >
                  Add internal note
                </button>
                {!ticket.can_note && (
                  <p className="muted">
                    Reopen this ticket or ask its assigned agent to add a note.
                  </p>
                )}
              </form>
            )}
          </section>
          <section className="panel">
            <h2>Activity history</h2>
            <History<Activity>
              path={`${path}/activity`}
              revision={revision}
              render={(item) => (
                <div className="activity-event" key={item.id}>
                  <span className="activity-dot" />
                  <div>
                    <strong>
                      {label(
                        item.action
                          .replace("ticket.", "")
                          .replace("message.", ""),
                      )}
                    </strong>
                    <p>
                      {item.actor_name} · {date(item.created_at)} · v
                      {item.ticket_version}
                    </p>
                    {"from" in item.changes && (
                      <code>
                        {activityValue(item.action, item.changes.from)} →{" "}
                        {activityValue(item.action, item.changes.to)}
                      </code>
                    )}
                  </div>
                </div>
              )}
            />
          </section>
        </div>
        <aside className="ticket-properties panel">
          <h2>Ticket properties</h2>
          <label>
            Ticket status
            <select
              value={ticket.status}
              disabled={disabled || !ticket.allowed_transitions.length}
              onChange={(event) =>
                void mutate("", "PATCH", { status: event.target.value })
              }
            >
              <option value={ticket.status}>{ticket.status}</option>
              {ticket.allowed_transitions.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Ticket priority
            <select
              value={ticket.priority}
              disabled={disabled || !ticket.can_update}
              onChange={(event) =>
                void mutate("", "PATCH", { priority: event.target.value })
              }
            >
              {priorities.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Ticket category
            <select
              value={ticket.category_id ?? ""}
              disabled={disabled || !ticket.can_update}
              onChange={(event) =>
                void mutate("", "PATCH", {
                  category_id: event.target.value || null,
                })
              }
            >
              <option value="">Uncategorized</option>
              {categories.data
                ?.filter(
                  (value) => value.is_active || value.id === ticket.category_id,
                )
                .map((value) => (
                  <option
                    key={value.id}
                    value={value.id}
                    disabled={!value.is_active}
                  >
                    {value.name}
                    {!value.is_active ? " (inactive)" : ""}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Ticket assignee
            <select
              value={ticket.assigned_membership_id ?? ""}
              disabled={disabled || !ticket.can_update}
              onChange={(event) =>
                void mutate("", "PATCH", {
                  assigned_membership_id: event.target.value || null,
                })
              }
            >
              <option value="">Unassigned</option>
              {members.data
                ?.filter(
                  (value) =>
                    value.id === ticket.assigned_membership_id ||
                    (value.is_active &&
                      (ticket.can_assign_others || value.id === self?.id)),
                )
                .map((value) => (
                  <option
                    key={value.id}
                    value={value.id}
                    disabled={!value.is_active}
                  >
                    {value.full_name}
                    {!value.is_active ? " (disabled)" : ""}
                  </option>
                ))}
            </select>
          </label>
          {!ticket.can_update && (
            <p className="muted">
              {ticket.status === "closed"
                ? "A manager must reopen this ticket before editing."
                : "Assigned to another staff member. You have read access."}
            </p>
          )}
          <dl className="ticket-dates">
            <dt>Created</dt>
            <dd>{date(ticket.created_at)}</dd>
            <dt>Last updated</dt>
            <dd>{date(ticket.updated_at)}</dd>
            <dt>First response</dt>
            <dd>
              {ticket.first_response_at
                ? date(ticket.first_response_at)
                : "Awaiting reply"}
            </dd>
            {ticket.resolved_at && (
              <>
                <dt>Resolved</dt>
                <dd>{date(ticket.resolved_at)}</dd>
              </>
            )}
            {ticket.closed_at && (
              <>
                <dt>Closed</dt>
                <dd>{date(ticket.closed_at)}</dd>
              </>
            )}
          </dl>
        </aside>
      </div>
    </>
  );
}

function History<T extends { id: string }>({
  path,
  revision,
  render,
}: {
  path: string;
  revision: number;
  render: (item: T) => React.ReactNode;
}) {
  const [data, setData] = useState<T[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [more, setMore] = useState(0);
  useEffect(() => {
    setMore(0);
  }, [path, revision]);
  useEffect(() => {
    let active = true;
    setBusy(true);
    setError("");
    void api<Timeline<T>>(`${path}?after_version=${more}`)
      .then((result) => {
        if (active) {
          setData((previous) =>
            more
              ? [
                  ...previous.filter(
                    (item) => !result.items.some((next) => next.id === item.id),
                  ),
                  ...result.items,
                ]
              : result.items,
          );
          setCursor(result.next_after_version);
        }
      })
      .catch((err) => {
        if (active) setError(message(err));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [path, revision, more]);
  return (
    <>
      <ErrorText text={error} />
      {!data.length && busy ? (
        <p aria-busy="true">Loading history…</p>
      ) : !data.length ? (
        <p className="empty-state">No activity yet.</p>
      ) : (
        data.map(render)
      )}
      {cursor !== null && (
        <button
          className="secondary"
          disabled={busy}
          onClick={() => setMore(cursor)}
        >
          Load more
        </button>
      )}
    </>
  );
}
