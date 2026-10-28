import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type CheckState = 'checking' | 'available' | 'unavailable';
type Checks = { api: CheckState; database: CheckState };

async function check(path: string, expected: string, signal: AbortSignal): Promise<CheckState> {
  try {
    const response = await fetch(path, { signal, cache: 'no-store' });
    if (!response.ok) return 'unavailable';
    const body: unknown = await response.json();
    return typeof body === 'object' && body !== null && 'status' in body && body.status === expected
      ? 'available' : 'unavailable';
  } catch {
    return 'unavailable';
  }
}

function App() {
  const [checks, setChecks] = useState<Checks>({ api: 'checking', database: 'checking' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    setChecks({ api: 'checking', database: 'checking' });
    void Promise.all([
      check('/api/health/live', 'ok', controller.signal),
      check('/api/health/ready', 'ready', controller.signal),
    ]).then(([api, database]) => {
      if (active) setChecks({ api, database });
      window.clearTimeout(timeout);
    });
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [attempt]);

  const loading = checks.api === 'checking' || checks.database === 'checking';
  return (
    <main>
      <header><span className="brand-mark" aria-hidden="true">S</span> SupportFlow AI</header>
      <section className="card">
        <p className="eyebrow">Release 0.1 · Milestone M1</p>
        <h1>A reliable foundation<br />for customer support.</h1>
        <p className="intro">The application baseline is ready to verify. Helpdesk workflows arrive in the next milestones.</p>
        <div className="checks" aria-live="polite" aria-busy={loading}>
          <Status label="API liveness" state={checks.api} />
          <Status label="Database & migrations" state={checks.database} />
        </div>
        {checks.database === 'unavailable' && <p className="hint">Readiness is unavailable. Check PostgreSQL, migrations, and API logs.</p>}
        <div className="actions">
          <button disabled={loading} onClick={() => setAttempt(value => value + 1)}>{loading ? 'Checking…' : 'Check again'}</button>
          <a href="/api/docs">Explore API documentation <span aria-hidden="true">↗</span></a>
        </div>
      </section>
      <footer>Application baseline only · No accounts, tickets, or AI features yet.</footer>
    </main>
  );
}

function Status({ label, state }: { label: string; state: CheckState }) {
  return <div className="status-row"><span>{label}</span><span className={`status ${state}`}>{state}</span></div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
