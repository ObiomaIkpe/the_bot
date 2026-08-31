import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { ModelOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";

/** Shared "which models exist" query -- replaces the old hardcoded
 * `const MODELS = ["fvg", "ob", "fvg_ob"]` that used to be duplicated
 * across AdminEventFeed.tsx/AdminTrades.tsx/TradeHistory.tsx. Same
 * pattern as useCurrentUser(): react-query dedupes the identical
 * queryKey across every caller, and AdminModels.tsx invalidates
 * ["models"] after adding one, so every open dropdown picks it up
 * immediately -- no redeploy needed. */
export function useModels() {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: ["models"],
    queryFn: () => apiClient.get<ModelOut[]>("/models"),
    enabled: isAuthenticated,
  });
}
