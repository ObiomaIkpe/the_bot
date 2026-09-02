import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiClient, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Button } from "../components/Button";
import { Card } from "../components/Card";

export function Register() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    // Checked client-side, not just left to the API's 422 -- FastAPI's
    // validation-error body is a list of {loc, msg, type} objects, not a
    // plain string, and this form (like Login's) only ever renders
    // `err.detail` as text. Catching the two common real-world mistakes
    // here keeps a first-time trader from ever seeing that raw shape.
    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords don't match");
      return;
    }

    setSubmitting(true);
    try {
      await apiClient.post("/auth/register", { email, password });
      // Registration itself doesn't return a token (see app/routers/auth.py)
      // -- log straight in with the same credentials so a new trader lands
      // in the app immediately, not back at the login form.
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Card style={{ width: 320 }}>
        <h1 className="mt-0 text-xl">Create account</h1>
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
              minLength={8}
              required
            />
          </div>
          <div className="flex flex-col gap-1 mb-3">
            <label htmlFor="confirm-password" className="text-[13px] text-text-muted">
              Confirm password
            </label>
            <input
              id="confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              minLength={8}
              required
            />
          </div>
          {error && <p className="text-negative">{error}</p>}
          <Button type="submit" variant="primary" disabled={submitting} className="w-full justify-center">
            {submitting ? "Creating account..." : "Create account"}
          </Button>
        </form>
        <p className="text-[13px] text-text-muted mt-3 mb-0">
          Already have an account? <Link to="/login">Log in</Link>
        </p>
      </Card>
    </div>
  );
}
