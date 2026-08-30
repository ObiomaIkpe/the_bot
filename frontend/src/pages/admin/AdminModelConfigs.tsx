import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { AdminModelConfigOut } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";

/** Admin-only, all-users, read-only equivalent of admin_dashboard/'s
 * "Models" tab. No editing here -- same as the Streamlit original;
 * editing a model config stays a per-user action via /models/:modelName. */
export function AdminModelConfigs() {
  const configsQuery = useQuery({
    queryKey: ["admin-model-configs"],
    queryFn: () => apiClient.get<AdminModelConfigOut[]>("/admin/model-configs"),
  });

  const configs = configsQuery.data ?? [];

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Model configs (all users)</h1>
      </div>

      {configsQuery.isLoading && <p>Loading...</p>}
      {!configsQuery.isLoading && configs.length === 0 && (
        <EmptyState title="No model configs" message="No model_configs rows found." />
      )}
      {configs.length > 0 && (
        <>
          <Table>
            <thead>
              <tr>
                <th>User</th>
                <th>Model</th>
                <th>Status</th>
                <th>Risk %</th>
                <th>Magic number</th>
                <th>Max concurrent positions</th>
                <th>Paused</th>
              </tr>
            </thead>
            <tbody>
              {configs.map((c) => (
                <tr key={c.config_id}>
                  <td>{c.user_email}</td>
                  <td>{c.model_name}</td>
                  <td>{c.status}</td>
                  <td className="font-mono">{(c.risk_pct * 100).toFixed(1)}%</td>
                  <td className="font-mono">{c.magic_number}</td>
                  <td>{c.max_concurrent_positions ?? "-"}</td>
                  <td>{c.is_paused ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="text-text-muted text-[13px] mt-3">
            status: disabled = nothing runs · shadow = journals only, no real orders · active = the only state that
            places real orders.
          </p>
        </>
      )}
    </div>
  );
}
