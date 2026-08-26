import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { clearToken, getToken, login as apiLogin, setToken, setUnauthorizedHandler } from "../api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => getToken() !== null);
  const navigate = useNavigate();

  useEffect(() => {
    // Any 401 from anywhere in the app funnels through here -- see
    // api/client.ts's setUnauthorizedHandler. Keeps token-expiry handling
    // in one place instead of every call site checking for it.
    setUnauthorizedHandler(() => {
      setIsAuthenticated(false);
      navigate("/login");
    });
  }, [navigate]);

  async function login(email: string, password: string) {
    const token = await apiLogin(email, password);
    setToken(token);
    setIsAuthenticated(true);
  }

  function logout() {
    clearToken();
    setIsAuthenticated(false);
    navigate("/login");
  }

  return <AuthContext.Provider value={{ isAuthenticated, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
