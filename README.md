# Phase 0 — Foundation (users, config, journal schema, basic auth)

Stack: Python + FastAPI + SQLAlchemy + Alembic + Postgres. Chosen over
NestJS/TypeORM specifically because Phase 2 needs the official
`MetaTrader5` Python package, which has no equivalent officially-supported
Node client -- staying in one language end-to-end avoids a rewrite later.

**Not yet run or tested against a real database.** This sandbox has no
network access, so none of this has been installed or executed. Every
file was syntax-checked (`python -m py_compile`) but that's it -- please
verify the steps below actually work before trusting this, per the
project's own standard of testing every change in isolation.

## What's here

```
app/
  core/
    config.py     -- Settings (env-var driven: DATABASE_URL, JWT secret, encryption key)
    database.py   -- SQLAlchemy engine/session, Base, get_db() dependency
    security.py   -- password hashing (bcrypt), JWT issue/verify, Fernet encrypt/decrypt
    deps.py       -- get_current_user() FastAPI dependency
  models/
    user.py             -- users
    broker_credential.py -- broker_credentials (login/password encrypted at rest)
    user_settings.py     -- user_settings (incl. live_model / shadow_models split)
    trade.py             -- trades
    event.py             -- events
    notification.py      -- notifications
  schemas/auth.py -- Pydantic request/response models for register/login
  routers/auth.py -- POST /auth/register, POST /auth/login, GET /auth/me
  main.py         -- FastAPI app, mounts the auth router, GET /health
alembic/
  env.py                       -- wires Alembic to app.core.config.settings + all models
  versions/0001_initial_schema.py -- hand-written initial migration (see caveat below)
requirements.txt
.env.example
```

## Setup

1. Create a Postgres database and user, e.g.:
   ```
   createdb trading_bot
   ```
2. Copy `.env.example` to `.env` and fill in real values:
   - `DATABASE_URL` -- your Postgres connection string
   - `JWT_SECRET_KEY` -- any long random string
   - `CREDENTIALS_ENCRYPTION_KEY` -- generate with:
     ```
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Run the migration:
   ```
   alembic upgrade head
   ```
5. Start the API:
   ```
   uvicorn app.main:app --reload
   ```
6. Verify:
   ```
   curl http://localhost:8000/health
   curl -X POST http://localhost:8000/auth/register -H "Content-Type: application/json" \
     -d '{"email": "you@example.com", "password": "a-real-password"}'
   curl -X POST http://localhost:8000/auth/login -d "username=you@example.com&password=a-real-password"
   ```

## Things to specifically check, since I couldn't run this myself

- **The hand-written migration (`0001_initial_schema.py`) matches the
  SQLAlchemy models exactly.** I wrote both by hand rather than running
  `alembic revision --autogenerate` against a live DB. Recommend running
  autogenerate yourself once you have Postgres up (`alembic revision
  --autogenerate -m "check"`) -- it should produce an empty diff if
  0001 is correct. If it doesn't, trust autogenerate's diff, not my file.
- **The `CheckConstraint("NOT (live_model = ANY(shadow_models))")`
  syntax** -- this is valid Postgres (`ANY` over an array column in a
  CHECK constraint), but double check it actually creates without error
  on your Postgres version.
- **`BrokerCredential`'s encrypted columns** use Python `@property`
  getters/setters (`.account_login` / `.account_password`) backed by
  `_account_login_enc` / `_account_password_enc` columns. Any code that
  creates or reads these needs to go through the properties, not the
  underlying `_*_enc` attributes directly -- worth a quick unit test
  confirming round-trip encrypt/decrypt works before this touches any
  real credential.
- **`notifications` table**: the build plan's table definition was
  truncated after the `destination` column when I read it. I filled in
  `event_id` (FK), `status`, `created_at`, `sent_at` based on context --
  flagged in a comment in `app/models/notification.py` too. Please
  compare against your original doc if you have the untruncated version.
- **UUID primary keys**: I chose UUIDs (generated client-side via
  `uuid.uuid4()`, not `gen_random_uuid()` server-side) over serial
  integers, since this is a multi-user product and sequential IDs would
  leak user/trade counts. If you'd rather use integers, that's a
  straightforward model + migration change -- flag it before Phase 1
  builds on top of this.
- **Auth is password + JWT** (the plan left this undecided). Swappable
  later since `password_hash` is already the column name the plan itself
  specified.

## Deliberately not done in Phase 0

Per the plan, these come later: broker adapter, strategy engine, risk
limits UI, actual notification sending, Slack/Discord. Phase 0 is schema
+ auth only.
