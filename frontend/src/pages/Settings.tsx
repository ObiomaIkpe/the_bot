import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { apiClient, ApiError } from "../api/client";
import type { ModelConfigOut, ModelStatus, UserSettingsOut } from "../api/types";

const STATUSES: ModelStatus[] = ["disabled", "shadow", "active"];

const thStyle: React.CSSProperties = { textAlign: "left", padding: "6px 10px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "6px 10px", borderBottom: "1px solid #eee" };

export function Settings() {
  const queryClient = useQueryClient();

  const modelConfigsQuery = useQuery({
    queryKey: ["model-configs"],
    queryFn: () => apiClient.get<ModelConfigOut[]>("/model-configs"),
  });

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiClient.get<UserSettingsOut>("/settings"),
    retry: false, // a 404 (no settings row yet) is an expected, not transient, state
  });

  const patchModelConfig = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<Pick<ModelConfigOut, "status" | "is_paused">> }) =>
      apiClient.patch(`/model-configs/${id}`, changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["model-configs"] }),
  });

  const patchSettings = useMutation({
    mutationFn: (changes: Partial<Pick<UserSettingsOut, "is_paused" | "max_daily_loss_pct">>) =>
      apiClient.patch("/settings", changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  return (
    <div>
      <h1>Settings</h1>

      <h2>Models</h2>
      <p style={{ color: "#666", maxWidth: 600 }}>
        disabled = nothing runs · shadow = journals only, no real orders · active = places real
        orders. Pause stops just this model without affecting others; the account-wide pause below
        stops everything at once.
      </p>
      {modelConfigsQuery.isLoading && <p>Loading...</p>}
      {modelConfigsQuery.error && (
        <p style={{ color: "crimson" }}>Failed to load model configs: {String(modelConfigsQuery.error)}</p>
      )}
      {modelConfigsQuery.data && (
        <table style={{ borderCollapse: "collapse", width: "100%", maxWidth: 700, marginBottom: 32 }}>
          <thead>
            <tr>
              <th style={thStyle}>Model</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Paused</th>
              <th style={thStyle}>Risk %</th>
              <th style={thStyle}>Magic</th>
            </tr>
          </thead>
          <tbody>
            {modelConfigsQuery.data.length === 0 && (
              <tr>
                <td style={tdStyle} colSpan={5}>
                  No models configured yet.
                </td>
              </tr>
            )}
            {modelConfigsQuery.data.map((mc) => (
              <tr key={mc.config_id}>
                <td style={tdStyle}>{mc.model_name}</td>
                <td style={tdStyle}>
                  <select
                    value={mc.status}
                    disabled={patchModelConfig.isPending}
                    onChange={(e) =>
                      patchModelConfig.mutate({ id: mc.config_id, changes: { status: e.target.value as ModelStatus } })
                    }
                  >
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </td>
                <td style={tdStyle}>
                  <input
                    type="checkbox"
                    checked={mc.is_paused}
                    disabled={patchModelConfig.isPending}
                    onChange={(e) =>
                      patchModelConfig.mutate({ id: mc.config_id, changes: { is_paused: e.target.checked } })
                    }
                  />
                </td>
                <td style={tdStyle}>{(mc.risk_pct * 100).toFixed(1)}%</td>
                <td style={tdStyle}>{mc.magic_number}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Account-wide</h2>
      {settingsQuery.isLoading && <p>Loading...</p>}
      {settingsQuery.error instanceof ApiError && settingsQuery.error.status === 404 && (
        <p style={{ color: "#666" }}>No account settings configured yet.</p>
      )}
      {settingsQuery.data && (
        <div style={{ maxWidth: 400 }}>
          <p>
            <label>
              <input
                type="checkbox"
                checked={settingsQuery.data.is_paused}
                disabled={patchSettings.isPending}
                onChange={(e) => patchSettings.mutate({ is_paused: e.target.checked })}
              />{" "}
              <strong>Pause everything for this account</strong> (emergency stop -- affects every
              model at once)
            </label>
          </p>
          <p>
            Max daily loss %:{" "}
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
              style={{ width: 80 }}
            />
          </p>
        </div>
      )}
    </div>
  );
}
