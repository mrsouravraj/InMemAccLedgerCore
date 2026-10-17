import unittest
from unittest import mock
from dataclasses import FrozenInstanceError
from decimal import Decimal as D

from ledger import Ledger, WINDOW, money
from replay import run, ACCOUNTS, A1, A2


def through_day5():
    """Day 1..5 as replayed (E1-E8) with EOD5 run: the state before E9/E10."""
    lg = Ledger(ACCOUNTS)
    lg.credit("E1", A1, "1200.00", 1, 1); lg.debit("E2", A1, "950.00", 1, 1); lg.end_of_day(1)
    lg.authorize("E3", A1, "Auth-A", "200.00", 2, 2); lg.end_of_day(2)
    lg.credit("E4", A1, "400.00", 3, 3); lg.end_of_day(3)
    lg.settle("E5", A1, "Auth-A", "185.00", 4, 4); lg.settle("E6", A1, "Auth-Z", "180.00", 4, 4); lg.end_of_day(4)
    lg.debit("E7", A1, "620.00", 2, 5); lg.authorize("E8", A1, "Auth-B", "90.00", 5, 5); lg.end_of_day(5)
    return lg


def fees(lg, kind="FEE"):
    return [(e.value_day, e.amount) for e in lg.journal if e.kind == kind]


class Criteria(unittest.TestCase):
    def test_c1_day2_balance_before_fee_is_minus_370(self):
        lg = through_day5()
        self.assertEqual(lg.balance(A1, 2, skip=lambda e: e.kind == "FEE"), D("-370.00"))

    def test_c2_e7_causes_three_fees_not_one(self):
        self.assertEqual(fees(through_day5()), [(2, D("-25.00")), (4, D("-25.00")), (5, D("-25.00"))])

    def test_c2_fee_cascade_day3_stays_positive(self):
        lg = through_day5()   # D2 fee drops Day 3 from 30.00 to 5.00: still >= 0, no fee
        self.assertEqual(lg.balance(A1, 3), D("5.00"))
        self.assertFalse(lg.fee_active(A1, 3))

    def test_c3_auth_a_settlement_accepted_and_hold_released(self):
        lg = through_day5()
        self.assertEqual(lg.auth_state("Auth-A"), "SETTLED")
        self.assertEqual(lg.active_holds(A1), D("0.00"))

    def test_c4_unknown_auth_not_debited_and_logged(self):
        lg = through_day5()
        e6 = [e for e in lg.events if e.event == "E6"][0]
        self.assertEqual(e6.outcome, "REJECTED")
        self.assertTrue(all(x.event != "E6" for x in lg.journal))   # nothing posted

    def test_c5_hold_reduces_available_not_ledger(self):
        lg = Ledger(ACCOUNTS)   # world without E7, where Auth-B is approved
        lg.credit("E1", A1, "465.00", 1, 5)
        before = lg.balance(A1, 5)
        self.assertEqual(lg.authorize("E8", A1, "Auth-B", "90.00", 5, 5), "ACCEPTED")
        self.assertEqual(lg.balance(A1, 5), before)
        self.assertEqual(lg.available(A1, 5), before - D("90.00"))

    def test_c5_in_the_real_stream_auth_b_is_declined(self):
        self.assertEqual(through_day5().auth_state("Auth-B"), "DECLINED")

    def test_c6_not_a_return_to_pre_e7_state(self):
        lg, _ = run()
        self.assertEqual([lg.balance(A1, d, skip=lambda e: e.kind == "INTEREST") for d in range(1, 7)],
                         [D(x) for x in ("250.00", "250.00", "650.00", "465.00", "465.00", "465.00")])
        self.assertEqual(len(fees(lg)), 3)               # fees were assessed...
        self.assertEqual(len(fees(lg, "FEE_REVERSAL")), 3)   # ...and netted by compensating entries
        self.assertEqual(lg.auth_state("Auth-B"), "DECLINED")  # decision made on then-known state stays

    def test_c7_instalments_3333_3333_3334(self):
        lg, _ = run()
        parts = [e.amount for e in lg.journal if e.account == A2 and e.kind == "CREDIT"]
        self.assertEqual(parts, [D("3.333"), D("3.333"), D("3.334")])
        self.assertEqual(sum(parts), D("10.000"))

    def test_c8_interest_capitalized_equals_sum_of_rounded(self):
        lg, _ = run()
        for acct in ACCOUNTS:
            total = sum((a for _, a in lg.accruals(acct).values()), D(0))
            cap = sum((e.amount for e in lg.journal if e.account == acct and e.kind == "INTEREST"), D(0))
            self.assertEqual(total, cap)
        self.assertEqual(lg.balance(A1, WINDOW), D("466.03"))
        self.assertEqual(lg.balance(A2, WINDOW), D("10.008"))


