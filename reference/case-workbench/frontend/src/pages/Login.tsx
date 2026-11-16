import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, session } from "../api";

export default function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<{ token: string; display_name: string }>("POST", "/api/login", {
        username,
        password,
      });
      session.set(r.token, r.display_name);
      navigate("/cases");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit} id="loginForm">
        <h1>Case Workbench</h1>
        <p className="muted">Sign in to continue</p>
        <label htmlFor="username">User ID</label>
        <input
          id="username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && (
          <div id="loginError" className="message error" role="alert">
            {error}
          </div>
        )}
        <button id="btnLogin" type="submit" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
