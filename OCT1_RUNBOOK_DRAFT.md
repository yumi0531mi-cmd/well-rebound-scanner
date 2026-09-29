# Oct 1 Deploy Runbook (draft, verify dashboard first)

## Preconditions (all must be true)
- [ ] Render dashboard: quota reset confirmed (bandwidth < limit), service state known
- [ ] CockroachDB dashboard: RU reset confirmed, cluster enabled
- [ ] Working tree: `git status` clean except approved files; full suite green on the exact commit
- [ ] Baseline freeze in effect: no code push until this runbook executes

## Steps
1. `git log --oneline -3` + `git status --short` — record HEAD SHA.
2. Full suite (minus 3 forbidden): must be 0 failures. Record count.
3. `git push origin HEAD:main` (ONE push; KST 1/day rule).
4. Wait for Render build success (dashboard Deploys tab).
5. Verify: `/_stcore/health` HTTP 200, UI version badge == pushed APP_VERSION, origin/main SHA == pushed SHA.
6. KIS warmup: allow sessions to accumulate; do NOT claim entries until funnel shows evaluated > 0.
7. 24h bandwidth measurement, then 3–7 day projection. UptimeRobot stays OFF until projection ≤ 4GB/mo.

## Rollback
- `git push origin <last-good-SHA>:main` only with explicit approval (destructive per AGENTS.md).
- Last good remote: ca65d0b (0.11.8) unless superseded by a verified deploy.
