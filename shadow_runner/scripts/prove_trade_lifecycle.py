"""
shadow_runner/scripts/prove_trade_lifecycle.py

Standalone proof that the system can autonomously carry a trade through
its full real lifecycle: place -> fill -> target-attach -> close.

Deliberately bypasses DayOrchestrator entirely. Constructs a real
OrderManager directly (same class runner.py uses, just not day-scoped
here) and feeds it a SYNTHETIC trade_candidate_ready event -- shaped
exactly like phase1/streaming/day_orchestrator.py's real one (verified
against its actual _emit() call: event_type, timestamp, direction,
entry, stop, raid_bar, mss_bar). Uses a dedicated test magic number
(999999) so it can never collide with real fvg (900001) trade history
or model_configs, and never touches DayOrchestrator/CurrentDay at all.

Run manually, one-off, from the shadow_runner container (or anywhere
with BRIDGE_URL/DATABASE_URL configured the same way the real runner is):

    python -m shadow_runner.scripts.prove_trade_lifecycle \
        --user-id d4469ab9-742c-4656-8959-c21602dc71c5

BUGS FOUND + FIXED while wiring this against the real bridge_client.py
(both in shadow_runner/bridge_client.py, same commit as this script):
  1. get_positions()/get_pending_orders() were sending only_ours=True,
     which the bridge resolves SERVER-SIDE to its own config.magic_number
     (900001) regardless of the magic argument passed in -- any other
     magic (this script's 999999, and eventually OB's/fvg_ob's own
     magics) would always get an empty list back, silently. Fixed to
     only_ours=False + the pre-existing client-side magic filter, which
     was already correct and just never got real data to filter.
  2. bridge_client.py never wrapped the bridge's own
     POST /positions/{ticket}/close (only close_position_partial
     existed) -- added close_position(), needed for this script's
     teardown step.
Both were load-bearing for THIS script (magic 999999 != 900001) and
would eventually have broken OB/fvg_ob once either went active with
its own magic number, independent of this script ever existing.
"""
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.database import SessionLocal
from shadow_runner.bridge_client import BridgeClient, BridgeError
from shadow_runner.order_manager import OrderManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("prove_trade_lifecycle")

NY_TZ = ZoneInfo("America/New_York")
BAR_DURATION = timedelta(minutes=5)  # matches runner.py's M5 convention

TEST_MAGIC_NUMBER = 999999      # isolated from real fvg (900001)
TEST_MODEL_NAME = "test"        # -> comment "TEST:long-0" via build_comment()
POLL_INTERVAL_SECONDS = 5
MAX_WAIT_SECONDS = 180


def now_ny() -> datetime:
    return datetime.now(NY_TZ).replace(tzinfo=None)


def wait_until(check_fn, description: str, max_wait: int = MAX_WAIT_SECONDS):
    """Poll check_fn() until it returns a truthy value, or raise on timeout."""
    elapsed = 0
    while elapsed < max_wait:
        result = check_fn()
        if result:
            return result
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
    raise TimeoutError(f"Timed out after {max_wait}s waiting for: {description}")


def build_synthetic_event(direction: str, entry: float, stop: float) -> dict:
    """
    Shaped exactly like DayOrchestrator's real trade_candidate_ready
    (verified against phase1/streaming/day_orchestrator.py's actual
    _emit() call). raid_bar/mss_bar are synthetic (0, 0) -- they only
    need to be present and form a unique candidate_key; they don't need
    to correspond to real bar indices since we're bypassing the
    orchestrator that would normally have produced them.
    """
    return {
        "event_type": "trade_candidate_ready",
        "timestamp": now_ny(),
        "direction": direction,
        "entry": float(entry),
        "stop": float(stop),
        "raid_bar": 0,
        "mss_bar": 0,
    }


