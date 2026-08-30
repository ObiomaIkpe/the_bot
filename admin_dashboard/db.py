"""
Read-only database connection for the admin dashboard.

This tool is deliberately standalone (its own folder, own
requirements.txt, run as its own process) but reuses the main bot
repo's SQLAlchemy models (Event, Trade, ModelConfig, ...) instead of
redefining the schema here a second time -- redefining it would drift
out of sync with the real schema the moment a migration changes
anything over there.

To make that import work without turning this into a copy of the main
repo: since this folder now lives inside the_bot itself (a sibling of
app/, phase1/, shadow_runner/, ...), the repo root is just this file's
parent directory -- computed automatically below, no config needed.
Set MAIN_REPO_PATH only if you ever run this dashboard from somewhere
that *isn't* inside the_bot (it overrides the auto-detected path).

SAFETY: every connection this tool opens is set read-only at the
Postgres session level (`SET default_transaction_read_only = on`) the
moment it connects -- not just "the code never calls .commit()", but
the database itself will refuse any INSERT/UPDATE/DELETE attempted
through this tool's engine, even a future bug. This tool should never
be able to change anything about the live bot's data.
"""
import os
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Auto-detected: parent of this file's directory, i.e. the_bot/ itself,
# since admin_dashboard/ now lives inside it. Override with the
# MAIN_REPO_PATH env var only if that's ever not true (e.g. running this
# dashboard from somewhere outside the_bot).
_AUTO_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_REPO_PATH = os.environ.get("MAIN_REPO_PATH", _AUTO_REPO_ROOT)
if MAIN_REPO_PATH not in sys.path:
    sys.path.insert(0, MAIN_REPO_PATH)

# Imported only after sys.path is patched above.
from app.models import AuditLog, Event, ModelConfig, Trade  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Same connection string the main app "
        "uses, e.g.:\n"
        "  export DATABASE_URL=postgresql://user:pass@host:5432/trading_bot"
    )

engine = create_engine(DATABASE_URL)


@event.listens_for(engine, "connect")
def _set_read_only(dbapi_connection, connection_record):
    """Runs once per new physical connection. Belt-and-suspenders with
    the app-level 'never call .commit()' discipline below -- this makes
    it a real database-enforced guarantee, not just a coding
    convention this tool has to keep remembering to follow."""
    cursor = dbapi_connection.cursor()
    cursor.execute("SET default_transaction_read_only = on")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


def get_session():
    """One session per Streamlit script run (Streamlit reruns the whole
    script on every interaction, so this is cheap to call fresh each
    time rather than trying to hold one open across reruns)."""
    return SessionLocal()