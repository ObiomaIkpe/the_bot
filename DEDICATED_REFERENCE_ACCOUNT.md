# Dedicated price-only reference account -- DONE, live 2026-09-04

Not committed -- working notes. Explains the "Option 2" idea from the
multi-user fan-out price-feed discussion (see
`MULTI_USER_FANOUT_PLAN.md`'s Context section) and what it actually
took to build. **Built and cut over live 2026-09-04** -- see "Status"
at the bottom for the full account of what actually happened.

## What this is

Detection (`DaySelectionGate`/`DayOrchestrator`) needs one real MT5
bridge to read prices from. Today, and still, that's **the same one
real account that also places real trades** -- one account doing double
duty. The "dedicated reference account" idea is a *separate* account
that exists purely to supply prices, never places a real order itself.

This was discussed during the original fan-out design and deliberately
**not built** -- not needed for anything else to work, so it was parked
as "build only if it's ever actually needed." What *did* get built,
specifically so this stays cheap to add later: the reference bridge is
read from one config value (`BRIDGE_URL`), not hardwired to "whichever
account also trades" -- see `shadow_runner/main.py`, which constructs
`bridge = BridgeClient(config.bridge_url)` once at startup, completely
independent of the per-subscriber bridges (`bridge_factory`) that fan
-out trading uses.

## Why it might eventually matter

Right now the one real trading account is also the single point of
failure for detection itself -- if its bridge goes down, nobody trades
that day, not just that one account (already flagged in
`MULTI_USER_FANOUT_PLAN.md`'s "Open questions, resolved" #3). A
dedicated reference account wouldn't remove that SPOF by itself, but it
would decouple "is the price feed healthy" from "is this specific
trading account's connection healthy" -- two different failure modes
that are currently tangled into one bridge.

## What it would actually take, step by step

1. **A broker account (external, on the user)** -- can be a plain demo
   account, since it never places a real order, never needs real money.
   Same broker/server as the current setup, so the price feed lines up
   with what detection already expects.
2. **Connect it through the existing self-service signup flow** --
   already built and working end-to-end (see
   `self_service_mt5_provisioning` memory): register a user, add broker
   credentials, the provisioning poller sets up its own MT5 terminal +
   bridge worker automatically, same as any other account.
3. **Keep its `ModelConfig` status off, deliberately, forever.**
   Provisioning it does NOT automatically make it reference-only -- it
   just creates an account that *could* start real-trading the moment
   its status flips to `active`. Nothing in the system enforces this
   account staying off; it has to be a remembered, deliberate constraint
   (worth a clear label somewhere once this exists, e.g. in its account
   nickname/notes, so it's never mistaken for a normal trading account).
4. **Point `BRIDGE_URL` at its bridge, restart the runner.** The actual
   cutover -- a one-line env var change, not a code change, because of
   how this was deliberately built.
5. **Verify before trusting it** -- compare detected setups against the
   old feed for a while before fully relying on the new one, to confirm
   the new account's price data is close enough to what detection
   expects.

Steps 2-5 are things I can walk through directly when this is actually
wanted. Step 1 needs the user to actually get the account first.

## Status

**Done, live 2026-09-04.** Account created (Exness demo, login
`476781537`, server `Exness-MT5Trial9`, registered under a dedicated
user `reference-feed@ihusale.com.ng`), provisioned end-to-end through
self-service (own MT5 terminal + bridge worker, port 8002 on the
Windows VPS), verified genuinely live (real balance/equity showing in
the app), and confirmed doubly safe: `orders_enabled: false` on its
bridge worker (the bridge itself refuses order placement, a 403 at the
HTTP layer) AND every `ModelConfig` for it left at the default
`disabled` -- two independent layers, not just one. `BRIDGE_URL` cut
over on the live server from the real account's own bridge (port 8001)
to this one (port 8002), confirmed via the running container's actual
environment and a clean same-day bar replay against the new feed.

Two real things found and fixed along the way, not part of the original
plan:
- **A raw, unfriendly error** (`GET /positions failed: 403 Client
  Error: Forbidden...`) surfaced on the Live page the moment this
  account's positions/pending-orders were queried -- caused by the
  bridge gating those GET endpoints behind the same `orders_enabled`
  switch as real order placement, not just a display bug. Fixed
  properly: `app/routers/trading.py`'s `list_positions`/
  `list_pending_orders` now map this to `409 Conflict` (matching the
  pattern `close_position` already used for the identical root cause),
  and `frontend/src/pages/Live.tsx` shows a specific, friendly message
  instead of the raw string. Fixes this for every account in this
  situation, not just this one -- any brand-new account hits the exact
  same raw error before someone flips `orders_enabled` on.
- **`BRIDGE_URL`/`SHADOW_RUNNER_USER_ID` were hardcoded directly in
  `docker-compose.yml`**, discovered while doing the cutover -- every
  future change would have needed a code commit + deploy instead of a
  server-side `.env` edit + restart, and it's exactly the kind of
  tracked-file drift this project already got bitten by once. Moved
  both into `.env`; required a real `api` rebuild (its `Settings` class
  rejects unknown env vars, same class of issue as the earlier
  `POSTGRES_PASSWORD` incident) -- deliberately scoped to `api` alone,
  confirmed not to touch `shadow_runner`'s own (still-undeployed
  fan-out) image.

Revisit only if the reference-bridge itself ever needs to move again
(e.g. a different account, or genuine redundancy) -- the one-line
`.env` change is now the whole story for that.