class Rules(unittest.TestCase):
    def test_entries_are_immutable(self):
        lg = through_day5()
        with self.assertRaises(FrozenInstanceError):
            lg.journal[0].amount = D(0)

    def test_history_only_grows_and_reversal_keeps_original(self):
        lg = through_day5()
        snapshot = lg.journal
        lg.reverse("E9", A1, "E7", 2, 6); lg.end_of_day(6)
        self.assertEqual(lg.journal[:len(snapshot)], snapshot)
        self.assertGreater(len(lg.journal), len(snapshot))

    def test_history_views_are_read_only(self):
        lg = through_day5()
        for view in (lg.journal, lg.events):
            self.assertFalse(hasattr(view, "append") or hasattr(view, "pop"))
        with self.assertRaises(AttributeError):
            lg.journal = ()

    def test_settlement_of_declined_auth_is_unmatched_with_reason(self):
        lg = through_day5()   # Auth-B was declined: no hold exists, so nothing can settle against it
        self.assertEqual(lg.settle("E11", A1, "Auth-B", "90.00", 5, 5), "REJECTED")
        self.assertIn("was declined", lg.events[-1].detail)
        self.assertEqual(lg.balance(A1, 5), D("-230.00"))

    def test_future_value_date_rejected_by_every_command(self):
        lg = through_day5()
        n = len(lg.journal)
        calls = (lg.credit("X1", A1, "1.00", 6, 5), lg.debit("X2", A1, "1.00", 6, 5),
                 lg.authorize("X3", A1, "Auth-F", "1.00", 6, 5), lg.settle("X4", A1, "Auth-A", "1.00", 6, 5),
                 lg.reverse("X5", A1, "E7", 6, 5), lg.credit_instalments("X6", A2, "3.000", 3, 6, 5))
        self.assertEqual(set(calls), {"REJECTED"})
        self.assertEqual(len(lg.journal), n)   # nothing posted

    def test_redelivered_event_is_ignored_even_on_a_later_day(self):
        lg = Ledger(ACCOUNTS)
        lg.credit("E1", A1, "100.00", 1, 1)
        self.assertEqual(lg.credit("E1", A1, "100.00", 1, 2), "DUPLICATE")
        self.assertEqual(lg.credit("E1", A1, "100.0", 1, 1), "DUPLICATE")   # same amount, different spelling
        self.assertEqual(lg.balance(A1, 2), D("100.00"))
        self.assertEqual(len(lg.journal), 1)
        self.assertEqual([e.outcome for e in lg.events], ["ACCEPTED", "DUPLICATE", "DUPLICATE"])

    def test_conflicting_payload_on_same_event_id_rejected(self):
        lg = Ledger(ACCOUNTS)
        lg.credit("E1", A1, "100.00", 1, 1)
        self.assertEqual(lg.credit("E1", A1, "999.00", 1, 1), "REJECTED")
        self.assertEqual(lg.debit("E1", A1, "100.00", 1, 1), "REJECTED")   # different command, same id
        self.assertEqual(lg.balance(A1, 1), D("100.00"))

    def test_rejected_event_still_consumes_its_id(self):
        lg = Ledger(ACCOUNTS)
        self.assertEqual(lg.credit("E1", A1, "5.00", 3, 1), "REJECTED")   # future-dated
        self.assertEqual(lg.credit("E1", A1, "5.00", 1, 1), "REJECTED")   # corrected resend needs a new id
        self.assertEqual(lg.credit("E1b", A1, "5.00", 1, 1), "ACCEPTED")

    def test_blank_event_id_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            Ledger(ACCOUNTS).credit("  ", A1, "1.00", 1, 1)

    def test_double_reversal_rejected(self):
        lg = through_day5()
        self.assertEqual(lg.reverse("E9", A1, "E7", 2, 6), "ACCEPTED")
        self.assertEqual(lg.reverse("E9b", A1, "E7", 2, 6), "REJECTED")

    def test_zero_balance_is_not_overdrawn(self):
        lg = Ledger(ACCOUNTS); lg.credit("E1", A1, "10.00", 1, 1); lg.debit("E2", A1, "10.00", 1, 1)
        self.assertEqual(lg.end_of_day(1), [])

    def test_bhd_overdraft_has_no_defined_fee(self):
        lg = Ledger(ACCOUNTS); lg.debit("E1", A2, "1.000", 1, 1)
        with self.assertRaises(ValueError):
            lg.end_of_day(1)

    def test_interest_rounding_floors(self):
        # smallest balance that earns a non-zero daily accrual: 12.50 AED (0.005 -> 0.01), 1.250 BHD (0.0005 -> 0.001)
        for acct, below, at, got in ((A1, "12.49", "12.50", "0.01"), (A2, "1.249", "1.250", "0.001")):
            lg = Ledger(ACCOUNTS); lg.credit("E1", acct, below, 1, 1)
            self.assertEqual(lg.accruals(acct, 1)[1][1], D(0))
            lg = Ledger(ACCOUNTS); lg.credit("E1", acct, at, 1, 1)
            self.assertEqual(lg.accruals(acct, 1)[1][1], D(got))


