import type { AdminModelConfigOut, AdminTradeOut } from "../api/types";

/**
 * Multi-user fan-out's admin-view fast-follow (MULTI_USER_FANOUT_PLAN.md
 * section 4/7, built 2026-09-06 on request -- the engine itself has been
 * live since 2026-09-04, this was always the deliberately-deferred
 * observational half). Reshapes the flat AdminTradeOut list from "one
 * row = one trade" into "one shared narrative per model per day, with
 * every subscriber's own outcome nested underneath" -- the exact shape
 * the plan called for: "5 subscribed, 4 filled, 1 missed -- here's why."
 *
 * Deliberately client-side, no new backend endpoint -- same "dataset is
 * small enough" reasoning as pnl.ts's own module docstring. Reuses two
 * ALREADY-EXISTING endpoints (GET /admin/trades, GET /admin/model-configs),
 * both already fetched elsewhere in the admin section.
 *
 * Grouped by (model, calendar day of entry_time_ny) -- NOT by the exact
 * shared trade_candidate_ready event -- matching the plan's own stated
 * granularity ("one shared narrative header per model per day"), and
 * sidesteps needing to precisely correlate every possible per-subscriber
 * event type (order_placement_failed/pending_order_cancelled/etc, several
 * of which don't carry direction/entry in their own details) back to one
 * specific candidate on a sibling-race day. Uses the exact same
 * `new Date(...).toDateString()` day-bucketing convention pnl.ts's own
 * summarizeTrades() already uses, for consistency with how "today"/"this
 * week" are computed elsewhere in this app.
 */

export interface TradeStory {
  model: string;
  /** One representative entry_time_ny from this day's trades, for
   * display/sorting only -- not itself meaningful beyond "this is roughly
   * when this day's trading happened". */
  sampleEntryTimeNy: string;
  dateKey: string; // toDateString() of sampleEntryTimeNy -- the actual grouping key
  /** Every Trade row sharing this (model, day) -- includes the model's
   * own ownerless shared/shadow row (user_email === null) alongside every
   * subscriber's own real-outcome row. */
  outcomes: AdminTradeOut[];
  /** user_email for every user CURRENTLY subscribed (ModelConfig.status
   * === 'active') to this model who has no row in `outcomes` above.
   * Best-effort only -- reflects TODAY's subscription roster, not
   * necessarily who was actually subscribed on that historical day
   * (ModelConfig doesn't keep history) -- see this file's own comment
   * on buildTradeStories() for why that's an accepted limitation, not
   * a bug. */
  missingSubscribers: string[];
}

function dayKey(entryTimeNy: string): string {
  return new Date(entryTimeNy).toDateString();
}

/**
 * `modelConfigs` supplies the CURRENT subscriber roster only -- there is
 * no historical record of who was subscribed to a model on any given
 * past day (ModelConfig is a live row, not versioned). This means
 * `missingSubscribers` on an older story can be wrong in both directions
 * (someone who wasn't yet subscribed back then shows up as "missing";
 * someone who has since unsubscribed won't show up at all) -- deliberately
 * accepted rather than pretending a precision this data doesn't have.
 * Callers should present this as "currently subscribed but no row this
 * day", not as an unconditional historical fact.
 */
export function buildTradeStories(trades: AdminTradeOut[], modelConfigs: AdminModelConfigOut[]): TradeStory[] {
  const groups = new Map<string, TradeStory>();

  for (const trade of trades) {
    const key = `${trade.model}|${dayKey(trade.entry_time_ny)}`;
    let story = groups.get(key);
    if (!story) {
      story = {
        model: trade.model,
        sampleEntryTimeNy: trade.entry_time_ny,
        dateKey: dayKey(trade.entry_time_ny),
        outcomes: [],
        missingSubscribers: [],
      };
      groups.set(key, story);
    }
    story.outcomes.push(trade);
  }

  const activeSubscribersByModel = new Map<string, Set<string>>();
  for (const config of modelConfigs) {
    if (config.status !== "active") continue;
    const set = activeSubscribersByModel.get(config.model_name) ?? new Set<string>();
    set.add(config.user_email);
    activeSubscribersByModel.set(config.model_name, set);
  }

  for (const story of groups.values()) {
    const activeSubscribers = activeSubscribersByModel.get(story.model);
    if (!activeSubscribers) continue;
    const present = new Set(story.outcomes.map((t) => t.user_email).filter((e): e is string => e != null));
    story.missingSubscribers = [...activeSubscribers].filter((email) => !present.has(email));
  }

  return [...groups.values()].sort(
    (a, b) => new Date(b.sampleEntryTimeNy).getTime() - new Date(a.sampleEntryTimeNy).getTime(),
  );
}
