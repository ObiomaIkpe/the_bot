"""
The registry of every trading model this system knows about --
previously a hardcoded tuple (ALL_MODEL_NAMES in model_config.py) plus
two separate hardcoded DB CHECK constraints (on events.model and
trades.model). Adding a model used to require a migration + a 4-step
manual process (see model_config.py's git history); now it's "insert
one row here" (POST /admin/models), and everything else --
provisioning, the FK constraints on events/trades/model_configs, every
frontend dropdown -- reads from this table instead.
"""
from sqlalchemy import Column, DateTime, String, func

from app.core.database import Base


class Model(Base):
    __tablename__ = "models"

    # The plain string IS the primary key, deliberately -- every
    # existing model/model_name column across this codebase (trades,
    # events, model_configs) is already a plain string, and every
    # comparison against it (model == "fvg", etc.) stays unchanged.
    # Adding a synthetic UUID id here would mean either duplicating
    # both columns everywhere or rewriting every one of those
    # comparisons -- not worth it for a table that's never joined by
    # anything but this string.
    model_name = Column(String, primary_key=True)
    display_name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
