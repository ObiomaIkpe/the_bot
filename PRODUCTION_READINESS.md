# Production readiness assessment

Phase 0 was scoped as "schema + auth only" (see README). This documents
what already follows standard FastAPI conventions vs. what's still
missing before this could run in production. Written after getting the
app running end-to-end locally (venv, Postgres, migrations, register/
login all verified working).

## Code patterns: standard

- `pydantic-settings.BaseSettings` reading from `.env` -- the standard
  12-factor config approach.
- `Depends(get_db)` yielding a SQLAlchemy session, closed in `finally`
  -- the canonical FastAPI+SQLAlchemy dependency pattern.
- `OAuth2PasswordBearer` / `OAuth2PasswordRequestForm` for login,
  `Depends(get_current_user)` for protected routes -- lifted from
  FastAPI's own "OAuth2 with Password and Bearer" tutorial.
- `APIRouter(prefix=..., tags=...)` mounted via `include_router` --
  standard router separation.
- Alembic for schema migrations rather than
  `Base.metadata.create_all()`.
- `pool_pre_ping=True` on the engine -- avoids stale-connection errors.
- Password hashing (bcrypt, one-way) kept structurally separate from
  reversible Fernet encryption for broker credentials.
- Automated tests (`pytest`) + CI (GitHub Actions) -- see "Tests + CI"
  below. Done as of 2026-07-18.
- Logging + error handling -- see "Logging + error handling" below.
  Done as of 2026-07-18.

## Originally-unverified README items: now closed (2026-07-18)

The original Phase 0 README flagged two things it couldn't verify
itself (no network access, never run against a real DB). Both are now
covered by tests:

