# REJECTED

## 1. Acceptance criteria

Verdict per criterion, checked against the rules and against the replay output (`python3 replay.py`), not by intuition.

| # | Criterion | Verdict | Reasoning |
|---|---|---|---|
| 1 | Day 2 closing ledger balance, at end of Day 5 before any fee, is AED −370.00 | **Accepted** | 1,200.00 − 950.00 − 620.00 = −370.00. E7 is back-valued to Day 2 and counts for every day ≥ 2. E3's hold is a memo, not a ledger entry. Test: `test_c1_…`. |
| 2 | E7 causes exactly one overdraft fee, on Day 2 | **Refused** | E7 drives three days negative: Day 2 (−370.00), Day 4 (−180.00 after the Day-2 fee), Day 5 (−205.00 after two fees). Three fees: D2, D4, D5. Day 3 stays positive (30.00 → 5.00 after the Day-2 fee). Not one fee. Test: `test_c2_e7_causes_three_fees_not_one`. |
| 3 | The Day 4 settlement of Auth-A must be accepted | **Accepted** | Auth-A was approved on Day 2 (250.00 − 200.00 = 50.00 ≥ 0). A 185.00 settlement is within the 200.00 hold. Settling less than the hold is normal; the remaining 15.00 is released. |
| 4 | Any settlement referencing an authorization ID not in the ledger must be rejected and the funds must not leave the account | **Refused as worded** (behaviour for E6 is the same) | "Any" is the problem. A settlement with no matching authorization is not always an error: card-scheme clearing can arrive for an offline, stand-in or pre-window authorization, and then the issuer already owes the scheme, so rejecting it silently loses a real obligation. For this stream (E6, Auth-Z) I do not debit the customer, and I do not drop the event: it is logged as `REJECTED … UNMATCHED`, printed under the day's errors, and treated as an exception to be resolved by operations (route to suspense / dispute). So "funds do not leave" holds here; "must be rejected" as a blanket rule does not. |
| 5 | If Auth-B is approved, its hold reduces available balance but not ledger balance | **Accepted, but vacuous in this stream** | True of any approved hold. In the replayed stream Auth-B is **declined** (−155.00 − 90.00 = −245.00 < 0), so the criterion is never exercised. `test_c5_hold_reduces_available_not_ledger` builds the world without E7 to exercise it. |
| 6 | After E9, all balances and fees return to their pre-E7 values | **Refused** | Balances do restate to pre-E7 values (250/250/650/465/465/465). Fees do not "return": three `FEE` entries remain in the journal and three `FEE_REVERSAL` entries net them, because nothing is ever deleted. Auth-B's decline is not undone either: before E7 it would have been approved (465.00 − 90.00 = 375.00), after E9 its recorded outcome is still DECLINED. So the end state nets out, the history does not return. |
| 7 | The three BHD instalments in E10 must each be BHD 3.334 | **Refused** | 3 × 3.334 = 10.002, two fils more than the 10.000 credited. Equal thirds do not exist at 3 dp. Correct: 3.333 + 3.333 + 3.334 = 10.000. |
| 8 | If the rounded daily interest accruals do not sum to the capitalized total, the remainder is discarded | **Refused** | Contradicts the non-negotiable rule: the rounded accruals must sum exactly to the capitalized total. By construction capitalized = Σ rounded daily accruals, so there is never a remainder to discard. ACC-001: Σ rounded = 1.03 = capitalized (the unrounded sum, 1.018, would round to 1.02 and break the rule). |

## 2. Approaches considered and dropped

Honesty note: the first group was dropped while designing, before any of it was coded; I list them because they were real alternatives with real costs. The second group is things that went wrong while building.

**Dropped at design time (never coded)**

1. **Sort the stream by booking day.** It would run E10 on Day 5 and make the Day-5 report right. But the spec says "replayed in this order", and real late items arrive late; sorting hides exactly the late-arrival behaviour worth showing.
2. **Compute everything from the final value-dated view only.** Simple (one fold at the end), but authorization approvals depend on what was known when they arrived (E8 is declined only because E7 was already booked). A final-view-only design would approve Auth-B.
3. **Fee test that includes the day's own fee.** A fee would make its own day negative, so it could never be reversed after E9.
4. **Point-in-time interest accruals with correcting adjustment entries.** More faithful to a production accrual run, but more code and states for the same total. Replaced by one restated pass at the Day-6 close.
5. **Mutable `auth.state` / `account.balance` fields.** Rejected because the spec's append-only rule is easier to defend when no state is ever updated in place: balances, available balance and auth state are derived.
6. **Fee value-dated to the processing day.** See AMBIGUITIES #1.
7. **Capitalize `round(sum of unrounded accruals)`.** Gives 1.02 instead of 1.03 and violates the sum rule.
8. **Charging a BHD fee as AED 25.00 converted or as 25.000 BHD.** Invented number; replaced by raising an error.

**Things that went wrong mid-build**

9. **Test expectation error.** My first `test_no_interest_below_rounding_floor` asserted that 1.249 BHD earns 0.001 per day. 1.249 × 0.0004 = 0.0004996, which rounds to 0.000; the floor is 1.250 BHD. The code was right, my test was wrong. I replaced it with `test_interest_rounding_floors`, which checks both sides of each floor (12.49/12.50 AED, 1.249/1.250 BHD).
10. **Unquantized zero.** The first replay printed `0` instead of `0.000` for ACC-002 because empty sums came back as plain `Decimal(0)`. Fixed by quantizing balance and hold totals to the currency's precision.
11. **Public mutable history lists.** The first version exposed `journal` and `events` as plain lists, so `lg.journal.pop()` worked even though each `Entry` was frozen. "Append-only by construction" was overstated. History now lives in private lists and is exposed as read-only tuples (`test_history_views_are_read_only`).
12. **Auth decline found by string match.** `auth_state` located a declined authorization with `detail.startswith(auth_id + " ")`. Replaced by a structured `ref` field on `Event`.
13. **Future-date guard on only two commands.** NUMBERS.md said future value dates are rejected, but only credit and debit checked. Every command now does (`test_future_value_date_rejected_by_every_command`). A settlement against a declined auth also now says so, instead of "no authorization".
14. **Event-id check only for authorizations.** Version one rejected only a repeated authorization id; a redelivered credit or debit would have posted twice. After comparing with another implementation of the same spec that validated every event id, every command now goes through one `@command` check. The first spelling-sensitive comparison ("100.0" vs "100.00" treated as a conflict) failed its own test and was replaced by numeric canonicalization.
