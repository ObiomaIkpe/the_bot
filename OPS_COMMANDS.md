# Ops commands reference

Running log of commands used against the Hetzner production server
(`/app4/the-bot`), kept for your own reference so you don't have to
re-derive them next time. Append new ones as they come up — newest at
the bottom, each with what it's for.


8ep-jczF5zGnclX0gwlw5DTauFxDvW416x0glu1ttxw
---

### Check whether a broker credential already exists for a user

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c \
  "select credential_id, broker_name, server, account_type, is_active from broker_credentials where user_id='d4469ab9-742c-4656-8959-c21602dc71c5';"
```

**Purpose:** queries the `broker_credentials` table directly, bypassing
the UI/API, to see whether the admin backend already has a row for a
given user (`user_id` here is Tony's real account — swap for another
user's `user_id` from the `users` table if checking someone else).
`(0 rows)` means the account hasn't been connected through the admin
UI/API yet, even if the MT5 terminal itself is already logged in
separately (the two are unrelated until this row exists).

---

### Look up a user's broker-credential row(s)

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "select credential_id, broker_name, server, account_type from broker_credentials where user_id='d4469ab9-742c-4656-8959-c21602dc71c5';"
```

**Purpose:** `account_login`/`account_password` are stored encrypted
(`account_login_enc` is the real column -- there's no plaintext column
to filter on directly in SQL), so this is how you find a row's
`credential_id` before deleting or otherwise acting on it, when you
can't filter by the login itself.

---

### Delete a wrong broker-credential row

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "delete from broker_credentials where credential_id='52050cd8-967d-4430-bd6e-e2513b7ef364';"
```

**Purpose:** the admin UI's "Broker Connection" form has no edit
button for `account_login`/`account_password`/`server`/`broker_name`
(the PATCH endpoint only supports `is_active` and `bridge_url`) --
delete-and-redo-the-form is the only fix for a bad entry today.
Used here because browser autofill put the account's *email* into the
"MT5 account number" field instead of the real numeric login
(`476123801`). Delete by `credential_id` (found via the lookup query
above), never by a guessed/plaintext filter, since the sensitive
fields aren't queryable directly.
