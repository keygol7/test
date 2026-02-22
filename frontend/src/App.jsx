import { useEffect, useMemo, useState } from "react";
import { authHeaders, clearAuth, getToken, getUser, saveAuth } from "./auth";

const defaultApiBase = import.meta.env.VITE_API_BASE_URL || "/api";

async function httpJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (response.status === 401) {
    clearAuth();
    window.location.reload();
    return;
  }

  if (!response.ok) {
    throw new Error(payload?.detail || `Request failed (${response.status})`);
  }

  return payload;
}

export default function App() {
  const [apiBase, setApiBase] = useState(defaultApiBase);
  const [user, setUser] = useState(getUser());
  const [authView, setAuthView] = useState("login");
  const [health, setHealth] = useState("");
  const [users, setUsers] = useState([]);
  const [situations, setSituations] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  // Auth form state
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regDisplayName, setRegDisplayName] = useState("");
  const [regPassword, setRegPassword] = useState("");

  // Existing form state
  const [userEmail, setUserEmail] = useState("");
  const [userDisplayName, setUserDisplayName] = useState("");

  const [situationUserId, setSituationUserId] = useState("");
  const [situationTitle, setSituationTitle] = useState("");
  const [situationQuery, setSituationQuery] = useState("");
  const [situationDescription, setSituationDescription] = useState("");

  // Suggestion search state
  const [suggestionSearch, setSuggestionSearch] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);

  const [articleUrl, setArticleUrl] = useState("");
  const [articleTitle, setArticleTitle] = useState("");
  const [articleSource, setArticleSource] = useState("");
  const [articleSituationIds, setArticleSituationIds] = useState("");

  const [dashboardSituationId, setDashboardSituationId] = useState("");

  // Feed source state
  const [feedSources, setFeedSources] = useState([]);
  const [feedArticles, setFeedArticles] = useState([]);
  const [feedName, setFeedName] = useState("");
  const [feedUrl, setFeedUrl] = useState("");
  const [feedCategory, setFeedCategory] = useState("general");

  const baseUrl = useMemo(() => apiBase.replace(/\/$/, ""), [apiBase]);

  // Validate stored token on mount
  useEffect(() => {
    const token = getToken();
    if (token) {
      fetch(`${baseUrl}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((res) => {
          if (!res.ok) throw new Error("Token invalid");
          return res.json();
        })
        .then((userData) => {
          saveAuth(token, userData);
          setUser(userData);
        })
        .catch(() => {
          clearAuth();
          setUser(null);
        });
    }
  }, [baseUrl]);

  // Debounced news suggestion search
  useEffect(() => {
    const trimmed = suggestionSearch.trim();
    if (!trimmed) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    const timer = setTimeout(async () => {
      setSuggestionsLoading(true);
      try {
        const encoded = encodeURIComponent(trimmed);
        const data = await httpJson(`${baseUrl}/news-suggestions?q=${encoded}`);
        if (data && data.length > 0) {
          setSuggestions(data);
          setShowSuggestions(true);
        } else {
          setSuggestions([]);
          setShowSuggestions(false);
        }
      } catch {
        setSuggestions([]);
        setShowSuggestions(false);
      } finally {
        setSuggestionsLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [suggestionSearch, baseUrl]);

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

  async function handleLogin() {
    await run(async () => {
      const data = await httpJson(`${baseUrl}/auth/login`, {
        method: "POST",
        body: JSON.stringify({ email: loginEmail, password: loginPassword }),
      });
      if (data) {
        saveAuth(data.access_token, data.user);
        setUser(data.user);
        setLoginEmail("");
        setLoginPassword("");
      }
    });
  }

  async function handleRegister() {
    await run(async () => {
      const data = await httpJson(`${baseUrl}/auth/register`, {
        method: "POST",
        body: JSON.stringify({
          email: regEmail,
          display_name: regDisplayName,
          password: regPassword,
        }),
      });
      if (data) {
        saveAuth(data.access_token, data.user);
        setUser(data.user);
        setRegEmail("");
        setRegDisplayName("");
        setRegPassword("");
      }
    });
  }

  function handleLogout() {
    clearAuth();
    setUser(null);
    setUsers([]);
    setSituations([]);
    setDashboard(null);
    setHealth("");
    setMessage("");
  }

  function handleSelectSuggestion(suggestion) {
    setSituationTitle(suggestion.title);
    setSituationQuery(suggestion.suggested_query);
    setSituationDescription(
      `Tracking: ${suggestion.title}. Source: ${suggestion.source}.${
        suggestion.published ? " Published: " + suggestion.published + "." : ""
      }`
    );
    setSuggestionSearch("");
    setSuggestions([]);
    setShowSuggestions(false);
  }

  // ── Not logged in: show login / register ──

  if (!user) {
    return (
      <main className="layout">
        <section className="card auth-card">
          {authView === "login" ? (
            <>
              <h1>Sign In</h1>
              <p className="muted">Log in to your News Situation Dashboard account.</p>
              <label>
                Email
                <input
                  type="email"
                  value={loginEmail}
                  onChange={(e) => setLoginEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                  placeholder="Your password"
                />
              </label>
              <button disabled={loading} onClick={handleLogin}>
                {loading ? "Signing in..." : "Sign In"}
              </button>
              <p className="muted">
                No account?{" "}
                <a className="auth-toggle" onClick={() => { setAuthView("register"); setMessage(""); }}>
                  Register
                </a>
              </p>
            </>
          ) : (
            <>
              <h1>Register</h1>
              <p className="muted">Create a new account. The first user automatically becomes admin.</p>
              <label>
                Email
                <input
                  type="email"
                  value={regEmail}
                  onChange={(e) => setRegEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </label>
              <label>
                Display Name
                <input
                  value={regDisplayName}
                  onChange={(e) => setRegDisplayName(e.target.value)}
                  placeholder="Your Name"
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={regPassword}
                  onChange={(e) => setRegPassword(e.target.value)}
                  placeholder="Min 8 characters"
                />
              </label>
              <button disabled={loading} onClick={handleRegister}>
                {loading ? "Creating account..." : "Register"}
              </button>
              <p className="muted">
                Already have an account?{" "}
                <a className="auth-toggle" onClick={() => { setAuthView("login"); setMessage(""); }}>
                  Sign In
                </a>
              </p>
            </>
          )}
          {message ? <p className="err">{message}</p> : null}

          <label className="muted" style={{ marginTop: 16, fontSize: 12 }}>
            API Base URL
            <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
          </label>
        </section>
      </main>
    );
  }

  // ── Logged in: show dashboard ──

  const isAdmin = user.is_admin;

  return (
    <main className="layout">
      <section className="card header-bar">
        <h1>News Situation Dashboard</h1>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <p className="muted">
            Logged in as <strong>{user.display_name}</strong>{" "}
            <span className={isAdmin ? "badge admin" : "badge"}>{isAdmin ? "Admin" : "User"}</span>
          </p>
          <button className="btn-outline" onClick={handleLogout}>Logout</button>
        </div>
        <label className="muted" style={{ fontSize: 12 }}>
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
          {isAdmin && (
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
          )}
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

      {/* Admin only: Create User */}
      {isAdmin && (
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
                  body: JSON.stringify({ email: userEmail, display_name: userDisplayName }),
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
      )}

      {/* All users: Create Situation */}
      <section className="card">
        <h2>Create Situation</h2>

        {/* Suggestion search */}
        <div className="suggestion-wrapper">
          <label>
            Search for a News Topic
            <input
              value={suggestionSearch}
              onChange={(e) => setSuggestionSearch(e.target.value)}
              placeholder="e.g. AI regulation, climate summit..."
              autoComplete="off"
            />
          </label>
          {suggestionsLoading && (
            <p className="muted" style={{ fontSize: 12, margin: "4px 0" }}>
              Searching...
            </p>
          )}
          {showSuggestions && suggestions.length > 0 && (
            <ul className="suggestion-dropdown">
              {suggestions.map((s, i) => (
                <li
                  key={i}
                  className="suggestion-item"
                  onClick={() => handleSelectSuggestion(s)}
                >
                  <span className="suggestion-title">{s.title}</span>
                  <span className="suggestion-meta">
                    {s.source}
                    {s.published ? ` · ${s.published}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {isAdmin ? (
          <label>
            User ID
            <input
              value={situationUserId}
              onChange={(e) => setSituationUserId(e.target.value)}
              placeholder="UUID"
            />
          </label>
        ) : null}
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
                  user_id: isAdmin && situationUserId ? situationUserId : user.id,
                  title: situationTitle,
                  query: situationQuery,
                  description: situationDescription || null,
                  is_active: true,
                }),
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

      {/* Admin only: Ingest Article */}
      {isAdmin && (
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
                    metadata: {},
                  }),
                });
                setMessage("Article ingested");
              })
            }
          >
            Ingest Article
          </button>
        </section>
      )}

      {/* Admin only: Manage RSS Feeds */}
      {isAdmin && (
        <section className="card">
          <h2>Manage RSS Feeds</h2>
          <label>
            Feed Name
            <input
              value={feedName}
              onChange={(e) => setFeedName(e.target.value)}
              placeholder="e.g. BBC News"
            />
          </label>
          <label>
            RSS URL
            <input
              value={feedUrl}
              onChange={(e) => setFeedUrl(e.target.value)}
              placeholder="https://feeds.bbci.co.uk/news/rss.xml"
            />
          </label>
          <label>
            Category
            <input
              value={feedCategory}
              onChange={(e) => setFeedCategory(e.target.value)}
              placeholder="general"
            />
          </label>
          <div className="row">
            <button
              disabled={loading}
              onClick={() =>
                run(async () => {
                  const created = await httpJson(`${baseUrl}/feed-sources`, {
                    method: "POST",
                    body: JSON.stringify({
                      name: feedName,
                      rss_url: feedUrl,
                      category: feedCategory || "general",
                    }),
                  });
                  setFeedSources((prev) => [created, ...prev]);
                  setFeedName("");
                  setFeedUrl("");
                  setFeedCategory("general");
                  setMessage(`Added feed: ${created.name}`);
                })
              }
            >
              Add Feed
            </button>
            <button
              disabled={loading}
              onClick={() =>
                run(async () => {
                  const data = await httpJson(`${baseUrl}/feed-sources`);
                  setFeedSources(data);
                })
              }
            >
              Load Feeds
            </button>
          </div>
          {feedSources.length > 0 && (
            <div className="result">
              {feedSources.map((fs) => (
                <div key={fs.id} className="feed-row">
                  <div>
                    <strong>{fs.name}</strong>
                    <span className="badge">{fs.category}</span>
                    <br />
                    <span className="muted" style={{ fontSize: 12 }}>{fs.rss_url}</span>
                    {fs.last_fetched_at && (
                      <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>
                        Last fetched: {new Date(fs.last_fetched_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                  <button
                    className="btn-outline btn-small"
                    disabled={loading}
                    onClick={() =>
                      run(async () => {
                        await httpJson(`${baseUrl}/feed-sources/${fs.id}`, {
                          method: "DELETE",
                        });
                        setFeedSources((prev) => prev.filter((f) => f.id !== fs.id));
                        setMessage(`Removed feed: ${fs.name}`);
                      })
                    }
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* All users: Feed Articles */}
      <section className="card">
        <h2>Latest Feed Articles</h2>
        <button
          disabled={loading}
          onClick={() =>
            run(async () => {
              const data = await httpJson(`${baseUrl}/feed-articles?limit=20`);
              setFeedArticles(data);
            })
          }
        >
          Load Articles
        </button>
        {feedArticles.length > 0 && (
          <div className="result">
            {feedArticles.map((a) => (
              <div key={a.id} className="feed-article-row">
                <a href={a.original_url} target="_blank" rel="noopener noreferrer">
                  <strong>{a.title}</strong>
                </a>
                {a.author && <span className="muted"> by {a.author}</span>}
                {a.published_date && (
                  <span className="muted" style={{ fontSize: 12, marginLeft: 8 }}>
                    {new Date(a.published_date).toLocaleDateString()}
                  </span>
                )}
                {a.snippet && <p className="muted" style={{ fontSize: 13, margin: "4px 0 0" }}>{a.snippet}</p>}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* All users: Dashboard */}
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

      {/* Current Data */}
      <section className="card">
        <h2>Current Data</h2>
        {isAdmin && (
          <>
            <p className="muted">Users</p>
            <pre>{JSON.stringify(users, null, 2)}</pre>
          </>
        )}
        <p className="muted">Situations</p>
        <pre>{JSON.stringify(situations, null, 2)}</pre>
      </section>
    </main>
  );
}
