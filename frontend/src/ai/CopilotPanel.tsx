import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Organization } from "../api";
import { ErrorText, message } from "../auth";

type Citation = {
  article_id: string;
  revision: number;
  chunk_index: number;
  title: string;
  slug: string;
  excerpt: string;
  relevance: number;
};
type Run = {
  id: string;
  action: "reply" | "summary" | "agentic";
  status: "queued" | "running" | "completed" | "failed" | "stale" | "cancelled";
  ticket_version: number;
  result: string | null;
  insufficient_evidence: boolean;
  citations: Citation[];
  tools_used: string[];
  warnings: string[];
  error_code: string | null;
  latency_ms: number | null;
};
type Status = { enabled: boolean; state: string; model: string; digest: string | null };

export function CopilotPanel({
  org,
  ticketId,
  ticketVersion,
  onInsert,
}: {
  org: Organization;
  ticketId: string;
  ticketVersion: number;
  onInsert: (draft: string) => void;
}) {
  const [status, setStatus] = useState<Status>();
  const [run, setRun] = useState<Run>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const root = `/organizations/${org.id}/ai`;

  useEffect(() => {
    let active = true;
    void api<Status>(`${root}/status`)
      .then((value) => active && setStatus(value))
      .catch((err: unknown) => active && setError(message(err)));
    return () => {
      active = false;
    };
  }, [root]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void api<Run>(`${root}/runs/${run.id}`)
        .then((value) => active && setRun(value))
        .catch((err: unknown) => active && setError(message(err)));
    }, 2000);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [root, run]);

  async function generate(action: Run["action"]) {
    setBusy(true);
    setError("");
    setRun(undefined);
    try {
      const value = await api<Run>(
        `${root}/tickets/${ticketId}/runs`,
        "POST",
        { action, expected_ticket_version: ticketVersion },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      setRun(value);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!run) return;
    setError("");
    try {
      setRun(await api<Run>(`${root}/runs/${run.id}/cancel`, "POST"));
    } catch (err) {
      setError(message(err));
    }
  }

  const unavailable = !status?.enabled || status.state !== "available";
  return (
    <section className="panel copilot-panel" aria-label="AI Copilot">
      <div className="section-heading copilot-heading">
        <div>
          <p className="eyebrow">Private local assistance</p>
          <h2>AI Copilot</h2>
        </div>
        <span className={`knowledge-state state-${status?.state ?? "pending"}`}>
          {status?.state ?? "checking model"}
        </span>
      </div>
      <p className="muted">
        Drafts use public conversation context and published Knowledge Base
        guidance. Nothing is sent to the customer automatically.
      </p>
      <ErrorText text={error} />
      {!status && !error && <p role="status" aria-busy="true">Checking local model availability…</p>}
      {status && (
        <p className="copilot-model">Local model: {status.model}</p>
      )}
      <div className="copilot-actions">
        <button
          type="button"
          disabled={busy || unavailable || ticketVersion < 1}
          onClick={() => void generate("reply")}
        >
          Generate grounded reply
        </button>
        <button
          className="secondary"
          type="button"
          disabled={busy || unavailable || ticketVersion < 1}
          onClick={() => void generate("summary")}
        >
          Summarize conversation
        </button>
        <button
          className="secondary"
          type="button"
          disabled={busy || unavailable || ticketVersion < 1}
          onClick={() => void generate("agentic")}
        >
          Analyze with read-only tools
        </button>
      </div>
      {status && unavailable && (
        <p className="muted" role="status">
          Local generation is unavailable. Tickets and Knowledge Base remain usable.
        </p>
      )}
      {busy && <p role="status" aria-busy="true">Starting a private generation job…</p>}
      {run && ["queued", "running"].includes(run.status) && (
        <div className="copilot-job" role="status" aria-live="polite">
          <span>{run.status === "queued" ? "Waiting for the local model…" : "Generating locally…"}</span>
          <button className="text-button" type="button" onClick={() => void cancel()}>Cancel</button>
        </div>
      )}
      {run?.status === "failed" && <p className="error" role="alert">Generation failed ({run.error_code ?? "unknown"}). You can retry.</p>}
      {run?.status === "stale" && <p className="error" role="alert">The ticket or access changed while this was running. Refresh the ticket before using another draft.</p>}
      {run?.status === "cancelled" && <p className="muted" role="status">Generation cancelled.</p>}
      {run?.status === "completed" && (
        <div className="copilot-result" aria-live="polite">
          {run.insufficient_evidence && (
            <p className="copilot-abstention" role="status">
              The published guidance does not provide enough evidence for a grounded answer.
            </p>
          )}
          {run.result && <pre className="copilot-answer">{run.result}</pre>}
          {run.action === "reply" && run.result && !run.insufficient_evidence && (
            <button type="button" onClick={() => onInsert(run.result!)}>
              Insert draft into public reply
            </button>
          )}
          {run.citations.length > 0 && (
            <div className="copilot-citations">
              <h3>Published sources</h3>
              <ul>
                {run.citations.map((source) => (
                  <li key={`${source.article_id}:${source.revision}:${source.chunk_index}`}>
                    <Link to={`/app/knowledge/${source.article_id}`}>
                      {source.title} · revision {source.revision}
                    </Link>
                    <p>{source.excerpt}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {run.tools_used.length > 0 && (
            <p className="muted">Read-only tools used: {run.tools_used.join(", ")}</p>
          )}
          {run.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}
          {run.latency_ms !== null && <small className="muted">Local generation · {run.latency_ms} ms</small>}
        </div>
      )}
    </section>
  );
}
