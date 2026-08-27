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
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Account settings</h1>
      </div>

      {settingsQuery.isLoading && <p>Loading...</p>}
      {settingsQuery.error instanceof ApiError && settingsQuery.error.status === 404 && (
        <p className="text-text-muted">No account settings configured yet.</p>
      )}
      {settingsQuery.data && (
        <div className="bg-bg-elevated border border-line rounded-lg p-5 max-w-[420px]">
          <div className="mb-4">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settingsQuery.data.is_paused}
                disabled={patchSettings.isPending}
                onChange={(e) => patchSettings.mutate({ is_paused: e.target.checked })}
              />
              <strong>Pause everything for this account</strong>
            </label>
            <p className="text-text-muted text-[13px] mt-1 ml-6">Emergency stop — affects every model at once.</p>
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-[13px] text-text-muted">Max daily loss %</span>
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
              className="w-24"
            />
          </label>
        </div>
      )}
    </div>
  );
}
