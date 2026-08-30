import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { CurrentUser } from "../api/types";
import { useAuth } from "./AuthContext";

/** Shared "who am I" query -- used by Profile.tsx, Layout.tsx (to show
 * the Admin nav section), and AdminRoute.tsx (to gate /admin/* routes).
 * Deliberately a plain hook, not folded into AuthContext's own shape --
 * AuthContext only owns "am I logged in at all"; react-query already
 * dedupes identical queryKeys across components, so all three callers
 * share one cached request instead of three. */
export function useCurrentUser() {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiClient.get<CurrentUser>("/auth/me"),
    enabled: isAuthenticated,
  });
}
