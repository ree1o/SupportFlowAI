import { StrictMode, useEffect, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";
import {
  api,
  ApiError,
  type CurrentUser,
  type Member,
  type Organization,
  type Role,
} from "./api";
import { Health } from "./Health";
import { AuthProvider, useAuth, ErrorText, message } from "./auth";
import { Sidebar } from "./Sidebar";
import { SupportApp } from "./support/SupportApp";
import "./styles.css";

function AccountForm({ register = false }: { register?: boolean }) {
  const { current, loading, setCurrent } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (loading) return <main aria-busy="true">Loading your workspace…</main>;
  if (current) return <Navigate to="/app/tickets" replace />;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const data = new FormData(event.currentTarget);
    const payload = {
      email: String(data.get("email")),
      password: String(data.get("password")),
      ...(register
        ? {
            full_name: String(data.get("name")),
            organization: {
              name: String(data.get("organization")),
              slug: String(data.get("slug")),
            },
          }
        : {}),
    };
    try {
      setCurrent(
        await api<CurrentUser>(
          register ? "/auth/register" : "/auth/login",
          "POST",
          payload,
        ),
      );
      navigate("/app/tickets", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 422
          ? "Check your fields. Use a valid email, a 12–128 character password, and a lowercase workspace address."
          : message(err),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="account-page">
      <Link to="/login" className="wordmark">
        <span className="brand-mark" aria-hidden="true">
          S
        </span>
        SupportFlow AI
      </Link>
      <div className="account-grid">
        <section className="welcome">
          <p className="eyebrow">Your team, connected</p>
          <h1>
            Better support
            <br />
            starts together.
          </h1>
          <p>
            One account. Every workspace you belong to. A focused place to
            organize your team.
          </p>
          <Link to="/status">System status ↗</Link>
        </section>
        <section className="card account-card">
          <h2>{register ? "Create your workspace" : "Welcome back"}</h2>
          <p className="muted">
            {register
              ? "You’ll be the first owner of your organization."
              : "Sign in to manage your organizations and team."}
          </p>
          <form onSubmit={submit}>
            {register && (
              <label>
                Your name
                <input
                  name="name"
                  autoComplete="name"
                  maxLength={100}
                  required
                />
              </label>
            )}
            <label>
              Email
              <input
                name="email"
                type="email"
                autoComplete="email"
                maxLength={254}
                required
              />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete={register ? "new-password" : "current-password"}
                minLength={12}
                maxLength={128}
                required
              />
            </label>
            {register && (
              <>
                <label>
                  Organization name
                  <input name="organization" maxLength={100} required />
                </label>
                <label>
                  Workspace address
                  <input
                    aria-label="Workspace address"
                    aria-describedby="workspace-address-help"
                    name="slug"
                    pattern="[a-z0-9]+(-[a-z0-9]+)*"
                    minLength={2}
                    maxLength={60}
                    placeholder="your-company"
                    required
                  />
                  <small id="workspace-address-help">
                    Lowercase letters, numbers, and single hyphens.
                  </small>
                </label>
              </>
            )}
            <ErrorText text={error} />
            <button disabled={busy} type="submit">
              {busy
                ? "Please wait…"
                : register
                  ? "Create account & workspace"
                  : "Sign in"}
            </button>
          </form>
          <p className="account-link">
            {register ? "Already have an account?" : "New to SupportFlow?"}{" "}
            <Link to={register ? "/login" : "/register"}>
              {register ? "Sign in" : "Create a workspace"}
            </Link>
          </p>
        </section>
      </div>
    </main>
  );
}

function Protected() {
  const { current, loading } = useAuth();
  if (loading) return <main aria-busy="true">Loading your workspace…</main>;
  return current ? <Outlet /> : <Navigate to="/login" replace />;
}

function Workspace() {
  const { current, setCurrent, reload } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(() =>
    new URLSearchParams(window.location.search).has("new"),
  );
  const [revision, setRevision] = useState(0);
  const [members, setMembers] = useState<Member[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const selected = current?.organizations.find(
    (org) => org.id === current.selected_organization_id,
  );
  useEffect(() => {
    let active = true;
    setMembers([]);
    setError("");
    if (!selected) {
      setMembersLoading(false);
      return;
    }
    setMembersLoading(true);
    void api<Member[]>(`/organizations/${selected.id}/memberships`)
      .then((value) => {
        if (active) setMembers(value);
      })
      .catch((err) => {
        if (active) {
          setError(message(err));
          if (err instanceof ApiError && err.status === 404)
            void reload().catch(() => {});
        }
      })
      .finally(() => {
        if (active) setMembersLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected?.id, revision]);
  if (!current) return null;

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  async function switchOrg(id: string) {
    await act(async () => {
      setMembers([]);
      setCurrent(await api<CurrentUser>(`/organizations/${id}/select`, "POST"));
    });
  }
  async function createOrg(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await act(async () => {
      const org = await api<Organization>("/organizations", "POST", {
        name: data.get("name"),
        slug: data.get("slug"),
      });
      setCurrent(
        await api<CurrentUser>(`/organizations/${org.id}/select`, "POST"),
      );
      form.reset();
      setCreating(false);
    });
  }
  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    await act(async () => {
      await api(`/organizations/${selected.id}/memberships`, "POST", {
        email: data.get("email"),
        role: data.get("role"),
      });
      form.reset();
      setRevision((value) => value + 1);
    });
  }
  async function change(member: Member, role: Role, is_active: boolean) {
    if (!selected) return;
    await act(async () => {
      await api(
        `/organizations/${selected.id}/memberships/${member.id}`,
        "PATCH",
        { role, is_active },
      );
      await reload();
      setRevision((value) => value + 1);
    });
  }
  const canManage =
    selected?.role === "owner" || selected?.role === "administrator";
  return (
    <div className="workspace-shell">
      <Sidebar
        busy={busy}
        onSelect={switchOrg}
        onCreate={() => setCreating((value) => !value)}
      />
      <main className="workspace-main">
        <div className="page-heading">
          <div>
            <p className="eyebrow">Organization settings</p>
            <h1>{selected?.name ?? "Choose your workspace"}</h1>
            <p className="muted">
              Manage your workspace and the people who belong to it.
            </p>
          </div>
          {selected && <span className="role-badge">{selected.role}</span>}
        </div>
        <ErrorText text={error} />
        {creating && (
          <section className="panel">
            <h2>New workspace</h2>
            <form className="inline-form" onSubmit={createOrg}>
              <label>
                Name
                <input name="name" maxLength={100} required />
              </label>
              <label>
                Workspace address
                <input
                  aria-label="Workspace address"
                  aria-describedby="workspace-address-help"
                  name="slug"
                  minLength={2}
                  maxLength={60}
                  pattern="[a-z0-9]+(-[a-z0-9]+)*"
                  required
                />
              </label>
              <button disabled={busy}>Create workspace</button>
            </form>
          </section>
        )}
        {!selected && (
          <section className="panel">
            <h2>No workspace selected</h2>
            <p>
              Your access may have changed. Select another workspace or create a
              new one.
            </p>
          </section>
        )}
        {selected && (
          <>
            <section className="panel">
              <h2>Workspace details</h2>
              <p className="muted">
                Workspace address: <strong>{selected.slug}</strong>
              </p>
              {canManage ? (
                <form
                  key={selected.id}
                  className="inline-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const data = new FormData(event.currentTarget);
                    void act(async () => {
                      await api(`/organizations/${selected.id}`, "PATCH", {
                        name: data.get("name"),
                      });
                      await reload();
                    });
                  }}
                >
                  <label>
                    Organization name
                    <input
                      name="name"
                      defaultValue={selected.name}
                      maxLength={100}
                      required
                    />
                  </label>
                  <button disabled={busy}>Save name</button>
                </form>
              ) : (
                <p>You have read access to organization settings.</p>
              )}
            </section>
            <section className="panel">
              <div className="section-heading">
                <h2>Team members</h2>
                <span className="muted">{members.length} memberships</span>
              </div>
              <p className="muted">
                Owners manage all roles. Administrators manage agents. At least
                one active owner is required.
              </p>
              {canManage && (
                <form className="inline-form member-form" onSubmit={addMember}>
                  <label>
                    Existing account email
                    <input
                      type="email"
                      name="email"
                      maxLength={254}
                      required
                      placeholder="colleague@example.com"
                    />
                  </label>
                  <label>
                    Role
                    <select name="role" defaultValue="agent">
                      <option value="agent">Agent</option>
                      {selected.role === "owner" && (
                        <>
                          <option value="administrator">Administrator</option>
                          <option value="owner">Owner</option>
                        </>
                      )}
                    </select>
                  </label>
                  <button disabled={busy}>Add member</button>
                </form>
              )}
              {membersLoading ? (
                <p aria-busy="true">Loading team…</p>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Person</th>
                        <th>Role</th>
                        <th>Access</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {members.map((member) => {
                        const manageable =
                          selected.role === "owner" ||
                          (selected.role === "administrator" &&
                            member.role === "agent");
                        return (
                          <tr key={member.id}>
                            <td>
                              <strong>{member.full_name}</strong>
                              <span className="member-email">
                                {member.email}
                                {member.user_id === current.user.id
                                  ? " (you)"
                                  : ""}
                              </span>
                            </td>
                            <td>
                              {selected.role === "owner" ? (
                                <select
                                  aria-label={`Role for ${member.email}`}
                                  value={member.role}
                                  disabled={busy}
                                  onChange={(event) =>
                                    void change(
                                      member,
                                      event.target.value as Role,
                                      member.is_active,
                                    )
                                  }
                                >
                                  <option value="owner">Owner</option>
                                  <option value="administrator">
                                    Administrator
                                  </option>
                                  <option value="agent">Agent</option>
                                </select>
                              ) : (
                                <span className="role-badge">
                                  {member.role}
                                </span>
                              )}
                            </td>
                            <td>
                              <span
                                className={
                                  member.is_active ? "access-active" : "muted"
                                }
                              >
                                {member.is_active ? "Active" : "Disabled"}
                              </span>
                            </td>
                            <td>
                              {manageable ? (
                                <button
                                  className="text-button"
                                  disabled={busy}
                                  onClick={() =>
                                    void change(
                                      member,
                                      member.role,
                                      !member.is_active,
                                    )
                                  }
                                >
                                  {member.is_active ? "Disable" : "Enable"}
                                </button>
                              ) : (
                                <span className="muted">—</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
        <section className="session-panel">
          <span className="muted">
            Session expires {new Date(current.expires_at).toLocaleString()}
          </span>
          <button
            className="text-button"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                setCurrent(await api<CurrentUser>("/auth/renew", "POST"));
              })
            }
          >
            Renew session
          </button>
        </section>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/status" element={<Health />} />
        <Route
          path="*"
          element={
            <AuthProvider>
              <Routes>
                <Route path="/login" element={<AccountForm />} />
                <Route path="/register" element={<AccountForm register />} />
                <Route element={<Protected />}>
                  <Route path="/app" element={<Workspace />} />
                  <Route path="/app/*" element={<SupportApp />} />
                </Route>
                <Route
                  path="*"
                  element={<Navigate to="/app/tickets" replace />}
                />
              </Routes>
            </AuthProvider>
          }
        />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
