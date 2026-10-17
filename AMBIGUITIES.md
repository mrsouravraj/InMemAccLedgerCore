# AMBIGUITIES

Every place the spec is silent or can be read two ways, how I resolved it, and what would change under the other reading.
"Practice" notes are general industry convention offered as support, not rules enforced by the code.

**1. Which date does a fee carry?**
Spec: "booked with value_date equal to the day assessed". "Assessed" is either (a) the day whose closing balance was negative (Day 2), or (b) the day the fee was processed (Day 5 for a Day-2 shortfall).
Resolved: (a). The sentence just before it defines the trigger by "that day's closing ledger balance", so "that day" is the day assessed. Reading (b) would put all three fees on Day 5/6 and make "once per day per account" mean something else.
If (b): fees all land on the processing day, Day 2 and Day 4 show no fee, and criterion 2 ("on Day 2") could never be true.

**2. Which version of a day's closing balance triggers the fee?**
Day 2 closed at 250.00 on Day 2, and became −370.00 on Day 5 once E7 arrived. "All entries with value_date ≤ that day" points at the restated figure, so every end of day re-checks every earlier day. The original printed report is not edited (append-only); the restated table is printed separately.
Practice: back-valued items post to the current open day with an earlier value date; closed days are never reopened, but interest and charges are recomputed from the value date.

**3. Does a day's fee count in its own fee test?**
Spec silent. If it did, the fee would push Day d further negative and keep itself justified, and a later reversal could never release it. Resolved: day d's test excludes day d's own fee; fees of earlier days count.

**4. What happens to fees when E9 reverses E7?**
Spec does not say fees follow. Resolved: symmetric. The same end-of-day re-check removes the cause, so it appends a `FEE_REVERSAL` (same value date) for Days 2, 4, 5. The original `FEE` entries stay. Practice: consequential charges caused by an erroneous or reversed item are normally refunded, and refunds are compensating entries, never deletions.
If fees stand: Day 6 would close at 390.00 (3 × 25.00 left), interest on restated balances would be 0.93, final 390.93.

**5. Is zero overdrawn?**
"Negative" is read literally: `< 0`. An account at exactly 0.00 owes nothing.

**6. E10 is dated Day 5 but appears after E9 (Day 6).**
The spec says replay "in this order", so I do not sort. Resolved: treat as a late arrival: processed on Day 6, `value_date` Day 5 honoured, an audit event records "declared Day 5, processed Day 6". The Day-5 report already printed (ACC-002 = 0.000) is not rewritten; the restated table shows 10.000 on Day 5, and Day 5 earns interest.
Practice: late items are booked on the current business day and back-valued; the closed day is never reopened.

**7. What balance does an authorization test use?**
"Ledger balance minus active holds … after the hold is applied". Resolved: ledger balance as of the **processing day** using every entry written so far (so E7, already booked, counts at E8), minus holds still active, minus the new hold. The first version tested at the authorization's value date; a back-dated hold could then be approved against money already spent (credit 100 on Day 1, debit 100 on Day 2, hold 50 "value Day 1" approved on Day 3, leaving available at −50). A hold takes effect now, so it is tested now. In this stream value date equals processing day for both authorizations, so no number changes. E3: 250.00 − 0 − 200.00 = 50.00 → approved. E8: −155.00 − 0 − 90.00 = −245.00 → declined.
This is why Auth-B is declined in the real stream although it would be approved in a world without E7 (465.00 − 90.00 = 375.00).

**8. Interest base: which balance, computed when?**
Spec: "on the closing ledger balance", capitalized at end of Day 6. Resolved: at the Day-6 close, accrue each day on that day's final restated closing balance (fees and reversals included). One pass, no adjustment entries. The alternative (accrue point-in-time at each close and append corrections when E7/E9 arrive) gives the same total with more code.

**9. Order at the Day-6 close.**
Fee re-check first (reverses the three fees), then accruals, then the single capitalization credit. The capitalization credit is itself excluded from every accrual base.

**10. "The rounded daily accruals must sum exactly to the capitalized total."**
Resolved: capitalized total = Σ(rounded daily accruals). It is never recomputed from unrounded figures. For ACC-001 the unrounded sum is 1.018 (→ 1.02) while the sum of rounded days is 1.03; the spec's rule picks 1.03. The 0.012 "remainder" is not an extra thing to discard; it is already inside the 0.01-grid rounding of each day. (Hence criterion 8 is refused.)

**11. Settlement for a different amount than the hold.**
Auth-A: hold 200.00, settles 185.00. Resolved: post the settled amount, release the whole hold, auth becomes `SETTLED`; any amount is accepted because an approved auth is a commitment. Over-settlement is not tested against any tolerance (cut; see Part 2).
Practice: partial and tip-adjusted settlements are routine.

