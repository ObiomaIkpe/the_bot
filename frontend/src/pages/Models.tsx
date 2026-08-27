import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { ModelConfigOut, Position, TradeOut } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ModelCard } from "../components/ModelCard";

export function Models() {
  const modelConfigsQuery = useQuery({
    queryKey: ["model-configs"],
    queryFn: () => apiClient.get<ModelConfigOut[]>("/model-configs"),
  });

  const tradesQuery = useQuery({
    queryKey: ["trades-all"],
    queryFn: () => apiClient.get<TradeOut[]>("/trades?days_back=3650&limit=1000"),
  });

  const positionsQuery = useQuery({
    queryKey: ["positions"],
    queryFn: () => apiClient.get<Position[]>("/trading/positions"),
    retry: false,
  });

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Models</h1>
      </div>

      {modelConfigsQuery.isLoading && <p>Loading...</p>}
      {modelConfigsQuery.error && (
        <p className="text-negative">Failed to load models: {String(modelConfigsQuery.error)}</p>
      )}
      {modelConfigsQuery.data && modelConfigsQuery.data.length === 0 && (
        <EmptyState title="No models configured" message="Models are set up by the operator; check back once one is added." />
      )}
      {modelConfigsQuery.data && modelConfigsQuery.data.length > 0 && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
          {modelConfigsQuery.data.map((mc) => (
            <ModelCard
              key={mc.config_id}
              modelConfig={mc}
              trades={tradesQuery.data ?? []}
              openPositionsCount={positionsQuery.data?.filter((p) => p.magic === mc.magic_number).length ?? 0}
            />
          ))}
        </div>
      )}
    </div>
  );
}