def run(user_id: str, bridge_url: str, symbol: str, direction: str, offset_pips: float) -> None:
    log.info("=== Trade lifecycle proof starting (magic=%s, user=%s, symbol=%s) ===",
              TEST_MAGIC_NUMBER, user_id, symbol)

    bridge = BridgeClient(bridge_url)

    model_config = {
        "model_name": TEST_MODEL_NAME,
        "status": "active",       # must be 'active' -- is_active() gates on_trade_candidate_ready()
        "risk_pct": 0.005,        # deliberately small (0.5%) -- this is a mechanics proof, not a real bet
        "magic_number": TEST_MAGIC_NUMBER,
    }

    events = []
    order_manager = OrderManager(
        model_config=model_config,
        symbol=symbol,
        bridge=bridge,
        session_factory=SessionLocal,
        user_id=user_id,
        event_sink=events.append,
    )

    # --- build a near-market pending order so it fills fast without being a market order ---
    tick = bridge.get_symbol_info(symbol)
    account = bridge.account_info()
    log.info("Account balance: %s", account["balance"])

    # get_candles is used here just to derive an approximate current
    # price -- the bridge has no standalone /tick wrapper in
    # bridge_client.py (only get_candles), so we use the most recent bar's close.
    recent = bridge.get_candles(symbol, "M5", 5)
    current_price = recent[-1]["close"]
    pip_size = 0.0001  # matches order_manager.PIP

    if direction == "long":
        entry = round(current_price + offset_pips * pip_size, 5)
        stop = round(entry - 15 * pip_size, 5)
    else:
        entry = round(current_price - offset_pips * pip_size, 5)
        stop = round(entry + 15 * pip_size, 5)

    event = build_synthetic_event(direction, entry, stop)
    log.info("Synthetic trade_candidate_ready: direction=%s entry=%.5f stop=%.5f", direction, entry, stop)

    # --- STEP 1: feed it straight to OrderManager, exactly as
    # runner.py's combined_sink does for the real fvg model ---
    order_manager.on_trade_candidate_ready(event)
    for e in events[:]:
        log.info("EVENT: %s", e)
    if not order_manager._pending:
        log.error(
            "No pending order was recorded -- on_trade_candidate_ready() returned without "
            "placing anything. Check the events above (order_placement_failed / "
            "order_skipped_paused are the two silent-return paths) before re-running."
        )
        sys.exit(1)

    # --- STEP 2: wait for a real fill ---
    log.info("Waiting for real fill...")

    def check_fill():
        return order_manager.check_for_fills()

    wait_until(check_fill, "order fill")
    log.info("FILLED: position_ticket=%s fill_price=%s",
              order_manager._winner_position_ticket, order_manager._real_fill_price)

    # --- STEP 3: attach target from real recent bars, same as runner.py's
    # _check_order_manager_fills() does post-fill ---
    candles = bridge.get_candles(symbol, "M5", 20)
    closed_bars = [c for c in candles if c["time_ny"].replace(tzinfo=None) + BAR_DURATION <= now_ny()]
    order_manager.attach_target(closed_bars)

    def check_target():
        positions = bridge.get_positions(TEST_MAGIC_NUMBER)
        pos = next((p for p in positions if p["ticket"] == order_manager._winner_position_ticket), None)
        return pos if (pos and pos.get("take_profit")) else None

    with_target = wait_until(check_target, "take-profit attach")
    log.info("TARGET ATTACHED: ticket=%s take_profit=%s", with_target["ticket"], with_target["take_profit"])

    # --- STEP 4: teardown -- close the position ourselves (this is the
    # proof's own responsibility, not something the real system would
    # normally do mid-lifecycle) ---
    log.info("Lifecycle proven through target-attach. Closing position now (teardown).")
    close_result = bridge.close_position(order_manager._winner_position_ticket)
    log.info("CLOSED: %s", close_result)

    # --- STEP 5: confirm OrderManager's own close detection sees it,
    # same check_for_close() the real runner polls every cycle ---
    def check_close_detected():
        return order_manager.check_for_close()

    close_info = wait_until(check_close_detected, "close detection via OrderManager.check_for_close()")
    log.info("CLOSE DETECTED BY ORDER MANAGER: %s", close_info)

    outcome = order_manager.get_real_outcome()
    log.info("=== Full lifecycle proven: place -> fill -> target-attach -> close ===")
    log.info("Final real_outcome: %s", outcome)


def main():
    import os

    parser = argparse.ArgumentParser(description="Prove the autonomous trade lifecycle end-to-end.")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--bridge-url", default=os.environ.get("BRIDGE_URL", "").rstrip("/"),
                         help="Defaults to BRIDGE_URL env var, same as the real shadow_runner.")
    parser.add_argument("--symbol", default=os.environ.get("SHADOW_RUNNER_SYMBOL", "EURUSDm"))
    parser.add_argument("--direction", choices=["long", "short"], default="long")
    parser.add_argument("--offset-pips", type=float, default=3.0,
                         help="Distance from current price for the pending order -- close enough to fill fast, far enough not to be a market order.")
    args = parser.parse_args()

    if not args.bridge_url:
        parser.error("--bridge-url or BRIDGE_URL env var is required")

    try:
        run(args.user_id, args.bridge_url, args.symbol, args.direction, args.offset_pips)
    except TimeoutError as e:
        log.error("TIMEOUT: %s", e)
        sys.exit(1)
    except BridgeError as e:
        log.error("BRIDGE ERROR: %s", e)
        sys.exit(1)
    except Exception:
        log.exception("Proof script failed")
        sys.exit(1)


if __name__ == "__main__":
    main()