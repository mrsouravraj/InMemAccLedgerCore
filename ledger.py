"""In-memory account ledger core.

Event-sourced: two append-only lists (`events`, `journal`) are the only stored
state. Balances, available balance, auth state and active fees are all derived
from them on demand. No I/O and no clock here: the caller passes the business
`day` a command is processed on (see replay.py).
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

# --- constants (see NUMBERS.md) -------------------------------------------
PRECISION = {"AED": 2, "BHD": 3}          # minor units per currency
ROUNDING = ROUND_HALF_UP
OVERDRAFT_FEE = {"AED": Decimal("25.00")}  # no BHD fee defined -> raise, never default
DAILY_RATE = Decimal("0.0004")             # 0.04% per day
WINDOW = 6                                 # Day 1 .. Day 6

MEMO = {"HOLD", "HOLD_RELEASE"}            # affect available balance, not ledger balance


def money(x, ccy):
    """Quantize to the currency's own precision."""
    return Decimal(x).quantize(Decimal(1).scaleb(-PRECISION[ccy]), rounding=ROUNDING)


@dataclass(frozen=True)
class Entry:
    """Journal record. Signed amount; memo kinds (HOLD/HOLD_RELEASE) carry a positive amount."""
    id: str
    event: str        # input event label (E1..E10) or EODn for system postings
    account: str
    kind: str         # CREDIT DEBIT SETTLEMENT REVERSAL FEE FEE_REVERSAL INTEREST HOLD HOLD_RELEASE
    amount: Decimal
    value_day: int
    booked_day: int   # business day the record was written (processing day)
    ref: str = ""     # auth id, reversed entry id, or fee day


@dataclass(frozen=True)
class Event:
    """Every input event with its outcome, including rejected ones."""
    seq: int
    event: str
    kind: str
    account: str
    day: int          # processing day
    outcome: str      # ACCEPTED | REJECTED | DECLINED
    detail: str = ""


