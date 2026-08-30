# Trading bot admin frontend

React + TypeScript (Vite) admin UI for the trading bot. Replaced the
old read-only Streamlit tool (`admin_dashboard/`, removed 2026-08-30)
with a real, multi-user, write-capable frontend, plus (M6) a
role-gated `/admin/*` section covering the same cross-user views that
tool used to. See `ADMIN_FRONTEND_PLAN.md` at the repo root for the
original plan (M1-M6) this was built against, and
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

Dark by default, with a light theme available via the sidebar toggle,
in a "trading terminal + SaaS dashboard" blend: dark surfaces and
monospace numerals for financial figures, structured with
cards/sidebar/whitespace rather than a dense terminal layout. Built
with **Tailwind CSS v4** (CSS-first config, no `tailwind.config.js`)
plus a handful of small wrapper components:

- `src/index.css` — `@theme` block defining the color/font tokens as
  real Tailwind theme values (`bg-accent`, `text-positive`, `font-mono`,
  etc. all come from here) plus a `:root[data-theme="light"]` block
  overriding those same tokens with light-mode values, plus a small
  `@layer base` for body/input defaults. Swap values here to retheme;
  nothing else hardcodes a color.
- `src/theme/ThemeContext.tsx` — `ThemeProvider`/`useTheme()` (same
  shape as `src/auth/AuthContext.tsx`), toggles
  `document.documentElement`'s `data-theme` attribute + persists to
  `localStorage`. Because every component reads the semantic tokens
  above rather than hardcoded colors, the toggle needed **zero changes**
  to any existing component — only this context plus the light-palette
  block in `index.css`. `src/components/ThemeToggle.tsx` is the sidebar
  control for it.
- `src/lib/buttonStyles.ts` — the Tailwind class strings for each
  `Button` variant, kept in a plain module (not `Button.tsx` itself) so
  a `<Link>` that needs to look like a button (e.g. Overview's "Connect
  MT5 account" empty-state action) can reuse the same classes.
- `src/components/{Card,Button,Badge,StatTile,EmptyState,Table}.tsx` —
  thin React wrappers applying Tailwind utility classes. Use these
  instead of hand-rolling className strings in a new page.
- `src/components/ModelCard.tsx` — the per-model status/P&L/win-rate
  card shared by Overview and Models (same card, different grid).
- `src/components/PnlChart.tsx` — a Recharts line chart of cumulative
  P&L over time, colored via the same CSS variables as everything else
  so it re-themes automatically with the toggle. Used on Overview
  (all models) and Model detail (single model).
- `src/lib/pnl.ts` — client-side trade aggregation (today/week/all-time
  P&L, win rate, cumulative series for the chart). There's no backend
  endpoint for this; the trade volume is low enough that computing it
  from an already-fetched trade list is fine.

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
  health (via `GET /trading/health`, distinguishing "not configured"
  503 / "bridge unreachable" 502 / "bridge up but MT5 disconnected" 200
  three ways account-info alone couldn't), account balance/equity, P&L
  today/week/all-time plus a cumulative P&L chart, a per-model card
  grid, and a trimmed recent-activity feed.
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
- `src/pages/Profile.tsx` — account email/status (`GET /auth/me`) and a
  change-password form (`POST /auth/change-password`).
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
attach/clear, 401 handling, form-vs-JSON request bodies), `src/auth/`
(login/logout state transitions, `ProtectedRoute`'s redirect behavior),
and `src/theme/ThemeContext.test.tsx` (toggle flips the DOM attribute +
persists to localStorage). No component-level tests for the data-heavy
pages yet — those are thinner wrappers around `apiClient` + react-query
and mostly amount to "does this render a table," lower-value to test
than the auth/theme plumbing.

## Known gaps / next steps

- The light theme's colors are a first pass, not visually verified —
  no browser tool was available when they were written. Worth a look
  before calling it done.
- Changing your password doesn't invalidate existing sessions/tokens
  elsewhere, and there's no re-verification email step.
- `recharts` is a real bundle-size add (build now warns past the
  500KB-chunk threshold) — fine for now, but code-splitting it would be
  the next step if that ever matters.
