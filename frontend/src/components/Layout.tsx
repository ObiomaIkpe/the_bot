import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const navLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  marginRight: 16,
  textDecoration: "none",
  color: isActive ? "#111" : "#666",
  fontWeight: isActive ? 600 : 400,
});

export function Layout() {
  const { logout } = useAuth();

  return (
    <div style={{ fontFamily: "sans-serif" }}>
      <nav
        style={{
          display: "flex",
          alignItems: "center",
          padding: "12px 24px",
          borderBottom: "1px solid #ddd",
        }}
      >
        <strong style={{ marginRight: 32 }}>Trading Bot Admin</strong>
        <NavLink to="/dashboard" style={navLinkStyle}>
          Dashboard
        </NavLink>
        <NavLink to="/settings" style={navLinkStyle}>
          Settings
        </NavLink>
        <NavLink to="/live" style={navLinkStyle}>
          Live
        </NavLink>
        <NavLink to="/broker-credentials" style={navLinkStyle}>
          Broker Credentials
        </NavLink>
        <button type="button" onClick={logout} style={{ marginLeft: "auto" }}>
          Log out
        </button>
      </nav>
      <main style={{ padding: 24 }}>
        <Outlet />
      </main>
    </div>
  );
}
