import { describe, expect, it } from "vitest";
import type { AdminModelConfigOut, AdminTradeOut } from "../api/types";
import { buildTradeStories } from "./tradeStories";

function trade(overrides: Partial<AdminTradeOut>): AdminTradeOut {
  return {
    trade_id: "t1",
    user_email: null,
    model: "fvg",
    is_shadow: true,
    direction: "long",
    entry_price: 1.105,
    stop_price: 1.103,
    target_price: 1.109,
    exit_price: null,
    outcome: null,
    realized_r: null,
    risk_pct_used: 0.01,
    equity_before: 10000,
    equity_after: null,
    setup_context: {},
    entry_time_utc: "2026-08-27T12:00:00Z",
    entry_time_ny: "2026-08-27T08:00:00-04:00",
    exit_time_utc: null,
    real_status: null,
    real_position_ticket: null,
    real_fill_price: null,
    real_fill_time_ny: null,
    real_close_price: null,
    real_close_reason: null,
    real_profit: null,
    real_close_time_ny: null,
    real_volume: null,
    ...overrides,
  };
}

function config(overrides: Partial<AdminModelConfigOut>): AdminModelConfigOut {
  return {
    config_id: "c1",
    model_name: "fvg",
    status: "active",
    risk_pct: 0.01,
    magic_number: 900001,
    max_concurrent_positions: null,
    is_paused: false,
    user_email: "a@example.com",
    ...overrides,
  };
}

describe("buildTradeStories", () => {
  it("groups the shared shadow row with a subscriber's own real row on the same (model, day)", () => {
    const shared = trade({ user_email: null, is_shadow: true });
    const real = trade({ trade_id: "t2", user_email: "a@example.com", is_shadow: false, real_status: "closed" });

    const stories = buildTradeStories([shared, real], []);

    expect(stories).toHaveLength(1);
    expect(stories[0].model).toBe("fvg");
    expect(stories[0].outcomes).toHaveLength(2);
  });

  it("splits into separate stories for different days even with the same model", () => {
    const day1 = trade({ trade_id: "t1", entry_time_ny: "2026-08-27T08:00:00-04:00" });
    const day2 = trade({ trade_id: "t2", entry_time_ny: "2026-08-28T08:00:00-04:00" });

    const stories = buildTradeStories([day1, day2], []);

    expect(stories).toHaveLength(2);
  });

  it("splits into separate stories for different models on the same day", () => {
    const fvg = trade({ trade_id: "t1", model: "fvg" });
    const ob = trade({ trade_id: "t2", model: "ob" });

    const stories = buildTradeStories([fvg, ob], []);

    expect(stories).toHaveLength(2);
    expect(stories.map((s) => s.model).sort()).toEqual(["fvg", "ob"]);
  });

  it("flags a currently-active subscriber with no trade row that day as missing", () => {
    const real = trade({ user_email: "a@example.com" });
    const configs = [
      config({ user_email: "a@example.com" }),
      config({ user_email: "b@example.com" }), // subscribed, but never got a row this day
    ];

    const stories = buildTradeStories([real], configs);

    expect(stories[0].missingSubscribers).toEqual(["b@example.com"]);
  });

  it("does not flag a subscriber who actually has a row that day", () => {
    const real = trade({ user_email: "a@example.com" });
    const configs = [config({ user_email: "a@example.com" })];

    const stories = buildTradeStories([real], configs);

    expect(stories[0].missingSubscribers).toEqual([]);
  });

  it("ignores a disabled/shadow-status config entirely -- only 'active' subscribers count as expected", () => {
    const real = trade({ user_email: "a@example.com" });
    const configs = [
      config({ user_email: "a@example.com" }),
      config({ user_email: "b@example.com", status: "disabled" }),
      config({ user_email: "c@example.com", status: "shadow" }),
    ];

    const stories = buildTradeStories([real], configs);

    expect(stories[0].missingSubscribers).toEqual([]);
  });

  it("never flags anyone missing for a model with no active configs known at all", () => {
    const real = trade({ user_email: "a@example.com", model: "ob" });

    const stories = buildTradeStories([real], [config({ model_name: "fvg", user_email: "z@example.com" })]);

    expect(stories[0].missingSubscribers).toEqual([]);
  });

  it("sorts stories newest first", () => {
    const older = trade({ trade_id: "t1", entry_time_ny: "2026-08-20T08:00:00-04:00" });
    const newer = trade({ trade_id: "t2", entry_time_ny: "2026-08-27T08:00:00-04:00" });

    const stories = buildTradeStories([older, newer], []);

    expect(stories[0].dateKey).toBe(new Date("2026-08-27T08:00:00-04:00").toDateString());
    expect(stories[1].dateKey).toBe(new Date("2026-08-20T08:00:00-04:00").toDateString());
  });
});
