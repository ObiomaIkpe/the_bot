# Trading bot admin frontend

React + TypeScript (Vite) admin UI for the trading bot, replacing the
old read-only Streamlit tool (`admin_dashboard/`) with a real,
multi-user, write-capable frontend. See `ADMIN_FRONTEND_PLAN.md` at the
repo root for the full plan (M1-M5) this was built against.

## Setup

```
cp .env.example .env   # then edit VITE_API_BASE_URL if needed
npm install
npm run dev
```

Opens on `http://localhost:5173`. Points at the backend URL set in
`.env` (defaults to `http://localhost:8000` for local dev against
`uvicorn app.main:app`; use `https://api.ihusale.com.ng` for the real
deployed backend). The backend's CORS config already allows
`http://localhost:5173` by default.

## What's here

- `src/api/client.ts` — typed fetch wrapper. Attaches the JWT to every
  request, redirects to `/login` on any 401 (see `setUnauthorizedHandler`).
- `src/auth/` — `AuthContext` (login/logout/token state) and
  `ProtectedRoute` (redirects unauthenticated visitors to `/login`).
- `src/pages/Login.tsx` — email/password form, calls `/auth/login`.
- `src/pages/Dashboard.tsx` — event feed + trades table, both scoped
  automatically to the logged-in user by the backend's own JWT-based
  scoping (no user/account picker needed here).
- `src/pages/Settings.tsx` — per-model status (disabled/shadow/active)
  and per-model pause switches, plus the account-wide emergency-stop
  pause and max daily loss %.
- `src/pages/Live.tsx` — real open positions and pending orders, with
  Close/Cancel actions gated behind a confirmation modal
  (`src/components/ConfirmModal.tsx`) since these are real, irreversible
  broker actions once a broker account is actually connected
  (`bridge_url` set on a `broker_credentials` row).
- `@tanstack/react-query` handles data fetching/caching/polling
  throughout (e.g. the event feed and Live page auto-refresh) instead of
  Streamlit's meta-refresh-tag approach.

## Known gaps / next steps

- No UI yet for creating/managing `broker_credentials` rows themselves
  (submitting MT5 login details, minting a bridge token) — that's
  currently done via direct API calls (`curl`), not through this
  frontend. Worth adding a page for this.
- Styling is minimal/inline — functional, not polished.
- No tests yet.
