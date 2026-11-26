import { Fragment, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Organization } from "../api";
import { ErrorText, message } from "../auth";
import { useRemote } from "../support/remote";

type Article = {
  id: string;
  slug: string;
  title: string;
  content: string;
  lifecycle: "draft" | "published" | "archived";
  current_revision: number;
  published_revision: number | null;
  index_status: "not_published" | "pending" | "indexed" | "failed" | "stale";
  created_at: string;
  updated_at: string;
};
type Page = { items: Article[]; total: number; page: number; page_size: number };

export function KnowledgeBase({ org }: { org: Organization }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newArticle, setNewArticle] = useState(false);
  const base = `/organizations/${org.id}/knowledge/articles`;
  const list = useRemote<Page>(id ? null : `${base}?page=1&page_size=50&q=${encodeURIComponent(query)}`);
  const detail = useRemote<Article>(id ? `${base}/${id}` : null);
  const canManage = org.role === "owner" || org.role === "administrator";

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const article = await api<Article>(base, "POST", {
        slug: String(form.get("slug")),
        title: String(form.get("title")),
        content: String(form.get("content")),
      });
      setNewArticle(false);
      navigate(`/app/knowledge/${article.id}`);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  if (id) {
    return (
      <ArticleEditor
        org={org}
        article={detail.data}
        loading={detail.loading}
        loadError={detail.error}
        canManage={canManage}
        onReload={detail.reload}
      />
    );
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{org.name} · Published guidance</p>
          <h1>Knowledge Base</h1>
          <p className="muted">Keep approved support instructions in one versioned, searchable place.</p>
        </div>
        {canManage && <button onClick={() => setNewArticle((value) => !value)}>{newArticle ? "Cancel" : "New article"}</button>}
      </div>
      <ErrorText text={error || list.error} />
      {newArticle && canManage && (
        <section className="panel">
          <h2>Start a draft</h2>
          <form className="knowledge-editor" onSubmit={(event) => void create(event)}>
            <label>URL slug<input name="slug" required minLength={2} maxLength={120} pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="password-reset" /></label>
            <label>Article title<input name="title" required maxLength={200} /></label>
            <label>Instructions in Markdown<textarea name="content" required minLength={1} maxLength={50000} rows={10} /></label>
            <button disabled={busy}>{busy ? "Creating draft…" : "Create draft"}</button>
          </form>
        </section>
      )}
      <section className="panel">
        <div className="section-heading">
          <div><h2>Articles</h2><p className="muted">{list.data?.total ?? 0} entries</p></div>
          <label className="knowledge-search">Search titles<input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={200} /></label>
        </div>
        {list.loading && <p role="status">Loading knowledge articles…</p>}
        {!list.loading && list.data?.items.length === 0 && <p className="muted">No articles yet. Owners and Administrators can create the first draft.</p>}
        {list.data && list.data.items.length > 0 && (
          <div className="knowledge-list">
            {list.data.items.map((article) => (
              <Link className="knowledge-item" key={article.id} to={`/app/knowledge/${article.id}`}>
                <span><strong>{article.title}</strong><small>/{article.slug} · revision {article.published_revision ?? article.current_revision}</small></span>
                <span className={`knowledge-state state-${article.lifecycle}`}>{article.lifecycle}</span>
                <span className="muted">Index: {article.index_status}</span>
              </Link>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

function ArticleEditor({
  org,
  article,
  loading,
  loadError,
  canManage,
  onReload,
}: {
  org: Organization;
  article?: Article;
  loading: boolean;
  loadError: string;
  canManage: boolean;
  onReload: () => void;
}) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const base = `/organizations/${org.id}/knowledge/articles/${article?.id}`;
  useEffect(() => {
    setTitle(article?.title ?? "");
    setContent(article?.content ?? "");
  }, [article?.id, article?.current_revision, article?.published_revision]);

  async function mutate(action: "save" | "publish" | "archive" | "reindex") {
    if (!article) return;
    if (action === "archive" && !window.confirm("Archive this article? It will disappear from staff knowledge retrieval.")) return;
    setBusy(true);
    setError("");
    try {
      if (action === "save") {
        await api<Article>(base, "PATCH", {
          expected_revision: article.current_revision,
          title,
          content,
        });
      } else if (action === "publish") {
        await api<Article>(`${base}/publish`, "POST", { expected_revision: article.current_revision });
      } else {
        await api<Article>(`${base}/${action}`, "POST");
      }
      onReload();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Link className="back-link" to="/app/knowledge">← Knowledge Base</Link>
      {loading && <p role="status">Loading article…</p>}
      <ErrorText text={error || loadError} />
      {article && (
        <>
          <div className="page-heading">
            <div><p className="eyebrow">{org.name} · {article.lifecycle} · revision {article.current_revision}</p><h1>{article.title}</h1><p className="muted">Index status: {article.index_status}</p></div>
            {canManage && <span className="role-badge">Manager controls</span>}
          </div>
          <section className="panel">
            {canManage && article.lifecycle !== "archived" ? (
              <form className="knowledge-editor" onSubmit={(event) => { event.preventDefault(); void mutate("save"); }}>
                <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={200} /></label>
                <label>Instructions in Markdown<textarea value={content} onChange={(event) => setContent(event.target.value)} required maxLength={50000} rows={18} /></label>
                <section className="knowledge-preview" aria-label="Markdown preview">
                  <h3>Preview</h3>
                  <MarkdownContent content={content} />
                </section>
                <div className="form-actions">
                  <button disabled={busy}>{busy ? "Saving…" : "Save new revision"}</button>
                  <button type="button" className="secondary" disabled={busy || (article.lifecycle === "published" && article.current_revision === article.published_revision)} onClick={() => void mutate("publish")}>{article.lifecycle === "published" ? "Publish current revision" : "Publish article"}</button>
                  {article.index_status === "failed" && <button type="button" className="secondary" disabled={busy} onClick={() => void mutate("reindex")}>Retry indexing</button>}
                  <button type="button" className="secondary" disabled={busy} onClick={() => void mutate("archive")}>Archive</button>
                </div>
                <p className="muted">Saving creates draft revision {article.current_revision + 1}. The currently published revision {article.published_revision ?? "does not exist"} remains available until you publish the new revision.</p>
              </form>
            ) : (
              <article className="knowledge-content"><MarkdownContent content={article.content} /></article>
            )}
          </section>
        </>
      )}
    </>
  );
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="markdown-content">
      {content.split(/\r?\n/).map((line, index) => {
        if (!line.trim()) return <Fragment key={index}><br /></Fragment>;
        const heading = /^(#{1,3})\s+(.+)$/.exec(line);
        if (heading) return <Heading key={index} level={heading[1].length}>{inlineMarkdown(heading[2])}</Heading>;
        if (/^[-*]\s+/.test(line)) return <p className="markdown-list-item" key={index}>• {inlineMarkdown(line.replace(/^[-*]\s+/, ""))}</p>;
        if (/^>\s?/.test(line)) return <blockquote key={index}>{inlineMarkdown(line.replace(/^>\s?/, ""))}</blockquote>;
        return <p key={index}>{inlineMarkdown(line)}</p>;
      })}
    </div>
  );
}

function Heading({ level, children }: { level: number; children: ReactNode }) {
  if (level === 1) return <h1>{children}</h1>;
  if (level === 2) return <h2>{children}</h2>;
  return <h3>{children}</h3>;
}

function inlineMarkdown(text: string) {
  const pattern = /(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const value = match[0];
    const offset = match.index ?? 0;
    if (offset > cursor) nodes.push(text.slice(cursor, offset));
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(value);
    if (link) {
      let href: string | undefined;
      try {
        const parsed = new URL(link[2], window.location.origin);
        if (parsed.protocol === "https:" || parsed.protocol === "http:") href = parsed.href;
      } catch {
        href = undefined;
      }
      nodes.push(href ? <a key={offset} href={href} target="_blank" rel="noreferrer">{link[1]}</a> : link[1]);
    } else if (value.startsWith("**")) nodes.push(<strong key={offset}>{value.slice(2, -2)}</strong>);
    else if (value.startsWith("`")) nodes.push(<code key={offset}>{value.slice(1, -1)}</code>);
    else nodes.push(<em key={offset}>{value.slice(1, -1)}</em>);
    cursor = offset + value.length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}
