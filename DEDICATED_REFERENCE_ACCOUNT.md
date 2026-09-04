# Dedicated price-only reference account -- not built, deferred

Not committed -- working notes. Explains the "Option 2" idea from the
multi-user fan-out price-feed discussion (see
`MULTI_USER_FANOUT_PLAN.md`'s Context section) and what it would
actually take to build, whenever it's actually needed.

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

Not started. Purely documented here so it doesn't get lost -- revisit
when the reference-bridge SPOF or the "one account doing double duty"
tradeoff actually starts to matter in practice, not before.
