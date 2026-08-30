import { Navigate, Outlet } from "react-router-dom";
import { useCurrentUser } from "./useCurrentUser";

/** Sibling to ProtectedRoute -- that one only checks "is there a
 * token," this one additionally checks "is this user an admin," via
 * app/schemas/auth.py's UserOut.is_admin (app/routers/admin.py enforces
 * the same check server-side regardless, this is just so a non-admin
 * never sees the admin UI in the first place). Always rendered nested
 * INSIDE a ProtectedRoute-wrapped <Layout />, so isAuthenticated is
 * already guaranteed true here -- this only adds the extra check. */
export function AdminRoute() {
  const { data, isLoading } = useCurrentUser();

  if (isLoading) return null;
  if (!data?.is_admin) return <Navigate to="/overview" replace />;
  return <Outlet />;
}
