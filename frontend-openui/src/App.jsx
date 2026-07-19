import React, { useState, useEffect, useCallback } from "react";

// In production this is injected at build time or served same-origin behind
// the reverse proxy (see deploy/docker-compose.yml, Traefik routing rules).
// Falling back to localhost:8000 makes `npm run dev` work standalone against
// a locally running backend without any extra configuration.
const API_BASE_URL = import.meta.env.VITE_GENBI_API_BASE_URL || "http://localhost:8000";

function useAuthToken() {
  const [token, setToken] = useState(() => sessionStorage.getItem("genbi_token") || "");

  const saveToken = useCallback((newToken) => {
    sessionStorage.setItem("genbi_token", newToken);
    setToken(newToken);
  }, []);

  const clearToken = useCallback(() => {
    sessionStorage.removeItem("genbi_token");
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
    <div className="genbi-centered-panel">
      <h1>GenBI</h1>
      <p className="genbi-subtitle">Ask questions about your business data.</p>
      <form onSubmit={handleSubmit} className="genbi-form">
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
        {errorMessage && <p className="genbi-error">{errorMessage}</p>}
        <button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function ConnectionSelector({ token, selectedConnectionId, onSelect }) {
  const [connections, setConnections] = useState([]);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    let isCancelled = false;
    (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/admin/connections`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) throw new Error("Failed to load database connections.");
        const data = await response.json();
        if (!isCancelled) {
          setConnections(data);
          if (data.length > 0 && !selectedConnectionId) {
            onSelect(data[0].id);
          }
        }
      } catch (error) {
        if (!isCancelled) setErrorMessage(error.message);
      }
    })();
    return () => {
      isCancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (errorMessage) return <p className="genbi-error">{errorMessage}</p>;

  return (
    <select value={selectedConnectionId} onChange={(e) => onSelect(e.target.value)} className="genbi-select">
      {connections.length === 0 && <option value="">No connections configured yet</option>}
      {connections.map((connection) => (
        <option key={connection.id} value={connection.id}>
          {connection.display_name} ({connection.dialect})
        </option>
      ))}
    </select>
  );
}

function ResultsTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p className="genbi-muted">Query ran successfully but returned no rows.</p>;
  }
  const columnNames = Object.keys(rows[0]);
  return (
    <div className="genbi-table-wrapper">
      <table>
        <thead>
          <tr>
            {columnNames.map((columnName) => (
              <th key={columnName}>{columnName}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columnNames.map((columnName) => (
                <td key={columnName}>{String(row[columnName])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChatPanel({ token, onLogout }) {
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [isAsking, setIsAsking] = useState(false);

  const handleAsk = async (event) => {
    event.preventDefault();
    if (!question.trim() || !selectedConnectionId) return;

    const userMessage = { role: "user", text: question };
    setMessages((prev) => [...prev, userMessage]);
    setQuestion("");
    setIsAsking(true);

    try {
      const response = await fetch(`${API_BASE_URL}/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          target_connection_id: selectedConnectionId,
          natural_language_question: userMessage.text,
        }),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        throw new Error(errorBody.detail || "The query could not be completed.");
      }

      const data = await response.json();
      setMessages((prev) => [
        ...prev,
        { role: "assistant", sql: data.final_sql, rows: data.row_results, attempts: data.attempts },
      ]);
    } catch (error) {
      setMessages((prev) => [...prev, { role: "assistant", error: error.message }]);
    } finally {
      setIsAsking(false);
    }
  };

  return (
    <div className="genbi-chat-layout">
      <header className="genbi-header">
        <h1>GenBI</h1>
        <div className="genbi-header-controls">
          <ConnectionSelector
            token={token}
            selectedConnectionId={selectedConnectionId}
            onSelect={setSelectedConnectionId}
          />
          <button onClick={onLogout} className="genbi-secondary-button">
            Log out
          </button>
        </div>
      </header>

      <main className="genbi-messages">
        {messages.length === 0 && (
          <p className="genbi-muted">Ask a question about your data, e.g. "What were our top 5 customers by revenue last quarter?"</p>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`genbi-message genbi-message-${message.role}`}>
            {message.role === "user" && <p>{message.text}</p>}
            {message.role === "assistant" && message.error && <p className="genbi-error">{message.error}</p>}
            {message.role === "assistant" && !message.error && (
              <>
                <details className="genbi-sql-details">
                  <summary>Generated SQL ({message.attempts.length} attempt{message.attempts.length > 1 ? "s" : ""})</summary>
                  <pre>{message.sql}</pre>
                </details>
                <ResultsTable rows={message.rows} />
              </>
            )}
          </div>
        ))}
      </main>

      <form onSubmit={handleAsk} className="genbi-input-bar">
        <input
          type="text"
          placeholder="Ask a question about your data..."
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={isAsking}
        />
        <button type="submit" disabled={isAsking || !selectedConnectionId}>
          {isAsking ? "Thinking..." : "Ask"}
        </button>
      </form>
    </div>
  );
}

export default function App() {
  const { token, saveToken, clearToken } = useAuthToken();

  if (!token) {
    return <LoginForm onLoggedIn={saveToken} />;
  }
  return <ChatPanel token={token} onLogout={clearToken} />;
}