**12. Settlement with no authorization (E6, Auth-Z).**
Resolved: nothing is posted to the customer; the event is logged as `REJECTED … UNMATCHED` and shows in the day's errors. Funds stay in the account. This matches the spirit of criterion 4, but I refuse the criterion as worded ("any" settlement). See REJECTED.md.

**13. "Posted as three equal instalments" of BHD 10.000.**
Not divisible at 3 dp (3.3333…). "Equal" cannot hold. Resolved: 3.333 + 3.333 + 3.334, residual on the last. All three credits carry the same value date (Day 5).

**14. Hold expiry.**
The spec never ends Auth-B's hold. In my run Auth-B is declined, so no hold exists. If it had been approved it would stay active to the end of the window (no timer, no expiry). Cut; see Part 2 on authorization lifecycle.

**15. What does "reverses E7" do?**
Resolved: appends the opposite entry (+620.00) with value date Day 2 pointing to the original entry; the original stays. It does not undo the Auth-B decline or any printed report. A second reversal of the same event is rejected. Only CREDIT/DEBIT entries are reversible here.

**16. Fee currency for ACC-002.**
The fee is "AED 25.00". For a BHD account there is no rule. Resolved: raise an error if a BHD account ever needs a fee. Never silently convert.

**17. Are debits blocked when available is too low?**
The spec gates authorizations only. Debits (E2, E7), settlements and fees are posted regardless. This is what the known-failing test shows.

**18. Do holds affect interest or fees?**
No. Both use the ledger balance, as the spec says.

**19. "Once per day per account".**
Resolved as at most one **active** fee per value day (fees minus reversals). It does not mean "ever assessed once". If a fee is reversed and the day later turns negative again (a second back-dated debit), a new `FEE` is posted for that day: the journal then holds FEE, FEE_REVERSAL, FEE for the same day, and the customer is charged once. Reasoning: the reversal answered the first shortfall; a later shortfall is a new event, and refusing to charge would let a back-dated item erase a charge the rule says is due. The stricter reading (one fee per day for the account's whole life) would leave the second shortfall uncharged. Pinned by `test_fee_reinstated_when_a_day_goes_negative_again`.

**20. Opening balance entries.**
Both opening balances are 0, so there is no opening entry in the journal.

**21. Reversal of a credit that would overdraw the account.**
Not exercised. The reversal is posted regardless, like any back-office correction; fees then follow from the re-check.

**22. Event ids: redelivery and conflicts.**
The spec labels each event (E1..E10) but never says what a repeated or reused label means. Resolved: an id is consumed on first receipt, whatever its outcome. The same id with the same payload (amounts compared numerically, so "100.0" equals "100.00"; processing day ignored) is a redelivery: logged `DUPLICATE`, no effect. The same id with a different payload is `REJECTED` and logged; the first event stands. A rejected event still consumes its id, so a corrected resend must use a new id. A blank id is a programming error and raises. Only amounts are compared numerically; identifiers (auth ids, account ids, event labels) compare as exact strings, so auth `"01"` and `"1"` are different. If a command raises after its id was taken, the id is released again: no outcome was recorded, so a corrected retry is legal.
Practice: message-driven cores treat the id as an idempotency key so retries cannot double-post. The stream here has no repeats, so printed numbers are unchanged. Limit: the id index lives in memory, so it does not survive a restart (see Part 2).

**23. What counts as a valid input?**
The spec gives no input validation. Resolved: amounts must be finite, positive and at least one minor unit at the account's precision (so 0.004 AED is rejected, not silently stored as 0.00); value date and processing day must be whole days in 1..6; instalment count at least 1; value date no later than processing day. Any failure is a logged `REJECTED` event, not an exception, and posts nothing. Without this, a negative debit credited the account, a negative hold created available balance, and `"NaN"` could become the ledger balance. Unknown accounts and blank event ids are programming errors and raise.

**24. When does an account stop accepting input?**
Capitalization is the end of the window for that account. A `CAPITALIZATION` event is logged (also when the total is 0), and later inputs for that account are rejected, so the accruals can never drift from the capitalized total (the earlier version let a late credit make the accruals 0.48 against 0.24 capitalized). End of day also skips a finalized account: fees freeze together with the interest. This matters because the capitalization credit is value-dated Day 6 and could otherwise turn a negative Day-6 balance positive and un-charge its fee on a rerun.

**25. Is a failed end of day all-or-nothing?**
Resolved: yes. Before posting anything, end of day checks that no account whose currency has no fee tariff is overdrawn, and raises first if one is. A BHD overdraft therefore cannot leave AED fees half-posted.
