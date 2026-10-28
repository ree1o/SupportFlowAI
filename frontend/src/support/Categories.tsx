import { useState, type FormEvent } from "react";
import { api, type Organization } from "../api";
import { ErrorText, message } from "../auth";
import { useRemote } from "./remote";
import type { Category } from "./types";

export function Categories({ org }: { org: Organization }) {
  const root = `/organizations/${org.id}/categories`;
  const remote = useRemote<Category[]>(`${root}?include_inactive=true`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const manager = org.role !== "agent";
  async function mutate(path: string, method: string, data: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(path, method, data);
      remote.reload();
      return true;
    } catch (err) {
      setError(message(err));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (await mutate(root, "POST", { name: new FormData(form).get("name") }))
      form.reset();
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Workspace configuration</p>
          <h1>Categories</h1>
          <p className="muted">
            A simple vocabulary for routing and finding support work.
          </p>
        </div>
      </div>
      <ErrorText text={error || remote.error} />
      {manager ? (
        <section className="panel">
          <h2>Create category</h2>
          <form className="inline-form" onSubmit={create}>
            <label>
              Category name
              <input name="name" maxLength={80} required />
            </label>
            <button disabled={busy}>Create category</button>
          </form>
        </section>
      ) : (
        <p className="muted">Owners and administrators manage categories.</p>
      )}
      <section className="panel">
        <h2>Ticket categories</h2>
        {remote.loading && !remote.data ? (
          <p>Loading categories…</p>
        ) : !remote.data?.length ? (
          <p className="empty-state">
            No categories yet. Tickets can be uncategorized.
          </p>
        ) : (
          <div className="category-list">
            {remote.data.map((category) => (
              <div
                className="category-row"
                key={`${category.id}-${category.name}`}
              >
                <form
                  className="inline-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void mutate(`${root}/${category.id}`, "PATCH", {
                      name: new FormData(event.currentTarget).get("name"),
                    });
                  }}
                >
                  <label>
                    Category name
                    <input
                      aria-label={`Category name for ${category.name}`}
                      name="name"
                      defaultValue={category.name}
                      disabled={!manager || busy}
                      maxLength={80}
                      required
                    />
                  </label>
                  <span
                    className={category.is_active ? "access-active" : "muted"}
                  >
                    {category.is_active ? "Active" : "Inactive"}
                  </span>
                  {manager && (
                    <>
                      <button className="secondary" disabled={busy}>
                        Rename
                      </button>
                      <button
                        type="button"
                        className="text-button"
                        disabled={busy}
                        onClick={() =>
                          void mutate(`${root}/${category.id}`, "PATCH", {
                            is_active: !category.is_active,
                          })
                        }
                      >
                        {category.is_active ? "Deactivate" : "Reactivate"}
                      </button>
                    </>
                  )}
                </form>
              </div>
            ))}
          </div>
        )}
        <p className="muted">
          Deactivation prevents new use and preserves historical ticket links.
        </p>
      </section>
    </>
  );
}
