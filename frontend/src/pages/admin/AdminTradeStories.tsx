import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "../../api/client";
import type { AdminModelConfigOut, AdminTradeOut } from "../../api/types";
import { Card } from "../../components/Card";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";
import { formatPrice } from "../../lib/format";
import { resolveExitPrice, resolveOutcome, resolveRealizedR, resolveRealStatusLabel } from "../../lib/pnl";
import { buildTradeStories } from "../../lib/tradeStories";

/** Multi-user fan-out's deliberately-deferred admin fast-follow
 * (MULTI_USER_FANOUT_PLAN.md section 4/7) -- reshapes AdminTrades.tsx's
 * flat "one row = one trade" list into "one shared narrative per model
 * per day, every subscriber's own outcome nested underneath." Purely
 * observational -- no new backend endpoint, reuses GET /admin/trades and
 * GET /admin/model-configs (both already used elsewhere in the admin
 * section). See lib/tradeStories.ts for the actual grouping logic and
 * its documented limitations (current-subscriber-roster only, not a
 * historical snapshot). */
export function AdminTradeStories() {
  const tradesQuery = useQuery({
    queryKey: ["admin-trades", "stories"],
    queryFn: () => apiClient.get<AdminTradeOut[]>("/admin/trades?days_back=3650&limit=1000"),
  });
  const configsQuery = useQuery({
    queryKey: ["admin-model-configs"],
    queryFn: () => apiClient.get<AdminModelConfigOut[]>("/admin/model-configs"),
  });

  const isLoading = tradesQuery.isLoading || configsQuery.isLoading;
  const stories = buildTradeStories(tradesQuery.data ?? [], configsQuery.data ?? []);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="m-0 text-2xl">Trade stories (all users)</h1>
          <p className="mt-1 mb-0 text-[13px] text-text-muted">
            One shared setup per model per day, with every subscriber's own outcome nested underneath -- who fired,
            who filled, who didn't.
          </p>
        </div>
      </div>

      {isLoading && <p>Loading...</p>}
      {!isLoading && stories.length === 0 && (
        <EmptyState title="No trade stories" message="No trades recorded yet." />
      )}

      {stories.map((story) => (
        <Card key={`${story.model}|${story.dateKey}`} className="mb-4">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="m-0 text-base font-semibold">
              {story.model} &mdash; {new Date(story.sampleEntryTimeNy).toLocaleDateString()}
            </h2>
            <span className="text-[13px] text-text-muted">
              {story.outcomes.length} outcome{story.outcomes.length === 1 ? "" : "s"}
              {story.missingSubscribers.length > 0 &&
                `, ${story.missingSubscribers.length} currently-subscribed user${
                  story.missingSubscribers.length === 1 ? "" : "s"
                } with no row this day`}
            </span>
          </div>

          <Table>
            <thead>
              <tr>
                <th>Subscriber</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Outcome</th>
                <th>Real status</th>
                <th>Real profit</th>
                <th>Realized R</th>
                <th>Lot size</th>
              </tr>
            </thead>
            <tbody>
              {story.outcomes.map((t) => (
                <tr key={t.trade_id}>
                  <td>
                    <Link to={`/admin/trades/${t.trade_id}`} className="text-accent no-underline hover:underline">
                      {t.user_email ?? "shared narrative (shadow)"}
                    </Link>
                  </td>
                  <td>{t.direction}</td>
                  <td className="font-mono">{formatPrice(t.entry_price)}</td>
                  <td className="font-mono">{formatPrice(resolveExitPrice(t))}</td>
                  <td>{resolveOutcome(t)}</td>
                  <td>{resolveRealStatusLabel(t)}</td>
                  <td className={`font-mono ${(t.real_profit ?? 0) >= 0 ? "text-positive" : "text-negative"}`}>
                    {t.real_profit ?? "-"}
                  </td>
                  <td className="font-mono">{resolveRealizedR(t)?.toFixed(2) ?? "-"}</td>
                  <td className="font-mono">{t.real_volume ?? "-"}</td>
                </tr>
              ))}
              {story.missingSubscribers.map((email) => (
                <tr key={email} className="opacity-60">
                  <td>{email}</td>
                  <td colSpan={8}>
                    currently subscribed to this model, no trade row this day (paused, no fill, or unsubscribed
                    since -- see this page's own note below)
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      ))}

      {stories.length > 0 && (
        <p className="text-[13px] text-text-muted mt-2">
          "No trade row this day" reflects who is subscribed to this model <em>right now</em>, not necessarily who
          was subscribed on that historical day -- subscription status isn't versioned, so this is a best-effort
          signal, not a historical fact.
        </p>
      )}
    </div>
  );
}