class Hardening(unittest.TestCase):
    """Gaps found in review of the first push: each test reproduces one."""

    def test_backdated_hold_cannot_spend_money_already_gone(self):
        lg = Ledger(ACCOUNTS)
        lg.credit("a", A1, "100.00", 1, 1); lg.debit("b", A1, "100.00", 2, 2)
        self.assertEqual(lg.authorize("c", A1, "X", "50.00", 1, 3), "DECLINED")   # value Day 1, but 0.00 is left on Day 3
        self.assertEqual(lg.active_holds(A1), D("0.00"))

    def test_invalid_amounts_are_rejected_and_change_nothing(self):
        lg = Ledger(ACCOUNTS)
        bad = ("-100", "0", "NaN", "Infinity", "abc", "0.004", "1e40", None)   # 0.004 AED rounds to 0.00
        for i, amt in enumerate(bad):
            for cmd in (lg.credit, lg.debit):
                self.assertEqual(cmd(f"c{i}{cmd.__name__}", A1, amt, 1, 1), "REJECTED", (cmd.__name__, amt))
            self.assertEqual(lg.authorize(f"a{i}", A1, f"H{i}", amt, 1, 1), "REJECTED", amt)
            self.assertEqual(lg.settle(f"s{i}", A1, "H", amt, 1, 1), "REJECTED", amt)
        self.assertEqual((len(lg.journal), lg.balance(A1, 1), lg.available(A1, 1)), (0, D("0.00"), D("0.00")))
        self.assertEqual(lg.authorize("ok", A1, "Hneg", "-100", 1, 1), "REJECTED")   # negative hold must not create funds
        self.assertEqual(lg.authorize("ok2", A1, "Hpos", "1.00", 1, 1), "DECLINED")  # so a positive hold still has no funds

    def test_float_amounts_are_rejected_not_silently_misrounded(self):
        lg = Ledger(ACCOUNTS)   # 2.675 as a float is 2.67499..., as a string it is 2.675 -> 2.68 under HALF_UP
        for cmd in (lg.credit, lg.debit):
            self.assertEqual(cmd(f"f{cmd.__name__}", A1, 2.675, 1, 1), "REJECTED")
        self.assertEqual(lg.authorize("fa", A1, "H", 2.675, 1, 1), "REJECTED")
        self.assertEqual(lg.credit_instalments("fi", A2, 10.0, 3, 1, 1), "REJECTED")
        self.assertEqual(len(lg.journal), 0)
        self.assertEqual(lg.credit("s", A1, "2.675", 1, 1), "ACCEPTED")
        self.assertEqual(lg.balance(A1, 1), D("2.68"))
        with self.assertRaises(TypeError):
            money(2.675, "AED")

    def test_days_and_counts_are_bounded(self):
        lg = Ledger(ACCOUNTS)
        self.assertEqual(lg.credit("a", A1, "1.00", 0, 1), "REJECTED")     # value day 0
        self.assertEqual(lg.credit("b", A1, "1.00", 1, 7), "REJECTED")     # beyond the window
        self.assertEqual(lg.credit("c", A1, "1.00", "1", 1), "REJECTED")   # not a whole number
        self.assertEqual(lg.credit_instalments("d", A2, "3.000", 0, 1, 1), "REJECTED")
        self.assertEqual(lg.credit_instalments("e", A2, "3.000", True, 1, 1), "REJECTED")
        self.assertEqual(len(lg.journal), 0)

    def test_capitalization_closes_the_window(self):
        lg = Ledger(ACCOUNTS)
        lg.credit("a", A1, "100.00", 1, 1)
        self.assertIsNotNone(lg.capitalize_interest(A1, 6))
        self.assertEqual(lg.credit("b", A1, "100.00", 1, 6), "REJECTED")   # would have made accruals 0.48 vs 0.24 capitalized
        self.assertIsNone(lg.capitalize_interest(A1, 6))
        total = sum((a for _, a in lg.accruals(A1).values()), D(0))
        self.assertEqual(total, sum((e.amount for e in lg.journal if e.kind == "INTEREST"), D(0)))
        self.assertEqual(lg.end_of_day(6), [])   # fees are frozen with the interest

    def test_zero_interest_still_finalizes(self):
        lg = Ledger(ACCOUNTS)
        self.assertIsNone(lg.capitalize_interest(A2, 6))
        self.assertEqual(lg.credit("a", A2, "5.000", 1, 6), "REJECTED")

    def test_fee_reinstated_when_a_day_goes_negative_again(self):
        # Policy (AMBIGUITIES #19): at most one ACTIVE fee per day. A day that turns negative again after a refund is a new shortfall.
        lg = Ledger(ACCOUNTS)
        lg.debit("a", A1, "10.00", 1, 1); lg.end_of_day(1)
        lg.credit("b", A1, "10.00", 1, 2); lg.end_of_day(2)
        lg.debit("c", A1, "10.00", 1, 3); lg.end_of_day(3)
        self.assertEqual([(e.kind, e.value_day) for e in lg.journal if e.kind.startswith("FEE") and e.value_day == 1],
                         [("FEE", 1), ("FEE_REVERSAL", 1), ("FEE", 1)])   # Days 2 and 3 cascade separately
        self.assertTrue(lg.fee_active(A1, 1))

    def test_identifiers_compare_exactly_but_amounts_numerically(self):
        lg = Ledger(ACCOUNTS)
        lg.credit("z", A1, "500.00", 1, 1)
        self.assertEqual(lg.authorize("e", A1, "01", "1.00", 1, 1), "ACCEPTED")
        self.assertEqual(lg.authorize("e", A1, "1", "1.00", 1, 1), "REJECTED")      # different id string: conflict
        self.assertEqual(lg.authorize("e", A1, "01", "1.0", 1, 1), "DUPLICATE")     # same id, same amount
        self.assertEqual(lg.authorize("e2", A1, "1", "1.00", 1, 1), "ACCEPTED")     # "1" really is a different auth

    def test_end_of_day_is_all_or_nothing(self):
        lg = Ledger(ACCOUNTS)
        lg.debit("a", A1, "10.00", 1, 1); lg.debit("b", A2, "1.000", 1, 1)
        with self.assertRaises(ValueError):
            lg.end_of_day(1)
        self.assertEqual([e for e in lg.journal if e.kind == "FEE"], [])   # no AED fee left behind

    def test_failed_command_releases_its_event_id(self):
        lg = Ledger(ACCOUNTS)
        with mock.patch.object(lg, "_post", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                lg.credit("a", A1, "1.00", 1, 1)
        self.assertEqual(lg.credit("a", A1, "1.00", 1, 1), "ACCEPTED")   # not DUPLICATE, not consumed
        self.assertEqual(lg.credit("bad", A1, "abc", 1, 1), "REJECTED")  # a logged rejection DOES consume its id
        self.assertEqual(lg.events[-1].outcome, "REJECTED")


if __name__ == "__main__":
    unittest.main()
