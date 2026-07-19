import React, { useState, useEffect, useCallback } from "react";
import "./styles.css";

const API_BASE_URL = import.meta.env.VITE_GENBI_API_BASE_URL || "http://localhost:8000";

function useAuthToken() {
  const [token, setToken] = useState(() => sessionStorage.getItem("genbi_admin_token") || "");

  const saveToken = useCallback((newToken) => {
    sessionStorage.setItem("genbi_admin_token", newToken);
    setToken(newToken);
  }, []);

  const clearToken = useCallback(() => {
    sessionStorage.removeItem("genbi_admin_token");
    setToken("");
  }, []);

  return { token, saveToken, clearToken };
}

function LoginForm({ onLoggedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setErrorMessage("");
    setIsSubmitting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        throw new Error(errorBody.detail || "Login failed.");
      }
      const data = await response.json();
      onLoggedIn(data.access_token);
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="genbi-admin-centered-panel">
      <h1>GenBI Admin Panel</h1>
      <p className="genbi-admin-subtitle">Tenant configuration &amp; connection management.</p>
      <form onSubmit={handleSubmit} className="genbi-admin-form">
        <label htmlFor="email">Email</label>
        <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {errorMessage && <p className="genbi-admin-error">{errorMessage}</p>}
        <button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function ConnectionsPanel({ token }) {
  const [connections, setConnections] = useState([]);
  const [statusMessage, setStatusMessage] = useState("");
  const [formValues, setFormValues] = useState({
    display_name: "",
    dialect: "postgresql",
    host: "",
    port: 5432,
    database_name: "",
    read_only_username: "",
    plaintext_password: "",
  });

  const loadConnections = useCallback(async () => {
    const response = await fetch(`${API_BASE_URL}/api/admin/connections`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (response.ok) setConnections(await response.json());
  }, [token]);

  useEffect(() => {
    loadConnections();
  }, [loadConnections]);

  const handleFieldChange = (field) => (event) => {
    setFormValues((prev) => ({ ...prev, [field]: event.target.value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setStatusMessage("Saving...");
    try {
      const response = await fetch(`${API_BASE_URL}/api/admin/connections`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ ...formValues, port: Number(formValues.port) }),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        throw new Error(errorBody.detail || "Failed to save connection.");
      }
      setStatusMessage("Connection saved.");
      setFormValues((prev) => ({ ...prev, display_name: "", host: "", database_name: "", read_only_username: "", plaintext_password: "" }));
      await loadConnections();
    } catch (error) {
      setStatusMessage(error.message);
    }
  };

  return (
    <section className="genbi-admin-card">
      <h2>Target Database Connections</h2>
      <p className="genbi-admin-muted">
        The credentials below should belong to a dedicated, read-only database role. See the README's security
        guidance before creating a connection against a production database.
      </p>

      <ul className="genbi-admin-list">
        {connections.map((connection) => (
          <li key={connection.id}>
            <strong>{connection.display_name}</strong> — {connection.dialect} @ {connection.host}:{connection.port}/
            {connection.database_name} {connection.is_active ? "" : "(inactive)"}
          </li>
        ))}
      </ul>

      <form onSubmit={handleSubmit} className="genbi-admin-form">
        <label>Display name</label>
        <input required value={formValues.display_name} onChange={handleFieldChange("display_name")} />

        <label>Dialect</label>
        <select value={formValues.dialect} onChange={handleFieldChange("dialect")}>
          <option value="postgresql">PostgreSQL</option>
          <option value="mssql">Microsoft SQL Server</option>
          <option value="mysql">MySQL</option>
          <option value="mariadb">MariaDB</option>
        </select>

        <label>Host</label>
        <input required value={formValues.host} onChange={handleFieldChange("host")} />

        <label>Port</label>
        <input required type="number" value={formValues.port} onChange={handleFieldChange("port")} />

        <label>Database name</label>
        <input required value={formValues.database_name} onChange={handleFieldChange("database_name")} />

        <label>Read-only username</label>
        <input required value={formValues.read_only_username} onChange={handleFieldChange("read_only_username")} />

        <label>Password</label>
        <input
          required
          type="password"
          value={formValues.plaintext_password}
          onChange={handleFieldChange("plaintext_password")}
        />

        <button type="submit">Save connection</button>
        {statusMessage && <p className="genbi-admin-muted">{statusMessage}</p>}
      </form>
    </section>
  );
}

function LlmConfigPanel({ token }) {
  const [configurations, setConfigurations] = useState([]);
  const [statusMessage, setStatusMessage] = useState("");
  const [formValues, setFormValues] = useState({
    provider: "openai",
    model_name: "",
    base_url: "",
    plaintext_api_key: "",
    activate_immediately: true,
  });

  const loadConfigurations = useCallback(async () => {
    const response = await fetch(`${API_BASE_URL}/api/admin/llm-configurations`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (response.ok) setConfigurations(await response.json());
  }, [token]);

  useEffect(() => {
    loadConfigurations();
  }, [loadConfigurations]);

  const handleFieldChange = (field) => (event) => {
    const value = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    setFormValues((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setStatusMessage("Saving and verifying connectivity...");
    try {
      const response = await fetch(`${API_BASE_URL}/api/admin/llm-configurations`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(formValues),
      });
      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        throw new Error(errorBody.detail || "Failed to save LLM configuration.");
      }
      setStatusMessage("LLM configuration saved and activated.");
      await loadConfigurations();
    } catch (error) {
      setStatusMessage(error.message);
    }
  };

  const isOllama = formValues.provider === "ollama";

  return (
    <section className="genbi-admin-card">
      <h2>LLM Provider Configuration</h2>
      <p className="genbi-admin-muted">
        Switching the active provider here takes effect immediately for every business user in this tenant — no
        restart required.
      </p>

      <ul className="genbi-admin-list">
        {configurations.map((config) => (
          <li key={config.id}>
            <strong>{config.provider}</strong> / {config.model_name}{" "}
            {config.is_currently_active ? <span className="genbi-admin-badge">ACTIVE</span> : null}
          </li>
        ))}
      </ul>

      <form onSubmit={handleSubmit} className="genbi-admin-form">
        <label>Provider</label>
        <select value={formValues.provider} onChange={handleFieldChange("provider")}>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
          <option value="gemini">Gemini</option>
          <option value="ollama">Ollama (local/offline)</option>
        </select>

        <label>Model name</label>
        <input
          required
          placeholder={isOllama ? "e.g. llama3.1" : "e.g. gpt-4o-mini"}
          value={formValues.model_name}
          onChange={handleFieldChange("model_name")}
        />

        {isOllama && (
          <>
            <label>Ollama base URL</label>
            <input
              required
              placeholder="http://ollama:11434"
              value={formValues.base_url}
              onChange={handleFieldChange("base_url")}
            />
          </>
        )}

        {!isOllama && (
          <>
            <label>API key</label>
            <input
              required
              type="password"
              value={formValues.plaintext_api_key}
              onChange={handleFieldChange("plaintext_api_key")}
            />
          </>
        )}

        <label className="genbi-admin-checkbox-label">
          <input
            type="checkbox"
            checked={formValues.activate_immediately}
            onChange={handleFieldChange("activate_immediately")}
          />
          Activate immediately (hot-swap)
        </label>

        <button type="submit">Save configuration</button>
        {statusMessage && <p className="genbi-admin-muted">{statusMessage}</p>}
      </form>
    </section>
  );
}

function AdminDashboard({ token, onLogout }) {
  return (
    <div className="genbi-admin-dashboard">
      <header className="genbi-admin-header">
        <h1>GenBI Admin Panel</h1>
        <button onClick={onLogout} className="genbi-admin-secondary-button">
          Log out
        </button>
      </header>
      <main className="genbi-admin-main">
        <ConnectionsPanel token={token} />
        <LlmConfigPanel token={token} />
      </main>
    </div>
  );
}

export default function App() {
  const { token, saveToken, clearToken } = useAuthToken();

  if (!token) {
    return <LoginForm onLoggedIn={saveToken} />;
  }
  return <AdminDashboard token={token} onLogout={clearToken} />;
}
