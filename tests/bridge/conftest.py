"""
bridge/ has no __init__.py anywhere (deliberate -- see
bridge/scripts/provisioning_poller/main.py's own docstring: it's a
namespace package, invoked with C:\\bridge as cwd on the real VPS). This
puts bridge/scripts on sys.path so `provisioning_poller` is importable
the same way here.

Only the parts of provisioning_poller/ that never import MetaTrader5
(Windows-only, not installed in this dev venv) are testable this way --
see provisioner.py's module docstring: MT5 connection work is
deliberately isolated to a verify_mt5_login.py subprocess, never
imported in-process, which is exactly what keeps this importable and
partially unit-testable from a plain Linux dev machine.
"""
import sys
from pathlib import Path

_BRIDGE_SCRIPTS = Path(__file__).resolve().parents[2] / "bridge" / "scripts"
if str(_BRIDGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_SCRIPTS))
