import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Button } from "./Button";

const NAV_ITEMS = [
  { to: "/overview", label: "Overview" },
  { to: "/models", label: "Models" },
  { to: "/live", label: "Live" },
  { to: "/trades", label: "Trade History" },
  { to: "/broker-credentials", label: "Broker Connection" },
  { to: "/account-settings", label: "Account Settings" },
];

export function Layout() {
  const { logout } = useAuth();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">Trading Bot Admin</div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <Button variant="ghost" onClick={logout} style={{ width: "100%" }}>
            Log out
          </Button>
        </div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