class Ledger:
    def __init__(self, accounts):
        self.accounts = dict(accounts)   # id -> currency; opening balances are all zero
        self.events: list[Event] = []
        self.journal: list[Entry] = []

    # ---- append-only primitives -------------------------------------------
    def _post(self, ev, acct, kind, amount, vd, day, ref=""):
        e = Entry(f"J{len(self.journal) + 1}", ev, acct, kind,
                  money(amount, self.accounts[acct]), vd, day, ref)
        self.journal.append(e)
        return e

    def _log(self, ev, kind, acct, day, outcome, detail=""):
        self.events.append(Event(len(self.events) + 1, ev, kind, acct, day, outcome, detail))
        return outcome

    # ---- derived state ----------------------------------------------------
    def balance(self, acct, day, skip=lambda e: False):
        """Ledger balance: all non-memo entries with value_date <= day."""
        return money(sum((e.amount for e in self.journal
                          if e.account == acct and e.kind not in MEMO
                          and e.value_day <= day and not skip(e)), Decimal(0)), self.accounts[acct])

    def active_holds(self, acct):
        return money(sum((e.amount if e.kind == "HOLD" else -e.amount for e in self.journal
                          if e.account == acct and e.kind in MEMO), Decimal(0)), self.accounts[acct])

    def available(self, acct, day):
        return self.balance(acct, day) - self.active_holds(acct)

    def auth_state(self, auth_id):
        if any(e.kind == "AUTHORIZATION" and e.detail.startswith(auth_id + " ") and e.outcome == "DECLINED"
               for e in self.events):
            return "DECLINED"
        refs = {e.kind for e in self.journal if e.ref == auth_id}
        if "SETTLEMENT" in refs:
            return "SETTLED"
        return "APPROVED" if "HOLD" in refs else "UNKNOWN"

    def fee_active(self, acct, d):
        return sum((e.amount for e in self.journal if e.account == acct
                    and e.kind in ("FEE", "FEE_REVERSAL") and e.value_day == d), Decimal(0)) != 0

    # ---- commands: validate, then append ----------------------------------
    def late_arrival(self, ev, acct, declared, day):
        """Audit note: event declared on a closed day, processed on `day`. Closed days are never reopened."""
        return self._log(ev, "LATE_ARRIVAL", acct, day, "ACCEPTED", f"declared Day {declared}, processed Day {day}")

    def _check(self, ev, kind, acct, vd, day, detail):
        if vd > day:   # future-dated value is out of scope
            return self._log(ev, kind, acct, day, "REJECTED", f"{detail}future value_date")
        return None

    def credit(self, ev, acct, amount, vd, day):
        return self._simple(ev, "CREDIT", acct, money(amount, self.accounts[acct]), vd, day)

    def debit(self, ev, acct, amount, vd, day):
        return self._simple(ev, "DEBIT", acct, -money(amount, self.accounts[acct]), vd, day)

    def _simple(self, ev, kind, acct, signed, vd, day):
        bad = self._check(ev, kind, acct, vd, day, "")
        if bad:
            return bad
        self._post(ev, acct, kind, signed, vd, day)
        return self._log(ev, kind, acct, day, "ACCEPTED", f"{signed}")

    def credit_instalments(self, ev, acct, total, n, vd, day):
        """Equal split rounded down; the residual goes on the last instalment."""
        ccy = self.accounts[acct]
        total = money(total, ccy)
        base = (total / n).quantize(Decimal(1).scaleb(-PRECISION[ccy]), rounding=ROUND_DOWN)
        parts = [base] * (n - 1) + [total - base * (n - 1)]
        for i, p in enumerate(parts, 1):
            self._post(ev, acct, "CREDIT", p, vd, day, ref=f"{ev}.{i}")
        return self._log(ev, "CREDIT_INSTALMENTS", acct, day, "ACCEPTED", " + ".join(map(str, parts)))

    def authorize(self, ev, acct, auth_id, amount, vd, day):
        amount = money(amount, self.accounts[acct])
        detail = f"{auth_id} hold {amount}"
        if self.auth_state(auth_id) != "UNKNOWN":
            return self._log(ev, "AUTHORIZATION", acct, day, "REJECTED", f"{auth_id} duplicate id")
        after = self.available(acct, vd) - amount
        if after < 0:   # approved only if available stays >= 0 after the hold
            return self._log(ev, "AUTHORIZATION", acct, day, "DECLINED",
                             f"{detail}; available after hold would be {after}")
        self._post(ev, acct, "HOLD", amount, vd, day, ref=auth_id)
        return self._log(ev, "AUTHORIZATION", acct, day, "ACCEPTED",
                         f"{detail}; available after hold {after}")

    def settle(self, ev, acct, auth_id, amount, vd, day):
        amount = money(amount, self.accounts[acct])
        hold = next((e for e in self.journal if e.kind == "HOLD" and e.ref == auth_id and e.account == acct), None)
        if hold is None:
            # settlement with no authorization: nothing is debited; sits in the exception list
            return self._log(ev, "SETTLEMENT", acct, day, "REJECTED",
                             f"{auth_id} UNMATCHED: no authorization; {amount} not debited, routed to exceptions")
        if self.auth_state(auth_id) == "SETTLED":
            return self._log(ev, "SETTLEMENT", acct, day, "REJECTED", f"{auth_id} already settled")
        self._post(ev, acct, "SETTLEMENT", -amount, vd, day, ref=auth_id)
        self._post(ev, acct, "HOLD_RELEASE", hold.amount, vd, day, ref=auth_id)  # whole hold released
        return self._log(ev, "SETTLEMENT", acct, day, "ACCEPTED",
                         f"{auth_id} settled {amount}; hold {hold.amount} released")

    def reverse(self, ev, acct, target_ev, vd, day):
        """Compensating entry for CREDIT/DEBIT entries posted by `target_ev`. Original is untouched."""
        targets = [e for e in self.journal if e.event == target_ev and e.account == acct
                   and e.kind in ("CREDIT", "DEBIT")]
        done = {e.ref for e in self.journal if e.kind == "REVERSAL"}
        if not targets or any(t.id in done for t in targets):
            return self._log(ev, "REVERSAL", acct, day, "REJECTED",
                             f"{target_ev} unknown or already reversed")
        for t in targets:
            self._post(ev, acct, "REVERSAL", -t.amount, vd, day, ref=t.id)
        return self._log(ev, "REVERSAL", acct, day, "ACCEPTED", f"reverses {target_ev}")

    # ---- system: end of day -----------------------------------------------
    def _fee_base(self, acct, d):
        """Closing balance of day d, excluding day d's own fee (otherwise a fee justifies itself)."""
        return self.balance(acct, d, skip=lambda e: e.kind in ("FEE", "FEE_REVERSAL") and e.value_day == d)

    def end_of_day(self, day):
        """Re-evaluate overdraft fees for every day <= `day`, ascending (earlier fees feed later bases).
        Back-valued entries can create fees on closed days; their correction reverses them."""
        new = []
        for acct, ccy in self.accounts.items():
            for d in range(1, day + 1):
                negative = self._fee_base(acct, d) < 0
                if negative == self.fee_active(acct, d):
                    continue
                if negative and ccy not in OVERDRAFT_FEE:
                    raise ValueError(f"no overdraft fee defined for {ccy}")
                fee = OVERDRAFT_FEE[ccy]
                new.append(self._post(f"EOD{day}", acct, "FEE" if negative else "FEE_REVERSAL",
                                      -fee if negative else fee, d, day, ref=f"day{d}"))
        return new

    def accruals(self, acct, last_day=WINDOW):
        """Per-day rounded interest on the final restated closing balance (capitalization excluded)."""
        ccy = self.accounts[acct]
        out = {}
        for d in range(1, last_day + 1):
            bal = self.balance(acct, d, skip=lambda e: e.kind == "INTEREST")
            out[d] = (bal, money(bal * DAILY_RATE, ccy) if bal > 0 else money(0, ccy))
        return out

    def capitalize_interest(self, acct, day=WINDOW):
        """One credit, value_date = `day`, equal to the sum of the rounded daily accruals."""
        if any(e.kind == "INTEREST" and e.account == acct for e in self.journal):
            return None
        total = sum((a for _, a in self.accruals(acct, day).values()), Decimal(0))
        return self._post(f"EOD{day}", acct, "INTEREST", total, day, day) if total else None
