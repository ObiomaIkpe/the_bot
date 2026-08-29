"""
Unit tests for the pure-Python pieces of
bridge/scripts/provisioning_poller/provisioner.py -- the ones that
don't touch MT5, NSSM, or the Windows firewall, so they're the only
parts of the poller testable from this Linux dev machine (see
tests/bridge/conftest.py). Everything else (_launch_and_login,
_verify_login, _install_and_start_service, _open_firewall_port,
_wait_for_health) can only be proven on the real VPS -- see the Phase 1
plan's Verification section.
"""
import json

from provisioning_poller import provisioner
from provisioning_poller.config import PollerConfig
from provisioning_poller.provisioner import _copy_mt5_install, _next_free_port, _rmtree_with_retry, _write_config_json


def _make_config(monkeypatch, bridge_root, **overrides):
    env = {
        "MACHINE_TOKEN": "t", "CREDENTIAL_API_URL": "http://example.invalid",
        "PROVISIONING_PUBLIC_HOST": "203.0.113.1", "FIREWALL_REMOTE_IP": "203.0.113.2",
        "BRIDGE_ROOT": str(bridge_root),
        **overrides,
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return PollerConfig()


def test_next_free_port_defaults_to_base_port_when_nothing_used(tmp_path, monkeypatch):
    config = _make_config(monkeypatch, tmp_path, PROVISIONING_BASE_PORT="8002")
    assert _next_free_port(config) == 8002


def test_next_free_port_skips_tonys_port_and_existing_accounts(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(json.dumps({"port": 8001}), encoding="utf-8")
    accounts_dir = tmp_path / "accounts"
    (accounts_dir / "friend").mkdir(parents=True)
    (accounts_dir / "friend" / "config.json").write_text(json.dumps({"port": 8002}), encoding="utf-8")

    config = _make_config(monkeypatch, tmp_path, PROVISIONING_BASE_PORT="8002")
    assert _next_free_port(config) == 8003


def test_next_free_port_skips_unreadable_config_without_crashing(tmp_path, monkeypatch, caplog):
    accounts_dir = tmp_path / "accounts"
    (accounts_dir / "broken").mkdir(parents=True)
    (accounts_dir / "broken" / "config.json").write_text("not json at all", encoding="utf-8")

    config = _make_config(monkeypatch, tmp_path, PROVISIONING_BASE_PORT="8002")
    assert _next_free_port(config) == 8002  # broken file skipped, not fatal


def test_write_config_json_has_no_secrets_and_expected_shape(tmp_path, monkeypatch):
    config = _make_config(monkeypatch, tmp_path)
    accounts_dir = tmp_path / "accounts" / "abcd1234"
    config_path = accounts_dir / "config.json"
    job = {
        "credential_id": "irrelevant-here",
        "account_login": "12345678",
        "account_password": "super-secret-should-not-appear",
        "server": "Exness-MT5Trial9",
        "magic_numbers": [900003, 900001, 900002],
        "bridge_token": "also-should-not-appear",
    }

    _write_config_json(config_path, accounts_dir, "abcd1234", tmp_path / "MT5-abcd1234" / "terminal64.exe", 8003, job, config)

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written == {
        "account_label": "abcd1234",
        "mt5_terminal_path": str(tmp_path / "MT5-abcd1234" / "terminal64.exe"),
        "default_symbol": "EURUSDm",
        "port": 8003,
        "orders_enabled": False,
        "magic_number": 900001,  # lowest of the three, matching provision_account.ps1's convention
        "magic_numbers": [900001, 900002, 900003],  # sorted
    }
    raw_text = config_path.read_text(encoding="utf-8")
    assert "super-secret-should-not-appear" not in raw_text
    assert "also-should-not-appear" not in raw_text
    assert "12345678" not in raw_text  # account_login itself never written either


def test_copy_mt5_install_skips_temp_logs_and_history(tmp_path):
    """Regression test for a real failure hit on the first live VPS run:
    copying from an actively-running source terminal raised WinError 32
    (file in use) on files under temp/ and Bases/<server>/history/ --
    both get skipped now, and none of them matter for a fresh install
    anyway (see _ignore_volatile_mt5_dirs's docstring)."""
    source = tmp_path / "MT5-Tony"
    (source / "temp" / "EBWebView" / "Default" / "Network").mkdir(parents=True)
    (source / "temp" / "EBWebView" / "Default" / "Network" / "Cookies").write_text("locked-in-real-life")
    (source / "logs").mkdir()
    (source / "logs" / "20260829.log").write_text("tony's own log, irrelevant to a new account")
    (source / "Bases" / "Exness-MT5Trial9" / "history" / "EURUSDm").mkdir(parents=True)
    (source / "Bases" / "Exness-MT5Trial9" / "history" / "EURUSDm" / "2026.hcc").write_text("locked-in-real-life")
    (source / "config").mkdir()
    (source / "config" / "common.ini").write_text("real config that SHOULD be copied")
    (source / "terminal64.exe").write_text("pretend-binary")

    dest = tmp_path / "MT5-newaccount"
    _copy_mt5_install(str(source), dest)

    assert (dest / "terminal64.exe").exists()
    assert (dest / "config" / "common.ini").exists()
    assert not (dest / "temp").exists()
    assert not (dest / "logs").exists()
    assert not (dest / "Bases" / "Exness-MT5Trial9" / "history").exists()


def test_rmtree_with_retry_succeeds_once_the_lock_clears(tmp_path, monkeypatch):
    """Regression test for a real failure hit on the second live VPS
    run: a just-exited MT5 process can hold file handles open briefly
    even after the process itself is gone, so an immediate rmtree can
    silently leave files behind. Simulates a lock that clears after 2
    failed attempts -- the real shutil.rmtree isn't touched, this just
    proves the retry loop actually retries and then succeeds."""
    target = tmp_path / "locked-dir"
    target.mkdir()
    (target / "file.txt").write_text("x")

    calls = {"count": 0}
    real_rmtree = provisioner.shutil.rmtree

    def flaky_rmtree(path, ignore_errors=False):
        calls["count"] += 1
        if calls["count"] < 3:
            return  # simulates the target still existing (locked) after this "attempt"
        real_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(provisioner.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(provisioner.time, "sleep", lambda seconds: None)  # don't actually wait in tests

    _rmtree_with_retry(target, attempts=5, delay_seconds=0)

    assert calls["count"] == 3
    assert not target.exists()


def test_rmtree_with_retry_warns_if_never_clears(tmp_path, monkeypatch, caplog):
    """The other half of the same fix: if it's STILL locked after every
    attempt, this must log a clear warning -- not silently pretend it
    succeeded the way the old bare ignore_errors=True call did."""
    target = tmp_path / "permanently-locked-dir"
    target.mkdir()

    monkeypatch.setattr(provisioner.shutil, "rmtree", lambda path, ignore_errors=False: None)  # never removes it
    monkeypatch.setattr(provisioner.time, "sleep", lambda seconds: None)
    # Guard against some other test in the full suite leaving this
    # module-level logger (a process-wide singleton via
    # logging.getLogger(name)) disabled or non-propagating -- caplog's
    # own level setting alone isn't enough to survive that.
    monkeypatch.setattr(provisioner.log, "disabled", False)
    monkeypatch.setattr(provisioner.log, "propagate", True)

    with caplog.at_level("WARNING", logger="provisioning_poller.provisioner"):
        _rmtree_with_retry(target, attempts=3, delay_seconds=0)

    assert target.exists()
    assert any("Could not fully remove" in record.message for record in caplog.records)
