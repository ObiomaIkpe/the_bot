import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext";
import { clearToken, getToken } from "../api/client";

function TestConsumer() {
  const { isAuthenticated, login, logout } = useAuth();
  return (
    <div>
      <p>authenticated: {String(isAuthenticated)}</p>
      <button onClick={() => login("a@example.com", "password")}>Log in</button>
      <button onClick={logout}>Log out</button>
    </div>
  );
}

function renderWithProviders(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<TestConsumer />} />
          <Route path="/login" element={<p>login page</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("AuthProvider", () => {
  beforeEach(() => {
    clearToken();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ access_token: "new-token", token_type: "bearer" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    clearToken();
  });

  it("starts unauthenticated when there is no stored token", () => {
    renderWithProviders();
    expect(screen.getByText("authenticated: false")).toBeInTheDocument();
  });

  it("login() stores the token and flips isAuthenticated to true", async () => {
    const user = userEvent.setup();
    renderWithProviders();

    await user.click(screen.getByText("Log in"));

    await waitFor(() => expect(screen.getByText("authenticated: true")).toBeInTheDocument());
    expect(getToken()).toBe("new-token");
  });

  it("logout() clears the token, flips isAuthenticated to false, and navigates to /login", async () => {
    const user = userEvent.setup();
    renderWithProviders();
    await user.click(screen.getByText("Log in"));
    await waitFor(() => expect(screen.getByText("authenticated: true")).toBeInTheDocument());

    await user.click(screen.getByText("Log out"));

    await waitFor(() => expect(screen.getByText("login page")).toBeInTheDocument());
    expect(getToken()).toBeNull();
  });
});
