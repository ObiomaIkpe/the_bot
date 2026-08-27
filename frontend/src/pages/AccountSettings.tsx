import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError } from "../api/client";
import type { UserSettingsOut } from "../api/types";

/** Split out of the old Settings.tsx: this page keeps only the
 * account-wide controls; per-model status/pause now lives on Models. */
export function AccountSettings() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiClient.get<UserSettingsOut>("/settings"),
    retry: false, // a 404 (no settings row yet) is an expected, not transient, state
  });

  const patchSettings = useMutation({
    mutationFn: (changes: Partial<Pick<UserSettingsOut, "is_paused" | "max_daily_loss_pct">>) =>
      apiClient.patch("/settings", changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  return (
    <div>
      <div className="page-header">
        <h1>Account settings</h1>
      </div>

      {settingsQuery.isLoading && <p>Loading...</p>}
      {settingsQuery.error instanceof ApiError && settingsQuery.error.status === 404 && (
        <p style={{ color: "var(--text-muted)" }}>No account settings configured yet.</p>
      )}
      {settingsQuery.data && (
        <div className="card" style={{ maxWidth: 420 }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={settingsQuery.data.is_paused}
                disabled={patchSettings.isPending}
                onChange={(e) => patchSettings.mutate({ is_paused: e.target.checked })}
              />
              <strong>Pause everything for this account</strong>
            </label>
            <p style={{ color: "var(--text-muted)", fontSize: 13, margin: "4px 0 0 24px" }}>
              Emergency stop — affects every model at once.
            </p>
          </div>
          <label className="field">
            <span>Max daily loss %</span>
            <input
              type="number"
              step="0.01"
              defaultValue={settingsQuery.data.max_daily_loss_pct}
              disabled={patchSettings.isPending}
              onBlur={(e) => {
                const value = Number(e.target.value);
                if (value !== settingsQuery.data!.max_daily_loss_pct) {
                  patchSettings.mutate({ max_daily_loss_pct: value });
                }
              }}
              style={{ width: 100 }}
            />
          </label>
        </div>
      )}
    </div>
  );
}
