import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiClient } from "../../api/client";
import type { AdminEventOut } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";

/** Admin-only equivalent of admin_dashboard/'s "Safety Checks" tab --
 * every fail-safe catch in the live path (bridge errors, DB errors,
 * order rejections) journals a safety_check_failed event; this is the
 * queryable version of "how many times has X failed this week," not
 * just a log line. */
export function AdminSafetyChecks() {
  const [hoursBack, setHoursBack] = useState(24 * 7);

  const failuresQuery = useQuery({
    queryKey: ["admin-safety-checks", hoursBack],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: "1000",
        since: new Date(Date.now() - hoursBack * 3600_000).toISOString(),
      });
      return apiClient.get<AdminEventOut[]>(`/admin/safety-checks?${params.toString()}`);
    },
  });

  const failures = failuresQuery.data ?? [];

  const countsByCheck = useMemo(() => {
    const counts = new Map<string, number>();
    for (const f of failures) {
      const checkName = (f.details.check_name as string | undefined) ?? "unknown";
      counts.set(checkName, (counts.get(checkName) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([check_name, count]) => ({ check_name, count }))
      .sort((a, b) => b.count - a.count);
  }, [failures]);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Safety check failures</h1>
      </div>

      <label className="flex flex-col gap-1 mb-5 w-fit">
        <span className="text-[13px] text-text-muted">Hours back</span>
        <input
          type="number"
          min={1}
          max={24 * 30}
          value={hoursBack}
          onChange={(e) => setHoursBack(Number(e.target.value))}
          className="w-24"
        />
      </label>

      {failuresQuery.isLoading && <p>Loading...</p>}
      {!failuresQuery.isLoading && failures.length === 0 && (
        <EmptyState title="No failures" message="No safety_check_failed events in this window." />
      )}

      {failures.length > 0 && (
        <>
          <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">
            Failure counts by check (repeated failures are the ones worth investigating first)
          </h2>
          <div style={{ width: "100%", height: 200 }} className="mb-8">
            <ResponsiveContainer>
              <BarChart data={countsByCheck} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--color-line)" strokeDasharray="3 3" />
                <XAxis dataKey="check_name" stroke="var(--color-text-muted)" fontSize={12} />
                <YAxis stroke="var(--color-text-muted)" fontSize={12} width={30} allowDecimals={false} />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-bg-elevated)",
                    border: "1px solid var(--color-line)",
                    borderRadius: 8,
                    color: "var(--color-text)",
                    fontSize: 13,
                  }}
                />
                <Bar dataKey="count" fill="var(--color-negative)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <Table>
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>User</th>
                <th>Model</th>
                <th>Check</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((f) => (
                <tr key={f.event_id}>
                  <td>{new Date(f.timestamp).toLocaleString()}</td>
                  <td>{f.user_email}</td>
                  <td>{f.model}</td>
                  <td>{(f.details.check_name as string | undefined) ?? "-"}</td>
                  <td>{(f.details.error as string | undefined) ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </div>
  );
}
