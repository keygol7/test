import { useMemo, useState } from "react";

const defaultApiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function httpJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    throw new Error(payload?.detail || `Request failed (${response.status})`);
  }

  return payload;
}

export default function App() {
  const [apiBase, setApiBase] = useState(defaultApiBase);
  const [health, setHealth] = useState("");
  const [users, setUsers] = useState([]);
  const [situations, setSituations] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const [userEmail, setUserEmail] = useState("");
  const [userDisplayName, setUserDisplayName] = useState("");

  const [situationUserId, setSituationUserId] = useState("");
  const [situationTitle, setSituationTitle] = useState("");
  const [situationQuery, setSituationQuery] = useState("");
  const [situationDescription, setSituationDescription] = useState("");

  const [articleUrl, setArticleUrl] = useState("");
  const [articleTitle, setArticleTitle] = useState("");
  const [articleSource, setArticleSource] = useState("");
  const [articleSituationIds, setArticleSituationIds] = useState("");

  const [dashboardSituationId, setDashboardSituationId] = useState("");

  const baseUrl = useMemo(() => apiBase.replace(/\/$/, ""), [apiBase]);

  async function run(action) {
    setLoading(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      setMessage(error.message || "Unexpected error");
    } finally {
      setLoading(false);
    }
  }

  function parseUuidCsv(text) {
    return text
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function headline(value) {
    if (!value || value.length === 0) {
      return "No headlines yet";
    }
    return value.join(" | ");
  }

  return (
    <main className="layout">
      <section className="card">
        <h1>News Situation Dashboard</h1>
        <p className="muted">Connect to your FastAPI backend and seed test data.</p>
        <label>
          API Base URL
          <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
        </label>
        <div className="row">
          <button
            disabled={loading}
            onClick={() =>
              run(async () => {
                const data = await httpJson(`${baseUrl}/health`);
                setHealth(`${data.status} (${data.service})`);
              })
            }
          >
            Check Health
          </button>
          <button
            disabled={loading}
            onClick={() =>
              run(async () => {
                const data = await httpJson(`${baseUrl}/users`);
                setUsers(data);
              })
            }
          >
            Load Users
          </button>
          <button
            disabled={loading}
            onClick={() =>
              run(async () => {
                const data = await httpJson(`${baseUrl}/situations`);
                setSituations(data);
              })
            }
          >
            Load Situations
          </button>
        </div>
        {health ? <p className="ok">Health: {health}</p> : null}
        {message ? <p className="err">{message}</p> : null}
      </section>

      <section className="card">
        <h2>Create User</h2>
        <label>
          Email
          <input
            value={userEmail}
            onChange={(e) => setUserEmail(e.target.value)}
            placeholder="you@example.com"
          />
        </label>
        <label>
          Display Name
          <input
            value={userDisplayName}
            onChange={(e) => setUserDisplayName(e.target.value)}
            placeholder="Your Name"
          />
        </label>
        <button
          disabled={loading}
          onClick={() =>
            run(async () => {
              const created = await httpJson(`${baseUrl}/users`, {
                method: "POST",
                body: JSON.stringify({ email: userEmail, display_name: userDisplayName })
              });
              setUsers((prev) => [created, ...prev]);
              setSituationUserId(created.id);
              setMessage(`Created user ${created.id}`);
            })
          }
        >
          Create User
        </button>
      </section>

      <section className="card">
        <h2>Create Situation</h2>
        <label>
          User ID
          <input
            value={situationUserId}
            onChange={(e) => setSituationUserId(e.target.value)}
            placeholder="UUID"
          />
        </label>
        <label>
          Title
          <input value={situationTitle} onChange={(e) => setSituationTitle(e.target.value)} />
        </label>
        <label>
          Query
          <input
            value={situationQuery}
            onChange={(e) => setSituationQuery(e.target.value)}
            placeholder="Search terms or prompt"
          />
        </label>
        <label>
          Description
          <textarea
            value={situationDescription}
            onChange={(e) => setSituationDescription(e.target.value)}
          />
        </label>
        <button
          disabled={loading}
          onClick={() =>
            run(async () => {
              const created = await httpJson(`${baseUrl}/situations`, {
                method: "POST",
                body: JSON.stringify({
                  user_id: situationUserId,
                  title: situationTitle,
                  query: situationQuery,
                  description: situationDescription || null,
                  is_active: true
                })
              });
              setSituations((prev) => [created, ...prev]);
              setDashboardSituationId(created.id);
              setArticleSituationIds(created.id);
              setMessage(`Created situation ${created.id}`);
            })
          }
        >
          Create Situation
        </button>
      </section>

      <section className="card">
        <h2>Ingest Article</h2>
        <label>
          URL
          <input
            value={articleUrl}
            onChange={(e) => setArticleUrl(e.target.value)}
            placeholder="https://example.com/news/story"
          />
        </label>
        <label>
          Title
          <input value={articleTitle} onChange={(e) => setArticleTitle(e.target.value)} />
        </label>
        <label>
          Source Name
          <input value={articleSource} onChange={(e) => setArticleSource(e.target.value)} />
        </label>
        <label>
          Situation IDs (comma-separated)
          <input
            value={articleSituationIds}
            onChange={(e) => setArticleSituationIds(e.target.value)}
            placeholder="uuid1,uuid2"
          />
        </label>
        <button
          disabled={loading}
          onClick={() =>
            run(async () => {
              await httpJson(`${baseUrl}/articles/ingest`, {
                method: "POST",
                body: JSON.stringify({
                  url: articleUrl,
                  title: articleTitle,
                  source_name: articleSource,
                  source_type: "news_site",
                  situation_ids: parseUuidCsv(articleSituationIds),
                  metadata: {}
                })
              });
              setMessage("Article ingested");
            })
          }
        >
          Ingest Article
        </button>
      </section>

      <section className="card">
        <h2>Dashboard</h2>
        <label>
          Situation ID
          <input
            value={dashboardSituationId}
            onChange={(e) => setDashboardSituationId(e.target.value)}
            placeholder="UUID"
          />
        </label>
        <button
          disabled={loading}
          onClick={() =>
            run(async () => {
              const data = await httpJson(
                `${baseUrl}/situations/${dashboardSituationId}/dashboard?persist_snapshot=true`
              );
              setDashboard(data);
            })
          }
        >
          Load Dashboard
        </button>
        {dashboard ? (
          <div className="result">
            <p>Situation: {dashboard.situation_id}</p>
            <p>Generated: {dashboard.generated_at}</p>
            <p>Articles: {dashboard.article_count}</p>
            <p>Sources: {dashboard.source_count}</p>
            <p>Headlines: {headline(dashboard.top_headlines)}</p>
          </div>
        ) : null}
      </section>

      <section className="card">
        <h2>Current Data</h2>
        <p className="muted">Users</p>
        <pre>{JSON.stringify(users, null, 2)}</pre>
        <p className="muted">Situations</p>
        <pre>{JSON.stringify(situations, null, 2)}</pre>
      </section>
    </main>
  );
}
