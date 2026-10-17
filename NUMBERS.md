# NUMBERS

Every constant in the code, where it lives, and why that value and not half of it.
"Half" results below were produced by re-running `replay.py` with the constant changed
(`OVERDRAFT_FEE` / `DAILY_RATE` patched in a scratch script), not estimated.

## A. Given by the spec (not my choice)

| Constant | Value | In code | At half the value |
|---|---|---|---|
| Overdraft fee | AED 25.00, once per day per account | `OVERDRAFT_FEE` | AED 12.50: same 3 fee days (D2, D4, D5), because fee eligibility depends on the sign of the base balance, not on the fee size. Day-5 close moves −230.00 → −192.50. Final balances unchanged (fees are reversed on Day 6). The fee size only changes the cascade depth, e.g. a large enough fee could push Day 3 (5.00 after the Day-2 fee) negative. At 25.00 it does not. Day 3 would turn negative at a fee of 30.01 or more (30.00 base before the Day-2 fee). |
| Daily interest | 0.04% = `0.0004` | `DAILY_RATE` | 0.0002: total accrual 0.50 (vs 1.03) and the final AED balance is 465.50. BHD 0.004 (vs 0.008). Rounding gets worse as the rate shrinks, since daily accruals are smaller relative to the 0.01 grid. |
| AED precision | 2 dp | `PRECISION["AED"]` | Half = 1 dp would break fils: AED 185.00 would be fine but 0.10 interest on 250.00 could not be represented exactly in a consistent way. 2 is ISO 4217. |
| BHD precision | 3 dp | `PRECISION["BHD"]` | Half would be 1.5, nonsense. 3 is ISO 4217 (fils = 1/1000 dinar). This is why 0.004 BHD interest exists and why 10.000 / 3 needs a residual rule. |
| Window | 6 days | `WINDOW` | Capitalization at end of Day 6 is defined by the spec. At half (3 days) E9 and E10 would fall outside it. |
| Opening balances | 0.00 AED, 0.000 BHD | `Ledger.__init__` (no opening entries) | n/a |

## B. Chosen by me

| Choice | Value | Why this and not the alternative |
|---|---|---|
| Rounding mode | `ROUND_HALF_UP` | Conventional for customer-facing interest. Banker's rounding (HALF_EVEN) is unbiased over millions of accruals but harder to explain for one account. No exact tie occurs in this data set, so the choice changes no printed number. Ties start to matter at balances like 12.50 AED (0.005). |
| Negative test | strictly `< 0` | A zero closing balance is not overdrawn. `<= 0` would fee an empty account every day (E1/E2 style netting to 0 would trigger it). `tests/test_ledger.py::test_zero_balance_is_not_overdrawn`. |
| Fee base | day-d balance **excluding day d's own fee** | Including it creates a loop: the fee makes the day more negative, which justifies the fee, and it could never be reversed. Earlier days' fees are included (they are real money by day d). |
| Fee re-evaluation | every EOD, every day ≤ today, ascending | Back-valued entries (E7, E9) change closed days. Ascending, because an earlier day's fee is part of every later day's base. |
| Remainder on instalments | on the **last** one: 3.333 / 3.333 / 3.334 | Sums to exactly 10.000 and earlier instalments are predictable. First-instalment residual (3.334/3.333/3.333) would also sum correctly, but a schedule that "trues up" at the end is how instalment/EMI schedules are normally built. |
| Instalment base | split rounded **down** to 3 dp, residual = total − base×(n−1) | Rounding half-up on 3.3333… gives 3.333 each = 9.999, one fils short. Rounding down then trueing up on the last is exact for any n. |
| Interest base | final restated closing balance of each day, interest entries excluded | Interest capitalizes once at Day 6. Using what we know at Day 6 for every day means no adjustment entries are needed for back-valued items (E7, E9). Excluding the capitalization credit avoids interest on interest inside the same window. |
| Daily accrual rounding | each day rounded to the currency's precision, then summed | The spec requires "the rounded daily accruals must sum exactly to the capitalized total". Rounding the unrounded sum would give **1.02** for ACC-001 (0.1+0.1+0.26+3×0.186 = 1.018) against **1.03** from summing rounded days. That 0.01 gap is why capitalizing round(sum) breaks the rule. |
| Smallest balance that earns interest | 12.50 AED / 1.250 BHD per day | 12.50 × 0.0004 = 0.005 → 0.01 under HALF_UP; 12.49 gives 0.004996 → 0.00. BHD: 1.250 × 0.0004 = 0.0005 → 0.001. Derived, not chosen, but worth knowing: balances under that earn nothing, and the "remainder" is simply not accrued daily. Tested in `test_interest_rounding_floors`. |
| Fee currency | AED only; BHD overdraft raises `ValueError` | The spec gives no BHD fee. Converting 25 AED at a guessed FX rate, or silently using 25.000 BHD (~USD 66), would be an invented number. ACC-002 never goes negative here. |
| Hold amount released at settlement | the whole hold, not just the settled amount | See AMBIGUITIES #11. Auth-A: hold 200.00, settles 185.00, 15.00 is released with it. |
| Future value dates | rejected (value_date > processing day) | Not in the stream and not defined by the spec; rejecting avoids inventing forward-balance semantics. |
| Journal ids | `J1, J2, …` in append order | Gives every figure a handle for the live walk-through. |
