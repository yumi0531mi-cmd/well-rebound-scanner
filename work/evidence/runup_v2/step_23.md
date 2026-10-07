# Step 23: IMPLEMENTED by Codex
Completed interrupted withdrawal: canonical proposal hash, revalidation, costs/NAV gates, cash caps, earmark release, historical approvals.

User explicitly requested Codex take over implementation after Muse provider rate limits. Existing stages01-22 retained. No strategy threshold changes.
Tests: related storage/ledger/settlement/withdrawal/worker suite 29 passed; new review + worker tests 9 passed (worker5 reused; withdrawal4 new). Ruff checks pass after import-only fixes. Synthetic functional tests, HTTP0, no performance test/deployment/order.
Remaining: inputs fee_estimate_rate/fee_minimum/slippage_estimate/tax_reserve None; all source coverage/live limitations preserved. Worker is disabled by default; source/daily/scan handlers connect in Step25. Blocking network requires per-handler bounded timeout; scheduler budget is cooperative.
API: database connection injected; withdrawal.confirm requires displayed proposal hash, returns (WithdrawalPeriod, reasons). Existing incomplete tests repaired to use actual DTO fields and explicit confirmed zero costs, not relaxed assertions. Migration4 adds job cursor and approval first-seen timestamp.
Files:
- runup/portfolio/withdrawal.py
- runup/portfolio/ledger.py
- runup/storage/migrations.py
- tests/runup_v2/integration/test_step_23_withdrawal.py
- tests/runup_v2/integration/test_step_23_review.py
