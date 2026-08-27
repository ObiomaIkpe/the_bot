# Trading bot admin frontend

React + TypeScript (Vite) admin UI for the trading bot, replacing the
old read-only Streamlit tool (`admin_dashboard/`) with a real,
multi-user, write-capable frontend. See `ADMIN_FRONTEND_PLAN.md` at the
repo root for the original plan (M1-M5) this was built against, and
`~/.claude/plans/misty-seeking-crescent.md` for the screens/design-system
restructuring this README currently describes.

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

## Design system

Dark-mode-first (no light/dark toggle) with a "trading terminal +
SaaS dashboard" blend: dark surfaces and monospace numerals for
financial figures, structured with cards/sidebar/whitespace rather than
a dense terminal layout. Built with **Tailwind CSS v4** (CSS-first
config, no `tailwind.config.js`) plus a handful of small wrapper
components:

- `src/index.css` — `@theme` block defining the color/font tokens as
  real Tailwind theme values (`bg-accent`, `text-positive`, `font-mono`,
  etc. all come from here) plus a small `@layer base` for body/input
  defaults. Swap values here to retheme; nothing else hardcodes a color.
- `src/lib/buttonStyles.ts` — the Tailwind class strings for each
  `Button` variant, kept in a plain module (not `Button.tsx` itself) so
  a `<Link>` that needs to look like a button (e.g. Overview's "Connect
  MT5 account" empty-state action) can reuse the same classes.
- `src/components/{Card,Button,Badge,StatTile,EmptyState,Table}.tsx` —
  thin React wrappers applying Tailwind utility classes. Use these
  instead of hand-rolling className strings in a new page.
- `src/components/ModelCard.tsx` — the per-model status/P&L/win-rate
  card shared by Overview and Models (same card, different grid).
- `src/lib/pnl.ts` — client-side trade aggregation (today/week/all-time
  P&L, win rate). There's no backend endpoint for this; the trade
  volume is low enough that computing it from an already-fetched trade
  list is fine.

An earlier pass at this design system used plain hand-written CSS
classes instead of Tailwind; migrated to Tailwind on request (personal
tooling preference) — same visual result, no backend or behavior change.

## What's here

- `src/api/client.ts` — typed fetch wrapper. Attaches the JWT to every
  request, redirects to `/login` on any 401 (see `setUnauthorizedHandler`).
- `src/auth/` — `AuthContext` (login/logout/token state) and
  `ProtectedRoute` (redirects unauthenticated visitors to `/login`).
- `src/components/RootRedirect.tsx` — the default post-login landing:
  sends a customer with zero connected broker accounts to Broker
  Connection (first-run), everyone else to Overview.
- `src/pages/Login.tsx` — email/password form, calls `/auth/login`.
- `src/pages/Overview.tsx` — the default landing page: connection
  health, account balance/equity, P&L today/week/all-time, a per-model
  card grid, and a trimmed recent-activity feed.
- `src/pages/Models.tsx` + `src/pages/ModelDetail.tsx` — the full
  per-model card grid, and a drill-in per model showing its own trade
  history, computed stats, and status/pause controls.
- `src/pages/Live.tsx` — real open positions and pending orders, with
  Close/Cancel actions gated behind a confirmation modal
  (`src/components/ConfirmModal.tsx`) since these are real, irreversible
  broker actions once a broker account is actually connected
  (`bridge_url` set on a `broker_credentials` row).
- `src/pages/TradeHistory.tsx` — the full trades table with filters
  (model/outcome/shadow/days-back) and client-side column sort.
- `src/pages/BrokerCredentials.tsx` — connect an MT5 account. Shows a
  prominent connect form when no accounts exist yet (first-run), the
  usual management table once at least one does. Mints/re-mints a
  bridge token (shown once, with a warning before re-minting since that
  invalidates whatever token a running bridge worker currently holds).
- `src/pages/AccountSettings.tsx` — the account-wide emergency-stop
  pause and max daily loss %.
- `@tanstack/react-query` handles data fetching/caching/polling
  throughout (e.g. the event feed and Live page auto-refresh) instead of
  Streamlit's meta-refresh-tag approach.

Old routes `/dashboard` and `/settings` still resolve (redirected to
`/overview` and `/account-settings`) so no previously-bookmarked link
goes dead.

## Testing

```
npm run test
```

Vitest + React Testing Library. Coverage so far focuses on the parts
most likely to have subtle bugs: `src/api/client.ts` (token
attach/clear, 401 handling, form-vs-JSON request bodies) and
`src/auth/` (login/logout state transitions, `ProtectedRoute`'s
redirect behavior). No component-level tests for the data-heavy pages
yet — those are thinner wrappers around `apiClient` + react-query and
mostly amount to "does this render a table," lower-value to test than
the auth plumbing.

## Known gaps / next steps

- No light/dark toggle — dark is the only theme for now.
- No charting library — P&L/win-rate are numeric stats, not charts.
- No connection-health endpoint on the backend (`BridgeClient.health()`
  exists server-side but isn't wired to any route) — Overview/Live infer
  health from the existing 503 ("not configured") / 502 ("bridge
  unreachable") responses on `GET /trading/account-info` instead.
- No profile/password-change screen.
