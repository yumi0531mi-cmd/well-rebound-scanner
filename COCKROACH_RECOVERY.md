# Cockroach recovery and RU reduction — 2026-10-01

## Verified versus unknown

- UPDATE after user login, October1 21:45–21:46KST: account page confirms
  Free Trial expired2026-09-26,0/400USD remaining and cluster Disabled.
  Deletion warning:25days. RU is now15.01thousand/50million; storage489.33MiB
  of10GiB. Thus RU reset did occur, but waiting for it did not restore service.
  Trial expiry is confirmed; do not keep reporting current50M RU exhaustion.

- Render deployment `7d0ba35` succeeded (09:59:06 KST start, 2m21s).
  Public health HTTP200 and UI `0.11.12-oct1-batch` verified by 10:02:39 KST.
- New-process DB connection at 10:00:57 KST returned monthly RU limit error;
  runtime selected local fallback. This process will not automatically restore
  its discarded durable component merely because a remote quota later resets.
- At 15:39 KST the app is still using local fallback. This is cached runtime
  state, NOT a fresh SQL probe of Cockroach at 15:39. Do not claim otherwise.
- Earlier15:35KST check was blocked by login; superseded by the update above.

## Important correction to the old reset assumption

Official docs currently distinguish a one-time $400 trial from monthly Basic
credits. Monthly $15 free resource credit (50M RU and 10GiB equivalent) is
shared across clusters and requires a payment method. Trial expiry without a
payment method leads to a grace period and eventual cluster deletion. A new
calendar month alone does NOT prove this account is restored.

Sources checked October 1:
- https://docs.cockroachlabs.com/docs/cockroachcloud/free-trial
- https://docs.cockroachlabs.com/docs/cockroachcloud/resource-usage-basic
- https://supabase.com/pricing

No payment method, paid plan, resource-limit increase, new account, migration,
or production SQL deletion was performed. Card registration is not an automatic
free fix: charges above credits are possible and forbidden without new authority.

## Local RU optimizations (not deployed)

1. Upsert bars in parameterized batches of at most 250 rows, not one SQL per row.
2. Retain the SAME newest 32,000 bars at each sweep; sweep on first write,
   at least 1h since last sweep, or 1,000 accumulated writes (whichever first).
   Between successful sweeps fewer than 1,000 extra inserted rows can accumulate;
   corrections are counted conservatively. Failed sweeps are not acknowledged.
3. Batched restores use per-symbol indexed DESC/LIMIT subqueries instead of
   ranking every retained historical row. 900 seed/3000 maximum unchanged.

These reduce query frequency/work, not guarantee an RU percentage. Local tests
use synthetic data and fake DB cursors. Actual Cockroach query plans, RU delta,
shared-account load and storage size still need measurement after access returns.
Signal/candidate/access evidence, cost/risk criteria and strategy activation
are unchanged. Do not reduce valid access-snapshot sampling to hide failures.

## Recovery decision and actions

1. User logs into Cockroach console manually. Check billing window, trial state,
   organization-wide free eligibility and cluster disable reason. Do not expose
   credentials or enter payment details; capture only non-secret status/usage.
2. If reset restored access under permitted existing terms: validate connectivity,
   apply verified savings in one authorized deployment/restart, then confirm
   durable initialization. Preserve local evidence before any restart where
   supported; free Render filesystem is not durable. Do not blindly restart.
3. If trial expiry blocks card-free continuation: do not wait another month or
   raise RU limits. Ask support about this account's existing free entitlement;
   no message is sent without approval. Preserve accessible allowed-date data.
4. Supabase is only a conditional Plan B. Its free 500MB database is not a
   drop-in capacity replacement for 10GiB. Inventory table/index bytes first;
   design <=300MB hot DB and separately capacity-budgeted compressed history.
   Its 1GB objects/5GB egress and inactivity pause also need accounting.
   Do not migrate full unfiltered tables: reserved dates/holdout remain forbidden.
5. Adopt a destination only after capacity, retention and 31-day transfer
   forecasts fit, a restore test passes, and user provisions credentials in
   approved platform secrets. Never request DB URLs or keys in chat.

## Acceptance before claiming prevention

Use actual provider metrics including other workloads and background activity.
Measure a representative busy session plus 24h and 3–7-day trends. Keep the
35M RU/7GiB internal targets only when this account's entitlement permits them;
reduce them if other clusters share credits. Missing/stale readings remain UNKNOWN.
Optional-work throttling cannot guarantee uninterrupted external free hosting.
If core workload alone exceeds allowance, report architectural infeasibility;
do not silently disable core scans or label stale data actionable.
