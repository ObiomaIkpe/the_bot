"""
Standalone verification step for provision_account.ps1: proves a given
MT5 terminal install actually connects and logs in, using the same
MetaTrader5 Python package the bridge itself depends on (see
requirements.txt) -- not just "the terminal process started", which
tells you nothing about whether the login/server were actually correct.

Deliberately independent of the rest of bridge/app/ -- this runs during
first-time provisioning, before that account's config.json even exists,
so it can't import bridge/app/config.py (which requires one).

Exit code 0 + prints the account's balance/currency on success.
Exit code 1 + prints the MT5 error on failure. Always calls
mt5.shutdown() before exiting, success or failure, so this never leaves
a dangling connection for the caller's next attempt.

Usage:
    python verify_mt5_login.py --path C:\\MT5-friend\\terminal64.exe \\
        --login 12345678 --password "..." --server "Exness-MT5Trial9"
"""
import argparse
import sys

import MetaTrader5 as mt5


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, help="Path to terminal64.exe")
    parser.add_argument("--login", required=True, type=int)
    parser.add_argument("--password", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    args = parser.parse_args()

    ok = mt5.initialize(
        path=args.path,
        login=args.login,
        password=args.password,
        server=args.server,
        timeout=args.timeout_ms,
    )
    if not ok:
        print(f"mt5.initialize() failed: {mt5.last_error()}")
        mt5.shutdown()
        return 1

    info = mt5.account_info()
    if info is None:
        print(f"initialize() succeeded but account_info() returned None: {mt5.last_error()}")
        mt5.shutdown()
        return 1

    print(f"Connected: login={info.login} server={info.server} balance={info.balance} {info.currency}")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
