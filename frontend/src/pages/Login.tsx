import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import { Button } from "../components/Button";
import { Card } from "../components/Card";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Card style={{ width: 320 }}>
        <h1 className="mt-0 text-xl">Trading Bot Admin</h1>
        <form onSubmit={handleSubmit}>
          <div className="flex flex-col gap-1 mb-3">
            <label htmlFor="email" className="text-[13px] text-text-muted">
              Email
            </label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="flex flex-col gap-1 mb-3">
            <label htmlFor="password" className="text-[13px] text-text-muted">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && <p className="text-negative">{error}</p>}
          <Button type="submit" variant="primary" disabled={submitting} className="w-full justify-center">
            {submitting ? "Logging in..." : "Log in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
