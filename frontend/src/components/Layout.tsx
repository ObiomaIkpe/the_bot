import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Button } from "./Button";
import { ThemeToggle } from "./ThemeToggle";

const NAV_ITEMS = [
  { to: "/overview", label: "Overview" },
  { to: "/models", label: "Models" },
  { to: "/live", label: "Live" },
  { to: "/trades", label: "Trade History" },
  { to: "/broker-credentials", label: "Broker Connection" },
  { to: "/account-settings", label: "Account Settings" },
  { to: "/profile", label: "Profile" },
];

export function Layout() {
  const { logout } = useAuth();

  return (
    <div className="flex min-h-screen">
      <aside className="w-[220px] shrink-0 bg-bg-elevated border-r border-line flex flex-col py-5">
        <div className="px-5 pb-4 mb-3 border-b border-line font-bold text-[15px]">Trading Bot Admin</div>
        <nav className="flex flex-col gap-0.5 flex-1">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `px-5 py-2.5 text-sm no-underline border-l-[3px] ${
                  isActive
                    ? "text-text border-accent bg-bg-elevated-2 font-semibold"
                    : "text-text-muted border-transparent hover:text-text hover:bg-bg-elevated-2"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-5 pt-3 mt-3 border-t border-line flex flex-col gap-1">
          <ThemeToggle />
          <Button variant="ghost" onClick={logout} className="w-full justify-center">
            Log out
          </Button>
        </div>
      </aside>
      <main className="flex-1 p-8 max-w-[1200px]">
        <Outlet />
      </main>
    </div>
  );
}
