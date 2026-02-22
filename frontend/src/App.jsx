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

// ── SVG Icons ──────────────────────────────────────────────

function IconSituations() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}

function IconArticles() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
    </svg>
  );
}

function IconUsers() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a4 4 0 010 7.75" />
    </svg>
  );
}

function IconFeeds() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 11a9 9 0 019 9" />
      <path d="M4 4a16 16 0 0116 16" />
      <circle cx="5" cy="19" r="1" />
    </svg>
  );
}

function IconIngest() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function IconSystem() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </svg>
  );
}

function IconSearch() {
  return (
    <svg className="search-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function IconChevron() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

// ── Main App ───────────────────────────────────────────────

export default function App() {
  const [apiBase, setApiBase] = useState(defaultApiBase);
  const [user, setUser] = useState(getUser());
  const [authView, setAuthView] = useState("login");
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState("error"); // "error" | "success"
  const [loading, setLoading] = useState(false);

  // Navigation
  const [activeView, setActiveView] = useState("situations");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Auth form state
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regDisplayName, setRegDisplayName] = useState("");
  const [regPassword, setRegPassword] = useState("");

  // Situations
  const [situations, setSituations] = useState([]);
  const [expandedSituationId, setExpandedSituationId] = useState(null);
  const [dashboards, setDashboards] = useState({}); // { situationId: dashboardData }
  const [allArticles, setAllArticles] = useState({}); // { situationId: articleList }
  const [loadingArticles, setLoadingArticles] = useState({});

  // Suggestion search state
  const [suggestionSearch, setSuggestionSearch] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);

  // Trending topics for autocomplete
  const [trendingTopics, setTrendingTopics] = useState([]);

  // Manual situation form
  const [situationUserId, setSituationUserId] = useState("");
  const [situationTitle, setSituationTitle] = useState("");
  const [situationQuery, setSituationQuery] = useState("");
  const [situationDescription, setSituationDescription] = useState("");

  // Feed articles
  const [feedArticles, setFeedArticles] = useState([]);
  const [articlesLoaded, setArticlesLoaded] = useState(false);

  // Feed sources (all users)
  const [feedSources, setFeedSources] = useState([]);
  const [feedsLoaded, setFeedsLoaded] = useState(false);
  const [feedName, setFeedName] = useState("");
  const [feedUrl, setFeedUrl] = useState("");
  const [feedCategory, setFeedCategory] = useState("general");

  // Admin state
  const [users, setUsers] = useState([]);
  const [userEmail, setUserEmail] = useState("");
  const [userDisplayName, setUserDisplayName] = useState("");
  const [articleUrl, setArticleUrl] = useState("");
  const [articleTitle, setArticleTitle] = useState("");
  const [articleSource, setArticleSource] = useState("");
  const [articleSituationIds, setArticleSituationIds] = useState("");
  const [health, setHealth] = useState("");

  const baseUrl = useMemo(() => apiBase.replace(/\/$/, ""), [apiBase]);
  const isAdmin = user?.is_admin;

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

  // Refresh situations: call backend to find new articles, then reload situations & dashboards
  async function refreshSituations() {
    try {
      await httpJson(`${baseUrl}/situations/refresh`, { method: "POST" });
      // Always reload situations list so card data is current
      const data = await httpJson(`${baseUrl}/situations`);
      if (data) setSituations(data);
      // Clear cached dashboards so expanded cards re-fetch fresh data
      setDashboards((prev) => {
        const keys = Object.keys(prev);
        if (keys.length > 0) {
          // Re-fetch all previously loaded dashboards
          keys.forEach((id) => {
            httpJson(`${baseUrl}/situations/${id}/dashboard`)
              .then((d) => { if (d) setDashboards((p) => ({ ...p, [id]: d })); })
              .catch(() => {});
          });
        }
        return prev;
      });
    } catch {
      // silent — don't interrupt user
    }
  }

  // Auto-load situations and trending topics when logged in, then refresh
  useEffect(() => {
    if (user) {
      httpJson(`${baseUrl}/situations`)
        .then((data) => { if (data) setSituations(data); })
        .catch(() => {});
      httpJson(`${baseUrl}/trending-topics?limit=30`)
        .then((data) => { if (data) setTrendingTopics(data); })
        .catch(() => {});
      // Refresh situations on login to pick up new articles
      refreshSituations();
    }
  }, [user, baseUrl]);

  // Refresh situations every 60 seconds
  useEffect(() => {
    if (!user) return;
    const interval = setInterval(async () => {
      try {
        await httpJson(`${baseUrl}/situations/refresh`, { method: "POST" });
        const data = await httpJson(`${baseUrl}/situations`);
        if (data) setSituations(data);
        // Re-fetch any cached dashboards
        setDashboards((prev) => {
          Object.keys(prev).forEach((id) => {
            httpJson(`${baseUrl}/situations/${id}/dashboard`)
              .then((d) => { if (d) setDashboards((p) => ({ ...p, [id]: d })); })
              .catch(() => {});
          });
          return prev;
        });
      } catch {
        // silent
      }
    }, 60_000);
    return () => clearInterval(interval);
  }, [user, baseUrl]);

  // Auto-load feed articles when switching to articles view
  useEffect(() => {
    if (user && activeView === "articles" && !articlesLoaded) {
      httpJson(`${baseUrl}/feed-articles?limit=30`)
        .then((data) => { if (data) { setFeedArticles(data); setArticlesLoaded(true); } })
        .catch(() => {});
    }
  }, [user, activeView, articlesLoaded, baseUrl]);

  // Auto-load admin data
  useEffect(() => {
    if (user && isAdmin && activeView === "admin-users" && users.length === 0) {
      httpJson(`${baseUrl}/users`).then((data) => { if (data) setUsers(data); }).catch(() => {});
    }
  }, [user, isAdmin, activeView, baseUrl]);

  useEffect(() => {
    if (user && activeView === "feeds" && !feedsLoaded) {
      httpJson(`${baseUrl}/feed-sources`).then((data) => { if (data) { setFeedSources(data); setFeedsLoaded(true); } }).catch(() => {});
    }
  }, [user, activeView, feedsLoaded, baseUrl]);

  // Debounced suggestion search
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

  // ── Helpers ────────────────────────────────────────────────

  async function run(action) {
    setLoading(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      setMessage(error.message || "Unexpected error");
      setMessageType("error");
    } finally {
      setLoading(false);
    }
  }

  function showSuccess(msg) {
    setMessage(msg);
    setMessageType("success");
    setTimeout(() => setMessage(""), 4000);
  }

  function parseUuidCsv(text) {
    return text.split(",").map((s) => s.trim()).filter(Boolean);
  }

  function navigate(view) {
    setActiveView(view);
    setSidebarOpen(false);
    setMessage("");
  }

  async function loadDashboard(situationId) {
    if (dashboards[situationId]) return; // already loaded
    try {
      const data = await httpJson(`${baseUrl}/situations/${situationId}/dashboard`);
      if (data) setDashboards((prev) => ({ ...prev, [situationId]: data }));
    } catch {
      // silently fail
    }
  }

  async function loadAllArticles(situationId) {
    if (allArticles[situationId]) return;
    setLoadingArticles((prev) => ({ ...prev, [situationId]: true }));
    try {
      const data = await httpJson(`${baseUrl}/situations/${situationId}/articles?limit=500`);
      if (data) setAllArticles((prev) => ({ ...prev, [situationId]: data }));
    } catch {
      // silently fail
    } finally {
      setLoadingArticles((prev) => ({ ...prev, [situationId]: false }));
    }
  }

  function toggleSituation(id) {
    if (expandedSituationId === id) {
      setExpandedSituationId(null);
    } else {
      setExpandedSituationId(id);
      loadDashboard(id);
    }
  }

  // ── Auth handlers ──────────────────────────────────────────

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
    setDashboards({});
    setHealth("");
    setMessage("");
    setFeedArticles([]);
    setArticlesLoaded(false);
    setFeedSources([]);
    setFeedsLoaded(false);
  }

  function handleSelectSuggestion(suggestion) {
    const topicName = suggestion.topic || suggestionSearch.trim();
    setSuggestionSearch("");
    setSuggestions([]);
    setShowSuggestions(false);

    run(async () => {
      const created = await httpJson(`${baseUrl}/situations/from-suggestion`, {
        method: "POST",
        body: JSON.stringify({
          topic: topicName,
          query: suggestion.query,
          description: suggestion.description,
          articles: suggestion.articles,
        }),
      });
      setSituations((prev) => [created, ...prev]);
      setExpandedSituationId(created.id);
      loadDashboard(created.id);
      showSuccess(`Created "${created.title}" with ${suggestion.article_count} articles`);
    });
  }

  // ── Auth Screen ────────────────────────────────────────────

  if (!user) {
    return (
      <div className="auth-wrapper">
        <section className="auth-card">
          <div className="auth-logo">News Dashboard</div>
          {authView === "login" ? (
            <>
              <h1>Welcome back</h1>
              <p className="auth-subtitle">Sign in to your account</p>
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
              <button disabled={loading} onClick={handleLogin} style={{ width: "100%", marginTop: 4 }}>
                {loading ? "Signing in..." : "Sign In"}
              </button>
              <p className="muted" style={{ marginTop: 16, textAlign: "center" }}>
                No account?{" "}
                <a className="auth-toggle" onClick={() => { setAuthView("register"); setMessage(""); }}>
                  Register
                </a>
              </p>
            </>
          ) : (
            <>
              <h1>Create account</h1>
              <p className="auth-subtitle">The first user automatically becomes admin.</p>
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
              <button disabled={loading} onClick={handleRegister} style={{ width: "100%", marginTop: 4 }}>
                {loading ? "Creating account..." : "Register"}
              </button>
              <p className="muted" style={{ marginTop: 16, textAlign: "center" }}>
                Already have an account?{" "}
                <a className="auth-toggle" onClick={() => { setAuthView("login"); setMessage(""); }}>
                  Sign In
                </a>
              </p>
            </>
          )}
          {message && <p className="err" style={{ marginTop: 12, textAlign: "center" }}>{message}</p>}
        </section>
      </div>
    );
  }

  // ── Authenticated Layout ───────────────────────────────────

  return (
    <div className="app-layout">
      {/* Mobile header */}
      <div className="mobile-header">
        <h1>News Dashboard</h1>
        <button className="hamburger" onClick={() => setSidebarOpen(!sidebarOpen)}>
          {sidebarOpen ? "\u2715" : "\u2630"}
        </button>
      </div>

      {/* Sidebar overlay (mobile) */}
      {sidebarOpen && (
        <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <aside className={`sidebar${sidebarOpen ? " open" : ""}`}>
        <div className="sidebar-brand">
          <h1>News Dashboard</h1>
          <p>Situation Tracker</p>
        </div>

        <div className="sidebar-user">
          <div className="sidebar-user-name">{user.display_name}</div>
          <span className={`sidebar-user-role ${isAdmin ? "admin" : "user"}`}>
            {isAdmin ? "Admin" : "User"}
          </span>
        </div>

        <nav className="sidebar-nav">
          <button className={`nav-item${activeView === "situations" ? " active" : ""}`} onClick={() => navigate("situations")}>
            <IconSituations /> My Situations
          </button>
          <button className={`nav-item${activeView === "articles" ? " active" : ""}`} onClick={() => navigate("articles")}>
            <IconArticles /> Browse Articles
          </button>
          <button className={`nav-item${activeView === "feeds" ? " active" : ""}`} onClick={() => navigate("feeds")}>
            <IconFeeds /> RSS Feeds
          </button>

          {isAdmin && (
            <>
              <div className="nav-section-label">Admin</div>
              <button className={`nav-item${activeView === "admin-users" ? " active" : ""}`} onClick={() => navigate("admin-users")}>
                <IconUsers /> Users
              </button>
              <button className={`nav-item${activeView === "admin-ingest" ? " active" : ""}`} onClick={() => navigate("admin-ingest")}>
                <IconIngest /> Ingest Articles
              </button>
              <button className={`nav-item${activeView === "admin-system" ? " active" : ""}`} onClick={() => navigate("admin-system")}>
                <IconSystem /> System
              </button>
            </>
          )}
        </nav>

        <div className="sidebar-footer">
          <button onClick={handleLogout}>Sign Out</button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {message && (
          <div className={`message-bar ${messageType}`}>{message}</div>
        )}

        {/* ── My Situations View ──────────────────────────── */}
        {activeView === "situations" && (
          <>
            <div className="view-header">
              <h2>My Situations</h2>
              <p>Track news topics and view dashboards for each situation.</p>
            </div>

            {/* Search bar */}
            <div className="card" style={{ marginBottom: 16 }}>
              <div className="search-bar">
                <IconSearch />
                <input
                  className="search-input"
                  value={suggestionSearch}
                  onChange={(e) => setSuggestionSearch(e.target.value)}
                  placeholder="Search situations discovered by AI..."
                  autoComplete="off"
                />
                {suggestionSearch && (
                  <button
                    className="search-clear"
                    onClick={() => { setSuggestionSearch(""); setSuggestions([]); setShowSuggestions(false); }}
                    aria-label="Clear search"
                  >
                    &times;
                  </button>
                )}
              </div>

              {/* Show trending topics when search is empty */}
              {!suggestionSearch.trim() && trendingTopics.length > 0 && (
                <div className="topic-autocomplete">
                  <span className="topic-label">Trending:</span>
                  {trendingTopics.slice(0, 10).map((topic) => (
                    <button
                      key={topic}
                      className="topic-chip"
                      onClick={() => setSuggestionSearch(topic)}
                    >
                      {topic}
                    </button>
                  ))}
                </div>
              )}

              {/* Topic autocomplete from trending */}
              {suggestionSearch.trim() && !showSuggestions && !suggestionsLoading && (() => {
                const q = suggestionSearch.trim().toLowerCase();
                const matches = trendingTopics.filter((t) => t.toLowerCase().includes(q) && t.toLowerCase() !== q);
                return matches.length > 0 ? (
                  <div className="topic-autocomplete">
                    {matches.slice(0, 8).map((topic) => (
                      <button
                        key={topic}
                        className="topic-chip"
                        onClick={() => setSuggestionSearch(topic)}
                      >
                        {topic}
                      </button>
                    ))}
                  </div>
                ) : null;
              })()}

              {suggestionsLoading && (
                <div className="search-status">
                  <span className="spinner" />
                  Searching situations...
                </div>
              )}

              {showSuggestions && suggestions.length > 0 && (
                <div className="headline-results">
                  <p className="headline-results-label">
                    {suggestions.length} situation{suggestions.length !== 1 ? "s" : ""} found
                  </p>
                  <div className="headline-grid">
                    {suggestions.map((s, i) => (
                      <button
                        key={i}
                        className="headline-card"
                        onClick={() => handleSelectSuggestion(s)}
                      >
                        <span className="headline-card-title">{s.topic}</span>
                        <span className="headline-card-stats">
                          <span className="headline-stat">{s.article_count} article{s.article_count !== 1 ? "s" : ""}</span>
                          <span className="headline-stat">{s.sources.length} source{s.sources.length !== 1 ? "s" : ""}</span>
                        </span>
                        <span className="headline-card-meta">
                          {s.sources.map((src, j) => (
                            <span key={j} className="headline-source">{src}</span>
                          ))}
                        </span>
                        {s.sample_headlines.length > 1 && (
                          <ul className="headline-samples">
                            {s.sample_headlines.slice(0, 3).map((h, j) => (
                              <li key={j}>{h}</li>
                            ))}
                          </ul>
                        )}
                        <span className="headline-card-action">+ Track this situation</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {!showSuggestions && !suggestionsLoading && suggestionSearch.trim() && (
                <p className="muted" style={{ textAlign: "center", padding: "12px 0", margin: 0 }}>
                  No matching situations found. Try a different search term or use manual entry below.
                </p>
              )}

              <details className="manual-form" open={!showSuggestions || Boolean(situationTitle)}>
                <summary className="manual-form-toggle">Manual entry</summary>
                {isAdmin && (
                  <label>
                    User ID
                    <input
                      value={situationUserId}
                      onChange={(e) => setSituationUserId(e.target.value)}
                      placeholder="UUID (admin only)"
                    />
                  </label>
                )}
                <label>
                  Title
                  <input value={situationTitle} onChange={(e) => setSituationTitle(e.target.value)} />
                </label>
                <label>
                  Query
                  <input
                    value={situationQuery}
                    onChange={(e) => setSituationQuery(e.target.value)}
                    placeholder="Search terms"
                  />
                </label>
                <label>
                  Description
                  <textarea
                    value={situationDescription}
                    onChange={(e) => setSituationDescription(e.target.value)}
                    rows={2}
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
                      setSituationTitle("");
                      setSituationQuery("");
                      setSituationDescription("");
                      showSuccess(`Created situation "${created.title}"`);
                    })
                  }
                >
                  Create Situation
                </button>
              </details>
            </div>

            {/* Situation cards */}
            {situations.length > 0 ? (
              <div className="situation-grid">
                {situations.map((s) => {
                  const isExpanded = expandedSituationId === s.id;
                  const dash = dashboards[s.id];
                  return (
                    <div
                      key={s.id}
                      className={`situation-card${isExpanded ? " expanded" : ""}`}
                      onClick={() => toggleSituation(s.id)}
                    >
                      <div className="situation-card-title">
                        <span>{s.title}</span>
                        <span className="expand-icon"><IconChevron /></span>
                      </div>
                      {s.description && (
                        <p className="situation-card-desc">{s.description}</p>
                      )}
                      <span className="situation-card-query">{s.query}</span>

                      {isExpanded && (
                        <div className="situation-dashboard" onClick={(e) => e.stopPropagation()}>
                          {dash ? (
                            <>
                              <div className="dashboard-stats">
                                <div className="stat-box">
                                  <span className="stat-number">{dash.article_count}</span>
                                  <span className="stat-label">Articles</span>
                                </div>
                                <div className="stat-box">
                                  <span className="stat-number">{dash.source_count}</span>
                                  <span className="stat-label">Sources</span>
                                </div>
                              </div>
                              {dash.top_headlines && dash.top_headlines.length > 0 ? (
                                <>
                                  <ul className="dashboard-headlines">
                                    {dash.top_headlines.map((h, i) => (
                                      <li key={i}>
                                        <a href={h.url} target="_blank" rel="noopener noreferrer">
                                          {h.title}
                                        </a>
                                      </li>
                                    ))}
                                  </ul>
                                  {dash.article_count > dash.top_headlines.length && (
                                    allArticles[s.id] ? (
                                      <div className="all-articles-section">
                                        <div className="all-articles-header">
                                          All Articles ({allArticles[s.id].length})
                                          <button
                                            className="btn-secondary btn-small"
                                            onClick={() => setAllArticles((prev) => { const { [s.id]: _, ...rest } = prev; return rest; })}
                                          >
                                            Collapse
                                          </button>
                                        </div>
                                        <ul className="dashboard-headlines all-articles-list">
                                          {allArticles[s.id].map((item, i) => (
                                            <li key={i}>
                                              <a href={item.article.url} target="_blank" rel="noopener noreferrer">
                                                {item.article.title}
                                              </a>
                                              {item.article.published_at && (
                                                <span className="article-date">
                                                  {new Date(item.article.published_at).toLocaleDateString()}
                                                </span>
                                              )}
                                            </li>
                                          ))}
                                        </ul>
                                      </div>
                                    ) : (
                                      <button
                                        className="btn-secondary btn-small view-all-btn"
                                        disabled={loadingArticles[s.id]}
                                        onClick={() => loadAllArticles(s.id)}
                                      >
                                        {loadingArticles[s.id] ? (
                                          <><span className="spinner" /> Loading...</>
                                        ) : (
                                          `View all ${dash.article_count} articles`
                                        )}
                                      </button>
                                    )
                                  )}
                                </>
                              ) : (
                                <p className="muted" style={{ textAlign: "center", padding: "8px 0", margin: 0 }}>
                                  No headlines yet
                                </p>
                              )}
                              <button
                                className="btn-danger btn-small"
                                style={{ marginTop: 12 }}
                                disabled={loading}
                                onClick={() =>
                                  run(async () => {
                                    await httpJson(`${baseUrl}/situations/${s.id}`, { method: "DELETE" });
                                    setSituations((prev) => prev.filter((sit) => sit.id !== s.id));
                                    setExpandedSituationId(null);
                                    const { [s.id]: _, ...rest } = dashboards;
                                    setDashboards(rest);
                                    showSuccess(`Deleted "${s.title}"`);
                                  })
                                }
                              >
                                Delete Situation
                              </button>
                            </>
                          ) : (
                            <div className="dashboard-loading">
                              <span className="spinner" /> Loading dashboard...
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="empty-state">
                <div className="empty-state-icon">
                  <IconSituations />
                </div>
                <p><strong>No situations yet</strong></p>
                <p>Search your feeds above to discover and track news situations.</p>
              </div>
            )}
          </>
        )}

        {/* ── Browse Articles View ────────────────────────── */}
        {activeView === "articles" && (
          <>
            <div className="view-header">
              <h2>Browse Articles</h2>
              <p>Latest articles from your RSS feeds.</p>
            </div>

            <div className="card">
              {feedArticles.length > 0 ? (
                <>
                  {feedArticles.map((a) => (
                    <div key={a.id} className="feed-article-row">
                      <a href={a.original_url} target="_blank" rel="noopener noreferrer">
                        {a.title}
                      </a>
                      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 2 }}>
                        {a.author && <span className="muted">{a.author}</span>}
                        {a.published_date && (
                          <span className="muted" style={{ fontSize: 12 }}>
                            {new Date(a.published_date).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                      {a.snippet && <p className="feed-article-snippet">{a.snippet}</p>}
                    </div>
                  ))}
                  <div style={{ textAlign: "center", paddingTop: 12 }}>
                    <button
                      className="btn-outline btn-small"
                      disabled={loading}
                      onClick={() =>
                        run(async () => {
                          const data = await httpJson(`${baseUrl}/feed-articles?limit=30&offset=${feedArticles.length}`);
                          if (data) setFeedArticles((prev) => [...prev, ...data]);
                        })
                      }
                    >
                      Load more
                    </button>
                  </div>
                </>
              ) : articlesLoaded ? (
                <div className="empty-state">
                  <p><strong>No articles yet</strong></p>
                  <p>Articles will appear here after your RSS feeds are fetched.</p>
                </div>
              ) : (
                <div className="dashboard-loading">
                  <span className="spinner" /> Loading articles...
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Admin: Users View ───────────────────────────── */}
        {activeView === "admin-users" && isAdmin && (
          <>
            <div className="view-header">
              <h2>Manage Users</h2>
              <p>Create and view user accounts.</p>
            </div>

            <div className="card" style={{ marginBottom: 16 }}>
              <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>Create User</h3>
              <label>
                Email
                <input
                  value={userEmail}
                  onChange={(e) => setUserEmail(e.target.value)}
                  placeholder="user@example.com"
                />
              </label>
              <label>
                Display Name
                <input
                  value={userDisplayName}
                  onChange={(e) => setUserDisplayName(e.target.value)}
                  placeholder="User Name"
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
                    setUserEmail("");
                    setUserDisplayName("");
                    showSuccess(`Created user: ${created.display_name}`);
                  })
                }
              >
                Create User
              </button>
            </div>

            <div className="card">
              <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>
                All Users ({users.length})
              </h3>
              {users.length > 0 ? (
                users.map((u) => (
                  <div key={u.id} className="feed-row">
                    <div>
                      <strong>{u.display_name}</strong>
                      {u.is_admin && <span className="badge" style={{ marginLeft: 8 }}>Admin</span>}
                      <br />
                      <span className="muted">{u.email}</span>
                    </div>
                    <span className="muted" style={{ fontSize: 12 }}>{u.id.slice(0, 8)}...</span>
                  </div>
                ))
              ) : (
                <div className="dashboard-loading">
                  <span className="spinner" /> Loading users...
                </div>
              )}
            </div>
          </>
        )}

        {/* ── RSS Feeds View (all users) ─────────────────── */}
        {activeView === "feeds" && (
          <>
            <div className="view-header">
              <h2>RSS Feeds</h2>
              <p>Add and manage RSS feed sources.</p>
            </div>

            <div className="card" style={{ marginBottom: 16 }}>
              <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>Add Feed</h3>
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
                    showSuccess(`Added feed: ${created.name}${created.last_fetched_at ? " (articles fetched)" : ""}`);
                    // Refresh trending topics since new articles are available
                    httpJson(`${baseUrl}/trending-topics?limit=30`)
                      .then((data) => { if (data) setTrendingTopics(data); })
                      .catch(() => {});
                  })
                }
              >
                Add Feed
              </button>
            </div>

            <div className="card">
              <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>
                Active Feeds ({feedSources.length})
              </h3>
              {feedSources.length > 0 ? (
                feedSources.map((fs) => (
                  <div key={fs.id} className="feed-row">
                    <div>
                      <strong>{fs.name}</strong>
                      <span className="badge navy" style={{ marginLeft: 8 }}>{fs.category}</span>
                      <br />
                      <span className="muted" style={{ fontSize: 12 }}>{fs.rss_url}</span>
                      {fs.last_fetched_at && (
                        <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>
                          Last fetched: {new Date(fs.last_fetched_at).toLocaleString()}
                        </span>
                      )}
                    </div>
                    <button
                      className="btn-danger btn-small"
                      disabled={loading}
                      onClick={() =>
                        run(async () => {
                          await httpJson(`${baseUrl}/feed-sources/${fs.id}`, { method: "DELETE" });
                          setFeedSources((prev) => prev.filter((f) => f.id !== fs.id));
                          showSuccess(`Removed feed: ${fs.name}`);
                        })
                      }
                    >
                      Remove
                    </button>
                  </div>
                ))
              ) : (
                <div className="empty-state">
                  <p><strong>No feeds added yet</strong></p>
                  <p>Add RSS feeds above to start collecting articles.</p>
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Admin: Ingest Articles View ─────────────────── */}
        {activeView === "admin-ingest" && isAdmin && (
          <>
            <div className="view-header">
              <h2>Ingest Article</h2>
              <p>Manually add an article and link it to situations.</p>
            </div>

            <div className="card">
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
                  placeholder="uuid1, uuid2"
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
                    setArticleUrl("");
                    setArticleTitle("");
                    setArticleSource("");
                    setArticleSituationIds("");
                    showSuccess("Article ingested successfully");
                  })
                }
              >
                Ingest Article
              </button>
            </div>
          </>
        )}

        {/* ── Admin: System View ──────────────────────────── */}
        {activeView === "admin-system" && isAdmin && (
          <>
            <div className="view-header">
              <h2>System</h2>
              <p>Health checks, API configuration, and raw data.</p>
            </div>

            <div className="card" style={{ marginBottom: 16 }}>
              <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>API Configuration</h3>
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
              </div>
              {health && <p className="ok" style={{ marginTop: 8 }}>Health: {health}</p>}
            </div>

            <div className="card" style={{ marginBottom: 16 }}>
              <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>Users Data</h3>
              <pre>{JSON.stringify(users, null, 2)}</pre>
            </div>

            <div className="card">
              <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>Situations Data</h3>
              <pre>{JSON.stringify(situations, null, 2)}</pre>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
