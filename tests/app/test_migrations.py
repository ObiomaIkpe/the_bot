from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from app.core.database import Base


def test_migrations_match_models(engine):
    """Regression test for the drift found manually earlier: the migration
    files must produce exactly the schema the SQLAlchemy models declare.
    A non-empty diff here means someone edited a migration or a model
    without updating the other."""
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"Migration drift detected: {diff}"
