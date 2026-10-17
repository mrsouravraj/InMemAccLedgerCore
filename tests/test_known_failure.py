"""KNOWN FAILURE: this test is expected to fail against my own design.

Claim under test: "available balance never goes below zero". The spec's authorization rule says an
auth is approved only if available stays >= 0 after the hold. It is tempting to read that as an
account-level invariant. This test asserts that invariant at every end of day.

It FAILS at Day 5 (available = -230.00). What that reveals:
  * The >= 0 rule is a point-of-authorization guarantee, enforced in Ledger.authorize() only.
  * E7 (a debit back-valued to Day 2, booked Day 5) is not gated by available balance; neither are
    settlements nor fees. They are obligations that already happened, so the ledger just records them.
  * Back-valued entries rewrite the history that earlier approvals relied on: Auth-A was approved
    on Day 2 against 250.00 and would not be approved on the restated Day 2 balance (-370.00).
    Approvals are point-in-time facts, not provable invariants.
  * The design chooses to record reality and let fees/declines respond, not to refuse postings.
    Enforcing the invariant would mean rejecting E7, or holding it in a suspense queue.
Kept failing on purpose; do not "fix" the test.
"""
import unittest
from decimal import Decimal

from replay import run


class AvailableNeverNegative(unittest.TestCase):
    def test_available_is_never_negative_at_any_day_close(self):
        lg, _ = run()
        for d in range(1, 6):
            # Day d close as it was printed: entries booked on or before Day d. available = ledger (value_date <= d) - active holds.
            seen = [e for e in lg.journal if e.account == "ACC-001" and e.booked_day <= d]
            ledger = sum((e.amount for e in seen if e.kind not in ("HOLD", "HOLD_RELEASE") and e.value_day <= d), Decimal(0))
            holds = sum((e.amount if e.kind == "HOLD" else -e.amount for e in seen if e.kind in ("HOLD", "HOLD_RELEASE")), Decimal(0))
            self.assertGreaterEqual(ledger - holds, 0, f"ACC-001 Day {d} available is {ledger - holds}")   # fails on Day 5: -230.00


if __name__ == "__main__":
    unittest.main()
