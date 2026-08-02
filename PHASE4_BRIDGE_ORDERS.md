# Phase 4 Step 1 — Bridge Order Endpoints: Manual Verification Checklist

This can't be unit tested the way earlier phases were — there's no real
MT5 terminal available outside the Windows VPS, and this code places
real orders against a real (demo) broker account. Every check below
must be run manually, in order, against the real Exness demo account,
**before this bridge is ever wired into the live-trading runner (step
2)**.

## Before you start

Confirm `orders_enabled: false` is still the current state on the VPS —
this checklist deliberately turns it on for testing, then off again at
the end:

```powershell
type C:\bridge\config.json
```

## 1. Deploy the updated bridge code

Same flow as always — copy the four updated files
(`config.py`, `mt5_client.py`, `models.py`, `main.py`) into
`C:\bridge\app\`, then restart:

```powershell
cd C:\bridge
venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1
```

## 2. Confirm the safety gate works BEFORE enabling anything

With `orders_enabled` still `false`, confirm the new endpoints correctly
refuse to do anything:

```powershell
curl -X POST http://127.0.0.1:8001/orders -H "Content-Type: application/json" -d "{\"symbol\":\"EURUSDm\",\"direction\":\"buy\",\"volume\":0.01,\"stop_loss\":1.10000,\"take_profit\":1.11000}"
```

**Expected:** HTTP 403, with a message explaining `orders_enabled` is
false. If you get anything else (a real order attempt, a different
error), stop here — the safety gate itself is broken and nothing past
this point should be tested until that's fixed.

## 3. Enable order placement

Edit `config.json`, set `"orders_enabled": true`, save, restart the
bridge (same command as step 1).

## 4. Confirm `/positions` works and starts empty

```powershell
curl "http://127.0.0.1:8001/positions"
```

**Expected:** `{"positions": []}` (assuming no positions already open on
this account — if there are, note them now so step 6's check is
meaningful).

## 5. Place ONE tiny test order

Smallest possible size, wide stop/target so it's unlikely to close
immediately (this is about confirming the mechanics work, not testing a
real trade setup):

```powershell
curl -X POST http://127.0.0.1:8001/orders -H "Content-Type: application/json" -d "{\"symbol\":\"EURUSDm\",\"direction\":\"buy\",\"volume\":0.01,\"stop_loss\":1.00000,\"take_profit\":2.00000,\"comment\":\"phase4-test\"}"
```

**Check the response carefully:**
- `retcode` — should be `10009` (MT5's `TRADE_RETCODE_DONE`). Any other
  value means the order did NOT go through as expected — read
  `broker_comment` for why.
- `fill_time_is_estimate` — should be `false`. If `true`, the
  authoritative-fill-time lookup fell back to an estimate; note this and
  investigate before relying on fill timestamps for real slippage
  measurement.
- `requested_price` vs `fill_price` — these being different by a small
  amount is normal and expected (this IS the slippage Option B exists to
  measure). Note both values.
- `position_ticket` — you'll need this for step 7.

**If this fails with a filling-mode-related retcode:** this is the
specific, flagged-in-advance risk from `mt5_client.py`'s comments — MT5
filling-mode support (`ORDER_FILLING_IOC` was used) is broker/symbol
specific and wasn't verified against Exness before this was written. If
it fails here, try `mt5.ORDER_FILLING_FOK` or `mt5.ORDER_FILLING_RETURN`
in `_do_place_market_order()`'s request dict instead, redeploy, and
retry this step.

## 6. Confirm the position shows up correctly

```powershell
curl "http://127.0.0.1:8001/positions"
```

**Expected:** one position, matching the order just placed —
`symbol: EURUSDm`, `direction: buy`, `volume: 0.01`, and `magic`
matching whatever `magic_number` is set to in `config.json` (default
`900001`).

Also confirm this in the actual MT5 terminal window on the VPS (Trade
tab) — the position should be visible there too, tagged with the same
magic number if you check the order's details.

## 7. Close the test position

Using the `position_ticket` from step 5's response:

```powershell
curl -X POST http://127.0.0.1:8001/positions/<TICKET>/close
```

**Expected:** `retcode: 10009` again, a `close_price`, and — check the
MT5 terminal — the position should now be gone from the open positions
list and show up in trade history instead.

## 8. Confirm `/positions` is empty again

```powershell
curl "http://127.0.0.1:8001/positions"
```

**Expected:** `{"positions": []}` (or back to whatever pre-existing
positions were noted in step 4, if any).

## 9. Turn order placement back off

Edit `config.json` back to `"orders_enabled": false`, restart the
bridge. This checklist's job is done; the live-trading runner (step 2)
is what should turn it on for real, deliberately, not this manual test
session left in an enabled state by accident.

## What "passed" means

All 9 steps completed with the expected results, in order, with no
step skipped. If step 5 needed the filling-mode fallback mentioned
there, record which mode actually worked — that's a real finding that
belongs in `PHASE4_VALIDATION.md` once it exists, not just tribal
knowledge.

## What this checklist deliberately does NOT cover

- Placing an order and having it actually hit stop-loss or take-profit
  naturally (this checklist closes it manually instead, for speed and
  predictability). Confirming the account's floating P/L updates
  correctly as a position moves is worth a separate, patient check
  later, not blocking step 2.
- Any concurrency scenario (two orders in flight at once, closing while
  another order is being placed). The live runner is single-threaded
  against this bridge in the same way the shadow runner is, so this
  isn't expected to matter, but it's untested.
- Anything about the live-trading runner itself, `user_settings` safety
  rails, or reconciliation logic -- those are steps 2-4, not this one.
