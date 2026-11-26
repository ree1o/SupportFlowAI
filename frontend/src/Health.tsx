import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "./styles.css";

type Component = { status: string; detail: string };
type Diagnostics = {
  overall: "operational" | "degraded";
  api: Component;
  database: Component;
  redis: Component;
  ticket_worker: Component;
  ai_worker: Component;
  ticket_embedding_model: Component;
  knowledge_embedding_model: Component;
  local_generation: Component;
};

const labels: Record<keyof Omit<Diagnostics, "overall">, string> = {
  api: "Application API",
  database: "PostgreSQL and migrations",
  redis: "Queue and rate limit store",
  ticket_worker: "Ticket and notification worker",
  ai_worker: "AI and knowledge indexing worker",
  ticket_embedding_model: "Ticket retrieval model files",
  knowledge_embedding_model: "Knowledge indexing model files",
  local_generation: "Optional local generation",
};

export function Health() {
  const [checks, setChecks] = useState<Diagnostics | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    setLoading(true);
    setError("");
    void fetch("/api/health/components", { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("System diagnostics are temporarily unavailable.");
        return (await response.json()) as Diagnostics;
      })
      .then(setChecks)
      .catch(() => setError("Unable to load diagnostics. Check the API and try again."))
      .finally(() => {
        window.clearTimeout(timeout);
        setLoading(false);
      });
    return () => {
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [attempt]);

  return (
    <main className="health-page">
      <header className="health-header">
        <Link className="wordmark" to="/login">
          <span className="brand-mark" aria-hidden="true">S</span>
          SupportFlow AI
        </Link>
        <Link to="/login">Sign in</Link>
      </header>
      <section className="health-overview panel">
        <p className="eyebrow">Service diagnostics</p>
        <h1>System status</h1>
        <p className="muted">Core helpdesk services are reported separately from optional AI features.</p>
        <div className="health-summary" aria-live="polite" aria-busy={loading}>
          <span className={`health-indicator ${checks?.overall ?? "checking"}`} aria-hidden="true" />
          <strong>{loading ? "Checking services…" : checks?.overall === "operational" ? "Core services operational" : checks ? "Some core services need attention" : "Checking services"}</strong>
          {checks?.local_generation.status === "disabled" && <span className="muted">Local generation is disabled</span>}
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="health-grid" aria-live="polite">
          {(Object.keys(labels) as (keyof typeof labels)[]).map((key) => {
            const component = checks?.[key];
            const status = component?.status ?? "checking";
            return (
              <article className="health-component" key={key}>
                <div className="health-component-heading">
                  <strong>{labels[key]}</strong>
                  <span className={`status ${status}`}>{status.replaceAll("_", " ")}</span>
                </div>
                <p className="muted">{component?.detail ?? (loading ? "Checking…" : "No status available")}</p>
              </article>
            );
          })}
        </div>
        <div className="health-actions">
          <button className="secondary" type="button" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>
            {loading ? "Checking…" : "Check again"}
          </button>
          <Link to="/api/docs">API documentation</Link>
        </div>
      </section>
      <footer className="muted">AI availability does not determine whether tickets and customer conversations can be managed.</footer>
    </main>
  );
}
