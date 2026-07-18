import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.main import app

_TEST_DB_URL = make_url(settings.database_url).set(
    database=make_url(settings.database_url).database + "_test"
)
_ADMIN_DB_URL = _TEST_DB_URL.set(database="postgres")


@pytest.fixture(scope="session")
def engine():
    """Creates a throwaway Postgres database and applies the real Alembic
    migrations against it (not Base.metadata.create_all()) -- this way
    test_migrations.py's drift check is actually meaningful, and the rest
    of the suite runs against the same schema production will get."""
    admin_engine = create_engine(_ADMIN_DB_URL, isolation_level="AUTOCOMMIT")
    dbname = _TEST_DB_URL.database
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin_engine.dispose()

    alembic_cfg = Config("alembic.ini")
    # str(url)/render_as_string() default to hide_password=True (masks it as
    # "***") -- must render with the real password or auth fails downstream.
    alembic_cfg.set_main_option(
        "sqlalchemy.url", _TEST_DB_URL.render_as_string(hide_password=False)
    )
    command.upgrade(alembic_cfg, "head")

    eng = create_engine(_TEST_DB_URL)
    yield eng
    eng.dispose()

    admin_engine = create_engine(_ADMIN_DB_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
    admin_engine.dispose()


@pytest.fixture
def db_session(engine):
    """Each test runs inside its own transaction that's rolled back
    afterward, so tests never see each other's data even though they
    share one migrated database for the whole session."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
