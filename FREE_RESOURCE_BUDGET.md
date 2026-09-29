# Free-resource budget and continuity policy

## Non-negotiable limitation

Free quotas are provider-enforced hard limits, not uptime guarantees. The
scanner cannot keep a Render workspace running after Render suspends it, or
force CockroachDB to serve reads after its RU/trial limit. A free-only design
can reduce the chance of suspension and degrade gracefully while the host is
available; it cannot promise uninterrupted operation after a provider cutoff.
Do not add a card, upgrade a plan, or create paid resources as an automatic
response.

## Monthly ceilings and internal budgets

| Resource | Provider ceiling | Internal target | When approaching target |
| --- | ---: | ---: | --- |
| Render outbound bandwidth, workspace-wide | 5 GB/month on Hobby | 3 GB/month (60%, about 100 MB/day over 30 days) | Reduce browser polling and nonessential KIS/history warmup; retain core evaluation and label stale data honestly. |
| Render free instance hours, workspace-wide | 750 hours/month | 600 hours/month | Keep the scanner health monitor paused; use only the bounded weekday/market warm schedule. |
| CockroachDB Basic compute | 50 million RU/month | 35 million RU/month | Prefer local cache, batch durable operations, defer nonessential history/diagnostic writes; do not assume query count equals RU. |
| CockroachDB Basic storage | 10 GiB | 7 GiB | Measure table growth and retention before changing or deleting data. |
| Render pipeline minutes | 500 minutes/month shown in this workspace | 350 minutes/month | Bundle verified changes; at most one planned push per KST day. |

## Pacing formula and automatic response tiers

For each monthly-reset resource, let `U` be the provider-reported usage in the
current billing window, `L` its hard ceiling, `T` the internal target above,
`d` the number of elapsed billing-window days, and `r = U / d` the measured
average daily rate. With `D` days remaining:

```text
month-end forecast = U + r * D
safe daily allowance = max(0, (T - U) / D)
```

Use the provider's reset window, not an assumed calendar month. If the
forecast exceeds `T`, act immediately even if today's usage is below a tier.
Without a provider reading, mark the forecast `UNKNOWN`; never substitute
application request counts for billed usage.

| Level | Trigger as fraction of internal target | Response |
| --- | ---: | --- |
| Observe | 50% | Record actual usage, elapsed days, days remaining, and projection. |
| Slow | 70% or forecast > target | Increase optional UI quote refresh to 60s; stop nonessential backfill/warmup first. |
| Shed | 90% | Pause admin/backtest and optional diagnostic work; keep official scan path, strategy-specific completed-frame requirements, and data freshness. |
| Internal ceiling | 100% | Stop optional use. For Cockroach, open durable-store circuit and continue only from local cache; for Render, keep core only while the hard-limit projection remains below 5 GB. |

For a 30-day billing window, the internal targets imply average pacing of
100 MB/day Render egress, 20 instance-hours/day, about 1.17 million Cockroach
RU/day, and about 11.7 build-pipeline minutes/day. These are pacing averages,
not entitlements that roll over. Storage is cumulative, so compare it directly
with its 7 GiB internal target instead of applying a monthly daily allowance.

If the measured core-only run rate itself projects beyond a provider ceiling,
there is no threshold trick that can guarantee both uninterrupted service and
zero overage on that free host. The choices are a separately authorized
failover host or a visible outage; do not silently weaken scanner qualification
or claim a free-only uptime guarantee.

The Render keep-awake schedule currently requests roughly 22 weekday hours
plus 30 minutes before each Korean weekday open: about 480 instance hours in a
typical 30-day month before manual visits or other workspace services. This is
an estimate, not observed usage. A five-minute uptime monitor could keep a
service awake for roughly 720 hours in a 30-day month by itself, so it must stay
paused unless the actual shared-hour budget is re-evaluated.

## Implemented locally (not yet deployed)

- Diagnostic JSON and large auxiliary tables stay behind opt-in controls.
- The scanner daemon does no KIS/DB work when all supported sessions are closed.
- Ranking discovery is reused for 180 seconds; cached bars are reused when no
  completed minute is new; nonessential history warmup yields over its soft
  KIS-call budget.
- KIS request attempts and response-body bytes are counted by market/session.
  Response-body bytes are not Render's outbound-bandwidth meter and must not
  be used to infer it; the provider's service-initiated total also covers
  network traffic beyond these measured KIS payloads.
- Browser status polling is now 30 seconds; displayed live-price refresh
  choices are 30/60/120 seconds, defaulting to 30. The scanner engine cadence
  remains 60 seconds; these UI settings do not slow its official evaluation.
- Durable-store failures use the local fallback. That fallback does not
  survive Render instance replacement/restart.
- Cold candidates receive background 900-bar structural seeds even before a
  setup forms; otherwise history-dependent strategies could never become
  ready. Only formed setups request deeper history up to 3000 bars. Both use
  one shared worker and a combined four-job queue, deduplicated by structural
  symbol/session, under the existing soft KIS-call budget. This bounds pending
  work, not provider-billed usage, and still requires live quota measurement.

## Monitoring gap and safe operation

The app currently has no authenticated provider-usage feed. Render documents a
read-only bandwidth metrics API (`GET /v1/metrics/bandwidth`) that requires an
API key; its dashboard graph is hourly and delayed, so any controller needs a
reserve and must start with read-only alerts before attempting dynamic
throttling. Internal KIS response-body counters cannot substitute for Render
billed bandwidth. A supported Cockroach Cloud API field for current Basic-plan
RU consumption was not verified in this audit; the signed-in cluster overview
is the authoritative RU reading until that is confirmed. Do not infer RU from
SQL call count. Until exact provider readings are available to a controller,
check both dashboards daily after recovery, record date/percentage outside
this public repository, and compare the measured run rate with days remaining.
Never store account-specific usage, cluster IDs, or credentials here.

Do not confuse free monthly Basic quotas with free-trial credits: the account
dashboard's trial-credit balance is not a monthly $10 allowance and does not
reset with the Basic RU/storage monthly quota.

At any warning threshold, cut optional work first: diagnostic payloads,
unneeded live-quote refreshes, and background warmups. Retain available history
up to 3000 bars, using the 900-bar seed and formed-only deep warmup policy;
preserve strategy-specific timeframe requirements and cost/quality rules. If fresh market data is
unavailable, show a non-actionable/stale state rather than a false ENTRY. If a
provider suspends the service or database, report the outage; do not represent
local fallback as durable cross-device continuity.

## Account-specific state checked 2026-09-28

- Render Billing showed the workspace suspended for exceeding its included
  outbound bandwidth; free instance hours were still below their ceiling.
- Cockroach Cloud showed the cluster disabled at its RU allowance and a free
  trial expiration notice; storage was well below its displayed limit. The
  account-specific measurements and cluster identifier are intentionally not
  copied into this public repository.
- No billing method, paid plan, provider setting, secret, or deployment was
  changed during this audit.

## Required verification after provider recovery

1. Keep the five-minute uptime monitor paused and deploy only after the owner
   manually restores the free services.
2. Verify health and exact deployed SHA/version; then measure a full active
   market day of Render outbound GB, instance-hours, Cockroach RUs/storage,
   KIS calls/body bytes, and scan funnel.
3. Project the observed daily rates to the next reset. If the 3 GB / 600 hour /
   35 million RU targets are exceeded, reduce optional workload and measure
   again before leaving the service unattended.
4. Do not claim “quota-proof” or uninterrupted free operation without measured
   headroom and an independent, permitted failover host.
