# Oct 1 Deploy Runbook (draft, verify dashboard first)

## October 1 takeover override (read before old steps)

- The batch was already pushed: fresh remote main and local HEAD both
  `7d0ba35cd60ee1b9abce01beedb913cdea18291a`.
- Public health remained HTTP503 at 2026-10-01 00:23:54 KST. The exact deployed
  version and provider quota resets are unverified; source0.11.12 is not proof.
- At 00:39–00:44 KST authenticated Render dashboard confirmed workspace
  suspended, 5.4/5GB bandwidth, 564.97/750 hours, 67/500 pipeline minutes.
  Billing still displayed September. Last successful deploy was ca65d0b.
  Cockroach login expired; current reset/trial entitlement could not be verified.
- The requested 09:00 KST upload was not completed. At 09:56 KST work resumed
  after approval-review usage exhaustion. Recheck actual provider status.
  Extra push exception for today was requested, not yet explicitly granted.
- Do NOT execute the push step below again today. Continue with read-only
  provider deployment/quota checks, then verify health/SHA/UI badge on recovery.
- Keep UptimeRobot paused; include the existing GitHub keep-awake schedule in
  host-hour accounting. Do not change provider settings automatically.
- Use the seven-suite exclusions enumerated in PROGRESS.json for this agent's
  safe regression; the old three-file exclusion below is superseded.
  Latest local safe run: 612 passed, zero failures/errors/skips, 23.257 seconds.
- Old rollback/main references below are historical, not a verified rollback
  target. Never reset remote main to an old SHA as an automatic rollback.

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
