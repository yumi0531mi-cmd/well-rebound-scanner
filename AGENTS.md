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