- **`BrokerCredential` encrypt/decrypt round-trip**
  (`tests/test_broker_credential_encryption.py`) -- confirms
  `.account_login`/`.account_password` round-trip correctly through a
  real Postgres row, that the raw `_account_password_enc` column is
  genuinely ciphertext (not plaintext passed through unchanged), and
  that encrypting the same password twice produces different
  ciphertext (Fernet's random IV) -- a cheap guard against an
  accidental switch to a deterministic cipher later.
- **`user_settings` CHECK constraints**
  (`tests/test_user_settings_constraints.py`) -- confirms
  `NOT (live_model = ANY(shadow_models))` is real and enforced
  (rejects `live_model` appearing in its own `shadow_models`), that the
  valid case still passes (so the constraint isn't just rejecting
  everything), and that `live_model IN ('fvg', 'ob', 'fvg_ob')` is
  enforced too.

## Missing for production deployment

Reasonable to be missing at "Phase 0 schema + auth only" -- listed here
so they don't get forgotten before a real deploy:

- **No app factory / no environment separation** -- one global
  `app = FastAPI()`, no dev/staging/prod config split.
- **Run via `uvicorn --reload` in a foreground terminal** -- dev-only.
  Production needs multiple workers (`uvicorn --workers N` or
  Gunicorn + `UvicornWorker`), behind a reverse proxy (nginx/Caddy)
  doing TLS termination, managed by systemd/Docker/Kubernetes.
- **No error tracking / request tracing** -- logging now exists (see
  below), but there's still no Sentry-style aggregation or distributed
  tracing (OpenTelemetry).
- **No CORS middleware** -- needed once a browser frontend calls this
  from a different origin.
- **No containerization** -- no `Dockerfile` / `docker-compose.yml`.
- **No rate limiting** on `/auth/login` -- brute-force protection.
- **Secrets in a flat `.env` file** -- fine for local dev, production
  standard is a secrets manager (Vault, AWS Secrets Manager, k8s
  Secret) rather than a file on disk.

## Logging + error handling (added 2026-07-18)

- `app/core/logging.py` -- `logging.config.dictConfig` setup, always
  logging to stdout (12-factor: the deploying platform collects logs,
  the app doesn't manage log files). Format is switchable via the
  `LOG_FORMAT` setting: `"text"` (default, human-readable) or `"json"`
  (structured, one JSON object per line -- for CloudWatch/Loki/Datadog
  etc). Also tunes `uvicorn`/`uvicorn.access`/`uvicorn.error` onto the
  same handler, and quiets `sqlalchemy.engine` to `WARNING` (it echoes
  every SQL statement at `INFO`).
- `app/main.py` -- calls `configure_logging()` before constructing the
  app; added `@app.exception_handler(Exception)` that logs the full
  traceback (`exc_info=exc`) and returns a generic
  `{"detail": "Internal server error"}` 500, never leaking internals.
  This directly addresses the bcrypt bug from earlier, which previously
  surfaced as an opaque 500 with no visible trace.
- `/health` now runs `SELECT 1` through `get_db` and returns 503 (not a
  static 200 stub) if the database is unreachable -- the standard
  liveness-vs-readiness distinction.
- `app/routers/auth.py` -- logs successful registration and failed/
  rejected login attempts (never logs passwords).
- Tests: `tests/test_logging.py` (JSON formatter output shape),
  `tests/test_error_handling.py` (unhandled exception -> generic 500),
  `tests/test_health.py` (503 when DB is unreachable, via a fake
  session that raises on `execute()`).

### Note on testing FastAPI's catch-all exception handler

Starlette's `TestClient` re-raises the original exception by default
(`raise_server_exceptions=True`) even when a registered `Exception`
handler produced a response -- intentional, so ordinary tests still
surface real bugs instead of the handler silently papering over them.
To test the handler's own behavior, `tests/test_error_handling.py` uses
a dedicated `TestClient(app, raise_server_exceptions=False)` rather
than the shared `client` fixture.

## Tests + CI (added 2026-07-18)

- `tests/conftest.py` -- creates a throwaway `trading_bot_test`
  database, runs the real Alembic migrations against it (not
  `create_all()`, so schema drift is actually testable), and wraps each
  test in its own transaction (SQLAlchemy 2.0
  `join_transaction_mode="create_savepoint"`) that's rolled back after,
  so tests never see each other's data.
- `tests/test_migrations.py` -- automates the manual
  `alembic revision --autogenerate` drift check done earlier: asserts
  `compare_metadata()` returns an empty diff against the migrated test
  DB. Directly guards against the `UNIQUE CONSTRAINT` vs. unique-index
  bug found in `0001_initial_schema.py`.
- `tests/test_health.py`, `tests/test_auth.py` -- health check,
  register (success + duplicate-email 409), login (success + wrong
  password + unknown user), `/auth/me` (requires token, returns current
  user).
- `.github/workflows/ci.yml` -- spins up a Postgres 16 service
  container, installs `requirements.txt` + `requirements-dev.txt`, runs
  `pytest` on every push/PR.
- `requirements-dev.txt` -- `pytest`, `httpx` (FastAPI's `TestClient`
  needs it), layered on top of `requirements.txt`.

### Bug found while building this

**`alembic/env.py` ignored programmatic URL overrides.** Line 13 did
`config.set_main_option("sqlalchemy.url", settings.database_url)`
unconditionally, clobbering any URL a caller had already set on the
`Config` object -- which is exactly what the test fixture does to point
migrations at `trading_bot_test` instead of the real dev database. This
made the test suite's migrations silently run against `trading_bot`
(already at `head`) instead of the test DB, so `alembic upgrade head`
reported success while doing nothing. Fixed by only falling back to
`settings.database_url` when the configured URL is still the literal
`alembic.ini` placeholder (`driver://user:pass@localhost/dbname`);
an explicit override now wins.

A second, related bug surfaced while fixing the first: `str(url)` /
`url.render_as_string()` default to `hide_password=True` in modern
SQLAlchemy, masking the password as `***`. The test fixture was doing
`alembic_cfg.set_main_option("sqlalchemy.url", str(_TEST_DB_URL))`,
which silently baked a masked password into the config and caused
`password authentication failed`. Fixed by using
`_TEST_DB_URL.render_as_string(hide_password=False)` instead.

## Bugs found and fixed while getting this running

1. **Migration drift** -- `0001_initial_schema.py` gave `users.email`
   and `user_settings.user_id` both a separate `UNIQUE CONSTRAINT` and
   a redundant non-unique index, instead of the single unique index
   the models actually declare (`Column(unique=True, index=True)`).
   Fixed by moving `unique=True` onto the `create_index()` calls.
   Confirmed via `alembic revision --autogenerate` producing an empty
   diff after the fix.
2. **Missing `email-validator` dependency** -- `pydantic.EmailStr` (used
   in `app/schemas/auth.py`) requires it, but `requirements.txt` only
   listed `pydantic>=2.6`. Fixed: `pydantic[email]>=2.6`.
3. **passlib/bcrypt incompatibility** -- `passlib` 1.7.4 (unmaintained
   since 2020) breaks under `bcrypt>=4.0`: it reads
   `bcrypt.__about__.__version__` (removed in bcrypt 4.1) and assumes
   bcrypt silently truncates passwords over 72 bytes (bcrypt>=4.0
   raises `ValueError` instead). Fixed by pinning `bcrypt<4.0` in
   `requirements.txt`.
