import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { AdminModelCreateOut, ModelOut } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";
import { useModels } from "../../lib/useModels";

/** The model registry itself (app/models/model.py) -- separate from
 * AdminModelConfigs.tsx, which shows per-user risk/status settings for
 * models that already exist. This page is where a new model actually
 * gets created: previously required a migration + a hand-edited
 * hardcoded tuple + 3 separate frontend files; now it's one form here,
 * and it's immediately available account-wide (see the backend's
 * provision_model_for_all_users()) -- no redeploy, no script run. */
export function AdminModels() {
  const modelsQuery = useModels();
  const queryClient = useQueryClient();

  const [modelName, setModelName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [lastResult, setLastResult] = useState<AdminModelCreateOut | null>(null);

  const createModel = useMutation({
    mutationFn: () =>
      apiClient.post<AdminModelCreateOut>("/admin/models", {
        model_name: modelName.trim(),
        display_name: displayName.trim(),
      }),
    onSuccess: (result) => {
      setLastResult(result);
      setModelName("");
      setDisplayName("");
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });

  const models = modelsQuery.data ?? [];

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Models</h1>
      </div>

      <Card className="mb-8 max-w-md">
        <h2 className="text-[13px] uppercase tracking-wide text-text-muted mt-0 mb-3">Add a model</h2>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            createModel.mutate();
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="text-[13px] text-text-muted">Internal name</span>
            <input
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              placeholder="e.g. drt"
              pattern="^[a-z][a-z0-9_]{0,31}$"
              title="lowercase letters, digits, underscores only, starting with a letter"
              required
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[13px] text-text-muted">Display name</span>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Displacement"
              required
            />
          </label>
          <Button variant="primary" type="submit" disabled={createModel.isPending}>
            {createModel.isPending ? "Adding..." : "Add model"}
          </Button>
          {createModel.error != null && <p className="text-negative text-[13px]">{String(createModel.error)}</p>}
          {lastResult && !createModel.error && (
            <p className="text-positive text-[13px]">
              Added "{lastResult.display_name}" -- made available to {lastResult.backfilled_users} existing
              account{lastResult.backfilled_users === 1 ? "" : "s"}.
            </p>
          )}
        </form>
      </Card>

      {modelsQuery.isLoading && <p>Loading...</p>}
      {!modelsQuery.isLoading && models.length === 0 && (
        <EmptyState title="No models" message="No models registered yet." />
      )}
      {models.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>Internal name</th>
              <th>Display name</th>
              <th>Added</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m: ModelOut) => (
              <tr key={m.model_name}>
                <td className="font-mono">{m.model_name}</td>
                <td>{m.display_name}</td>
                <td>{new Date(m.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
