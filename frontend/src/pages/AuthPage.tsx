import { FormEvent, useState } from "react";
import { api } from "../api";

export default function AuthPage({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(setup ? "/auth/setup" : "/auth/login", "POST", { username, password });
      onDone();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-line bg-panel p-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            D<span className="text-accent">Tube</span>
          </h1>
          <p className="mt-1 text-sm text-muted">
            {setup ? "Create your admin login to get started." : "Log in to continue."}
          </p>
        </div>
        <label className="block space-y-1 text-sm">
          <span className="text-muted">Username</span>
          <input className="field" value={username} onChange={(e) => setUsername(e.target.value)}
            autoComplete="username" required autoFocus />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-muted">Password{setup && " (at least 10 characters)"}</span>
          <input className="field" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete={setup ? "new-password" : "current-password"} minLength={setup ? 10 : 1} required />
        </label>
        {error && <p className="text-sm text-accent-strong">{error}</p>}
        <button className="btn btn-primary w-full" disabled={busy}>
          {setup ? "Create login" : "Log in"}
        </button>
      </form>
    </div>
  );
}
