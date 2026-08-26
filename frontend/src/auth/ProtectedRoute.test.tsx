import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./ProtectedRoute";
import { AuthProvider } from "./AuthContext";
import { clearToken, setToken } from "../api/client";

function renderProtected(initialPath = "/dashboard") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<p>login page</p>} />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <p>secret dashboard content</p>
              </ProtectedRoute>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  afterEach(() => {
    clearToken();
  });

  it("redirects to /login when there is no token", () => {
    clearToken();
    renderProtected();
    expect(screen.getByText("login page")).toBeInTheDocument();
    expect(screen.queryByText("secret dashboard content")).not.toBeInTheDocument();
  });

  it("renders its children when a token is present", () => {
    setToken("a-real-token");
    renderProtected();
    expect(screen.getByText("secret dashboard content")).toBeInTheDocument();
  });
});
