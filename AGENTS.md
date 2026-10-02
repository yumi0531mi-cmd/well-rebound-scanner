# Agent Safety Rules

These rules apply to every AI agent working in this repository.

## 0. Precedence

- The human's 2026-09-23 standing approval ("approve all") remains in force
  for scanner-completion work, except for the protections in sections 1-2,
  which are absolute and can never be pre-approved.
- Paid actions are never approved. Forbidden data
  (2026-08-17 through 2026-08-19, holdout-candidates) is never approved.

## 1. No secret leaks

- Never write API keys, secrets, tokens, passwords, or database URLs into
  source code, tests, logs, reports, progress files, commit messages, or chat
  output. This includes `KIS_APP_KEY`, `KIS_APP_SECRET`, `DATABASE_URL`,
  `WELLSCAN_ADMIN_TOKEN`, and any session or approval tokens.
- Secrets live only in environment variables or the deployment platform's
  secret manager (e.g. Render environment variables). Refer to them by name
  only, and confirm presence/absence — never print values.
- When a secret is required (e.g. pasting a database URL into Render), the
  human performs that step manually. The agent must not ask for secret
  values in chat.

## 2. Protect `.env` files

- Never create, modify, commit, display, or transmit the contents of any
  `.env` file or any file containing secrets.
- If a task requires reading a `.env` file, stop and ask the human first.
- `.env` and equivalent secret files must remain untracked by git. Verify
  with `git status` before staging anything nearby.

## 3. Manual approval before destructive commands

Except for pushes of verified changes covered by the standing approval
above, the agent must obtain explicit manual approval from the human BEFORE
running any of the following:

- `git push` (including to `origin/main`)
- Deleting files or directories (`rm`, `Remove-Item`, `git rm`, clean-ups)
- `git reset`, `git checkout -- <paths>`, `git restore`, force-pushes,
  history rewrites, or reverting the human's changes
- Changing environment variables, secrets, or deployment settings
  (Render, database consoles, monitoring services)
- Any action that is hard to undo (drops, overwrites of shared state,
  bulk data deletion)

Routine read-only work (reading files, searching code, running tests,
static checks) does not need approval. When in doubt whether an action is
destructive, treat it as destructive and ask.

## 4. Cross-market time awareness

- Never infer the whole scanner's operating state from the market currently
  selected in the web UI. The selected tab is a view, not the daemon state.
- Before saying that the market or scanner is closed, evaluate both KR and US
  session status from the same timezone-aware instant. If either enabled
  market session is active, identify that active session explicitly.
- Operational reports must show or reason from KST and New York local time;
  keep UTC for machine timestamps. Account for DST and exchange holidays via
  `wellscan.sessions.session_status`, never by fixed-hour mental arithmetic.

## 5. Shared deployment discipline

- The one-push-per-KST-calendar-day limit applies to every AI and every local
  thread working on this repository. Bundle verified commits into that push.
- Do not push or redeploy during a live baseline test. Render restarts erase
  the free-instance local minute cache while the durable store is unavailable.
- Before reporting remote state, run `git ls-remote origin refs/heads/main`;
  local `HEAD` or `origin/main` without a fresh fetch is not remote evidence.
- A version in source is a candidate version. Mark it deployed in
  `PROGRESS.json` only after the remote SHA, Render health 200, and the exact
  UI version badge have all been verified. Never infer deployment from a
  commit or version bump alone.

## 6. User-approved access-count contract (2026-10-02)

- The user explicitly replaced five actionable symbols with a minimum of TWO
  and a target of THREE at each active-session access time; default live display
  is THREE. This supersedes old five-symbol requirements, not old measurements.
- Keep the full discovery universe and all 22 strategy evaluations. Observations
  and ENTRY_WAIT do not qualify as immediately actionable entries. Record both.
- Preserve T1 goal80%/floor70%, minimum50 fills per market, costs/stops and all
  evidence rules. Do not pad a shortage or retroactively relabel old results.
- Daily entry totals are diagnostic only; count qualification is per access time.

## 7. User-approved strategy expansion (2026-10-02)

- Latest approval supersedes the old six-only activation hold: enable the 19
  non-opening-window strategies, preserve the three narrow-time implementations
  as diagnostics and the three separate scalp profiles as shadow-only.
- Preserve all costs, stops, causal completed-frame and fresh-quote checks.
  Activation is permission to detect setups, NOT statistical qualification.
- One symbol with several matched strategies remains one symbol/selected plan.
- Default compact quote UI and REST fallback request cadence are one second;
  WS is preferred. Report received age/delay honestly; no latency guarantee.
- Trace each symbol's 22 strategies through readiness/formation/cost/fill-band/
  selection/final publication. Do not force passes or repeat frozen backtests.

## 8. US penny and surge views (2026-10-02)

- User reiterated US penny/surge coverage. Default minimum display price is
  USD0.01 in all US views; dedicated penny view is below USD1. US surge view
  means gain above7%, with no20% upper display cap. Keep the KR view unchanged.
- These are overlapping views of the same observed KIS candidate feed, not
  duplicate scans and not a claim that every listed US stock is discovered.
- Sub-dollar prices retain four decimals in UI. Entry/cost/risk checks stay
  unchanged; a penny/surge candidate is not automatically an actionable entry.
