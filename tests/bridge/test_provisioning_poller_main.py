"""
Unit test for provisioning_poller/main.py's _configure_logging() --
logging/audit review, part 2 (log rotation). Everything else in main.py
(main() itself) can't be tested from here: it calls PollerConfig()
(needs real env vars) and runner.run_forever() (an infinite loop) --
see tests/bridge/test_provisioning_poller_provisioner.py's own docstring
for why only the pure-Python pieces are testable on this dev machine.
"""
import logging
import logging.handlers

from provisioning_poller.main import _configure_logging


def test_configure_logging_creates_logs_dir_and_rotating_file_handler(tmp_path):
    bridge_root = tmp_path / "bridge_root"
    # Deliberately doesn't exist yet -- _configure_logging must create it.
    assert not bridge_root.exists()

    _configure_logging(str(bridge_root))

    assert (bridge_root / "logs").is_dir()
    handlers = logging.getLogger().handlers
    file_handlers = [h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].baseFilename == str(bridge_root / "logs" / "poller.log")
    assert file_handlers[0].maxBytes == 10 * 1024 * 1024
    assert file_handlers[0].backupCount == 5

    stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler) and h not in file_handlers]
    assert len(stream_handlers) == 1


def test_configure_logging_actually_writes_to_the_file(tmp_path):
    bridge_root = tmp_path / "bridge_root"
    _configure_logging(str(bridge_root))

    logging.getLogger("test.logger").info("a real log line")

    log_file = bridge_root / "logs" / "poller.log"
    assert log_file.exists()
    assert "a real log line" in log_file.read_text()
