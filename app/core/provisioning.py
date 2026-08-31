"""
Gives a user their default ModelConfig rows (one per ALL_MODEL_NAMES)
and default UserSettings row -- neither of which anything else in this
codebase ever creates. Called automatically at registration
(app/routers/auth.py) and, for users who registered before this
existed, via the one-time backfill script
(app/scripts/backfill_user_defaults.py). Idempotent by design so both
call sites can use the exact same function safely.

Adding a new model later? New registrations pick it up automatically
the moment it's added to ALL_MODEL_NAMES (app/models/model_config.py) --
existing users need app/scripts/backfill_user_defaults.py re-run
(safe, idempotent, only adds what's missing). See ALL_MODEL_NAMES's own
comment for the full ordered sequence (pipeline -> migration -> here).
"""
import uuid

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.model import Model
from app.models.model_config import ModelConfig
from app.models.user import User
from app.models.user_settings import UserSettings

_MAGIC_NUMBER_ALLOCATION_ATTEMPTS = 3
_MAGIC_NUMBER_FLOOR = 900_000

# No canonical value exists anywhere in this codebase for this field,
# and per shadow_runner/order_manager.py's own comment it's currently
# visibility-only (crossing it logs an event but blocks nothing) -- a
# reasonable starting point to tune, not a load-bearing number.
_DEFAULT_MAX_DAILY_LOSS_PCT = 5.0

# Matches the default symbol already used in shadow_runner/config.py
# and bridge/app/config.py.
_DEFAULT_INSTRUMENT = "EURUSDm"


def _missing_model_names(db: Session, user_id: uuid.UUID) -> list[str]:
    """Every registered model (models table, app/models/model.py) this
    user doesn't already have a ModelConfig row for. Was a hardcoded
    ALL_MODEL_NAMES tuple; now reads the real registry, so a model
    added via POST /admin/models is picked up automatically here too --
    no code change needed per model."""
    all_models = {name for (name,) in db.query(Model.model_name).all()}
    existing = {
        model_name
        for (model_name,) in db.query(ModelConfig.model_name).filter(ModelConfig.user_id == user_id).all()
    }
    return [name for name in all_models if name not in existing]


def _allocate_magic_numbers(db: Session, count: int) -> list[int]:
    current_max = db.query(func.max(ModelConfig.magic_number)).scalar() or _MAGIC_NUMBER_FLOOR
    return [current_max + i + 1 for i in range(count)]


def _insert_model_configs_with_retry(db: Session, pairs: list[tuple[uuid.UUID, str]]) -> None:
    """pairs: (user_id, model_name) rows to insert, each getting a
    freshly allocated, globally-unique magic_number. Shared by
    _provision_missing_models() (one user, every model they're missing)
    and provision_model_for_all_users() (one new model, every existing
    user) -- same magic-number race-retry discipline either way."""
    if not pairs:
        return

    for _attempt in range(_MAGIC_NUMBER_ALLOCATION_ATTEMPTS):
        try:
            magic_numbers = _allocate_magic_numbers(db, len(pairs))
            for (user_id, model_name), magic_number in zip(pairs, magic_numbers):
                db.add(
                    ModelConfig(
                        user_id=user_id,
                        model_name=model_name,
                        status="disabled",  # explicit -- never auto-activated, see model_config.py's docstring
                        risk_pct=0.01,
                        magic_number=magic_number,
                    )
                )
            db.commit()
            return
        except IntegrityError:
            # Another insert raced us for the same magic number(s) --
            # recompute the max (now including their committed rows)
            # and retry. The DB's own unique constraint is what makes
            # this safe to just retry rather than something to avoid.
            db.rollback()

    raise RuntimeError(
        f"Could not allocate unique magic numbers for {len(pairs)} row(s) after "
        f"{_MAGIC_NUMBER_ALLOCATION_ATTEMPTS} attempts"
    )


def _provision_missing_models(db: Session, user_id: uuid.UUID) -> None:
    missing = _missing_model_names(db, user_id)
    _insert_model_configs_with_retry(db, [(user_id, name) for name in missing])


def provision_model_for_all_users(db: Session, model_name: str) -> int:
    """Called right after a new model is registered (POST /admin/models)
    -- backfills a ModelConfig row for every EXISTING user who doesn't
    have one yet for this model_name, so it's available account-wide
    immediately, with no separate script run needed. New registrations
    don't need this: provision_new_user_defaults() already covers them
    via the normal path, since this model now exists in the registry.
    Returns how many users got a new row (for the endpoint's response).
    """
    missing_user_ids = [
        user_id
        for (user_id,) in (
            db.query(User.user_id)
            .outerjoin(
                ModelConfig,
                (ModelConfig.user_id == User.user_id) & (ModelConfig.model_name == model_name),
            )
            .filter(ModelConfig.config_id.is_(None))
            .all()
        )
    ]
    _insert_model_configs_with_retry(db, [(user_id, model_name) for user_id in missing_user_ids])
    return len(missing_user_ids)


def _provision_missing_settings(db: Session, user_id: uuid.UUID) -> None:
    has_settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first() is not None
    if has_settings:
        return

    db.add(
        UserSettings(
            user_id=user_id,
            instrument=_DEFAULT_INSTRUMENT,
            max_daily_loss_pct=_DEFAULT_MAX_DAILY_LOSS_PCT,
            demo_or_live="demo",  # safe default, same philosophy as model status="disabled"
            is_paused=False,
        )
    )
    db.commit()


def provision_new_user_defaults(db: Session, user_id: uuid.UUID) -> None:
    """Idempotent: safe to call on a user who already has some or all of
    their default rows (skips whatever already exists). This is what
    lets both registration and the backfill script share one function
    without either needing to know the other's state."""
    _provision_missing_models(db, user_id)
    _provision_missing_settings(db, user_id)
