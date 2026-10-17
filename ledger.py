"""In-memory account ledger core.

Event-sourced: two append-only lists (`events`, `journal`) hold all history, plus a write-once
event-id index for idempotency. Balances, available balance, auth state and active fees are all derived
from them on demand. No I/O and no clock here: the caller passes the business
`day` a command is processed on (see replay.py).
"""
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

# --- constants (see NUMBERS.md) -------------------------------------------
PRECISION = {"AED": 2, "BHD": 3}          # minor units per currency
ROUNDING = ROUND_HALF_UP
OVERDRAFT_FEE = {"AED": Decimal("25.00")}  # no BHD fee defined -> raise, never default
DAILY_RATE = Decimal("0.0004")             # 0.04% per day
WINDOW = 6                                 # Day 1 .. Day 6

MEMO = {"HOLD", "HOLD_RELEASE"}            # affect available balance, not ledger balance


# Positions (after the event id) of the amount field and count field of each input command.
# Arguments are (acct, ..., value_day, processing_day).
AMOUNT_AT = {"credit": 1, "debit": 1, "credit_instalments": 1, "authorize": 2, "settle": 2}
COUNT_AT = {"credit_instalments": 2}
ID_AT = {"authorize": 1, "settle": 1, "reverse": 1}   # auth id / reversal target: identifiers beyond event and account


def _is_day(x):
    return isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= WINDOW


def command(fn):
    """Input-command guard.
    1. Event id: consumed on first receipt, whatever the outcome. Same id + same payload = redelivery, logged
       DUPLICATE, no effect. Same id + different payload: REJECTED. The processing day is not part of the payload.
       Amounts compare numerically ("100.0" == "100.00"); identifiers compare exactly ("01" != "1").
    2. Validation (`_invalid`) runs before anything is posted; a failure is a logged REJECTED event, not an exception.
    3. Identifiers (event, account, auth id, reversal target) must be non-empty strings; anything else raises
       before anything is stored. Strings are kept exactly, never normalised.
    4. All-or-nothing: if the command raises, every entry and event it staged is rolled back and the id is released,
       so a corrected retry starts from a clean ledger and cannot double-post."""
    name = fn.__name__

    def wrapper(self, ev, *args):
        acct, day = args[0], args[-1]
        ids = [ev, acct] + ([args[ID_AT[name]]] if name in ID_AT else [])
        if not all(isinstance(i, str) and i.strip() for i in ids):
            raise ValueError(f"identifiers must be non-empty strings, got {ids!r}")
        if acct not in self.accounts:
            raise KeyError(acct)
        kind = name.upper()
        payload = (name, tuple(_canon(a) if i == AMOUNT_AT.get(name) else str(a) for i, a in enumerate(args[:-1])))
        seen = self._received.get(ev)
        if seen is not None:
            if seen == payload:
                return self._log(ev, kind, acct, day, "DUPLICATE", "redelivery of an event id already received; ignored")
            return self._log(ev, kind, acct, day, "REJECTED", f"event id already used for a different payload ({seen[0]})")
        self._received[ev] = payload
        try:
            with self._atomic():
                if why := self._invalid(name, acct, args):
                    return self._log(ev, kind, acct, day, "REJECTED", why, str(args[1]) if name in ("authorize", "settle") else "")
                return fn(self, ev, *args)
        except BaseException:
            del self._received[ev]
            raise
    wrapper.__name__, wrapper.__doc__ = name, fn.__doc__
    return wrapper


def _canon(x):
    """Canonical text of an amount, so "100.0" and "100.00" compare equal."""
    try:
        return str(Decimal(str(x)).normalize())
    except InvalidOperation:
        return str(x)


def money(x, ccy):
    """Quantize to the currency's own precision. Floats are refused: 2.675 is really 2.67499..., so a float
    and the string "2.675" would post different amounts."""
    if isinstance(x, float):
        raise TypeError("money amounts must be str, int or Decimal, never float")
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
    outcome: str      # ACCEPTED | REJECTED | DECLINED | DUPLICATE
    detail: str = ""
    ref: str = ""     # auth id for AUTHORIZATION/SETTLEMENT events


class Ledger:
    def __init__(self, accounts):
        self.accounts = dict(accounts)   # id -> currency; opening balances are all zero
        self._events: list[Event] = []
        self._journal: list[Entry] = []
        self._received: dict[str, tuple] = {}   # event id -> payload; write-once index for the id check

    # Read-only views: callers get a tuple snapshot, so nothing outside can remove or reorder history.
    # ponytail: O(n) copy per access; fine here, expose an iterator/slice view if it ever shows up in a profile.
    @property
    def events(self):
        return tuple(self._events)

    @property
    def journal(self):
        return tuple(self._journal)

    @contextmanager
    def _atomic(self):
        """Commit group: staged entries and events either all stay or, on any exception, are all dropped.
        Only the uncommitted tail of this call is ever removed; committed history is never touched."""
        j, e = len(self._journal), len(self._events)
        try:
            yield
        except BaseException:
            del self._journal[j:], self._events[e:]
            raise

    # ---- append-only primitives -------------------------------------------
    def _post(self, ev, acct, kind, amount, vd, day, ref=""):
        e = Entry(f"J{len(self._journal) + 1}", ev, acct, kind,
                  money(amount, self.accounts[acct]), vd, day, ref)
        self._journal.append(e)
        return e

    def _log(self, ev, kind, acct, day, outcome, detail="", ref=""):
        self._events.append(Event(len(self._events) + 1, ev, kind, acct, day, outcome, detail, ref))
        return outcome

    # ---- derived state ----------------------------------------------------
    def balance(self, acct, day, skip=lambda e: False):
        """Ledger balance: all non-memo entries with value_date <= day."""
        return money(sum((e.amount for e in self._journal
                          if e.account == acct and e.kind not in MEMO
                          and e.value_day <= day and not skip(e)), Decimal(0)), self.accounts[acct])

    def active_holds(self, acct):
        return money(sum((e.amount if e.kind == "HOLD" else -e.amount for e in self._journal
                          if e.account == acct and e.kind in MEMO), Decimal(0)), self.accounts[acct])

    def available(self, acct, day):
        return self.balance(acct, day) - self.active_holds(acct)

    def auth_state(self, auth_id):
        if any(e.kind == "AUTHORIZATION" and e.ref == auth_id and e.outcome == "DECLINED"
               for e in self._events):
            return "DECLINED"
        refs = {e.kind for e in self._journal if e.ref == auth_id}
        if "SETTLEMENT" in refs:
            return "SETTLED"
        return "APPROVED" if "HOLD" in refs else "UNKNOWN"

    def fee_assessed(self, acct, d):
        """Has a fee ever been charged for day d (even if since reversed)? Enforces once per account per day."""
        return any(e.account == acct and e.kind == "FEE" and e.value_day == d for e in self._journal)

    def fee_active(self, acct, d):
        return sum((e.amount for e in self._journal if e.account == acct
                    and e.kind in ("FEE", "FEE_REVERSAL") and e.value_day == d), Decimal(0)) != 0

    # ---- commands: validate, then append ----------------------------------
    def late_arrival(self, ev, acct, declared, day):
        """Audit note: event declared on a closed day, processed on `day`. Closed days are never reopened."""
        return self._log(ev, "LATE_ARRIVAL", acct, day, "ACCEPTED", f"declared Day {declared}, processed Day {day}")

    def _invalid(self, name, acct, args):
        """Reason an input must be REJECTED, or None. Pure check: posts nothing."""
        vd, day = args[-2], args[-1]
        if not (_is_day(vd) and _is_day(day)):
            return f"value_date {vd!r} / processing day {day!r} must be whole days in 1..{WINDOW}"
        if vd > day:   # future-dated value is out of scope
            return f"value_date Day {vd} is after processing Day {day}"
        if self._finalized(acct):
            return "interest already capitalized: the window for this account is closed"
        ccy = self.accounts[acct]
        if name in AMOUNT_AT:
            raw = args[AMOUNT_AT[name]]
            if isinstance(raw, float):
                return f"amount {raw!r} is a float; pass a string or Decimal (binary floats cannot hold decimal money)"
            try:
                a = Decimal(str(raw))
                ok = a.is_finite() and a > 0 and money(a, ccy) > 0
            except InvalidOperation:
                ok = False
            if not ok:
                return f"amount {raw!r} must be a finite positive number of at least one {PRECISION[ccy]}-dp unit"
        if name in COUNT_AT:
            n = args[COUNT_AT[name]]
            if not (isinstance(n, int) and not isinstance(n, bool) and n >= 1):
                return f"count {n!r} must be a whole number >= 1"
        return None

    def _finalized(self, acct):
        """True once interest is capitalized: the window for this account is closed to further input."""
        return any(e.kind == "CAPITALIZATION" and e.account == acct for e in self._events)

    @command
    def credit(self, ev, acct, amount, vd, day):
        return self._simple(ev, "CREDIT", acct, money(amount, self.accounts[acct]), vd, day)

    @command
    def debit(self, ev, acct, amount, vd, day):
        return self._simple(ev, "DEBIT", acct, -money(amount, self.accounts[acct]), vd, day)

    def _simple(self, ev, kind, acct, signed, vd, day):
        self._post(ev, acct, kind, signed, vd, day)
        return self._log(ev, kind, acct, day, "ACCEPTED", f"{signed}")

    @command
    def credit_instalments(self, ev, acct, total, n, vd, day):
        """Equal split rounded down; the residual goes on the last instalment."""
        ccy = self.accounts[acct]
        total = money(total, ccy)
        base = (total / n).quantize(Decimal(1).scaleb(-PRECISION[ccy]), rounding=ROUND_DOWN)
        parts = [base] * (n - 1) + [total - base * (n - 1)]
        for i, p in enumerate(parts, 1):
            self._post(ev, acct, "CREDIT", p, vd, day, ref=f"{ev}.{i}")
        return self._log(ev, "CREDIT_INSTALMENTS", acct, day, "ACCEPTED", " + ".join(map(str, parts)))

    @command
    def authorize(self, ev, acct, auth_id, amount, vd, day):
        amount = money(amount, self.accounts[acct])
        detail = f"{auth_id} hold {amount}"
        if self.auth_state(auth_id) != "UNKNOWN":
            return self._log(ev, "AUTHORIZATION", acct, day, "REJECTED", f"{auth_id} duplicate id", auth_id)
        after = self.available(acct, day) - amount   # processing day: a back-dated hold cannot spend money already gone
        if after < 0:   # approved only if available stays >= 0 after the hold
            return self._log(ev, "AUTHORIZATION", acct, day, "DECLINED",
                             f"{detail}; available after hold would be {after}", auth_id)
        self._post(ev, acct, "HOLD", amount, vd, day, ref=auth_id)
        return self._log(ev, "AUTHORIZATION", acct, day, "ACCEPTED",
                         f"{detail}; available after hold {after}", auth_id)

    @command
    def settle(self, ev, acct, auth_id, amount, vd, day):
        amount = money(amount, self.accounts[acct])
        hold = next((e for e in self._journal if e.kind == "HOLD" and e.ref == auth_id and e.account == acct), None)
        if hold is None:
            # no hold behind it: nothing is debited; the event sits in the exception list
            why = "was declined" if self.auth_state(auth_id) == "DECLINED" else "does not exist"
            return self._log(ev, "SETTLEMENT", acct, day, "REJECTED",
                             f"{auth_id} UNMATCHED: auth {why}; {amount} not debited, routed to exceptions", auth_id)
        if self.auth_state(auth_id) == "SETTLED":
            return self._log(ev, "SETTLEMENT", acct, day, "REJECTED", f"{auth_id} already settled", auth_id)
        self._post(ev, acct, "SETTLEMENT", -amount, vd, day, ref=auth_id)
        self._post(ev, acct, "HOLD_RELEASE", hold.amount, vd, day, ref=auth_id)  # whole hold released
        return self._log(ev, "SETTLEMENT", acct, day, "ACCEPTED",
                         f"{auth_id} settled {amount}; hold {hold.amount} released", auth_id)

    @command
    def reverse(self, ev, acct, target_ev, vd, day):
        """Compensating entry for CREDIT/DEBIT entries posted by `target_ev`. Original is untouched."""
        targets = [e for e in self._journal if e.event == target_ev and e.account == acct
                   and e.kind in ("CREDIT", "DEBIT")]
        done = {e.ref for e in self._journal if e.kind == "REVERSAL"}
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

    def _assess_fees(self, acct, day):
        """Fee reconciliation for one account over days 1..day, ascending (earlier fees feed later bases).
        A day is charged at most once ever: a day whose fee was reversed is not charged again."""
        ccy = self.accounts[acct]
        for d in range(1, day + 1):
            negative = self._fee_base(acct, d) < 0
            if negative and ccy not in OVERDRAFT_FEE:
                raise ValueError(f"no overdraft fee defined for {ccy}")
            if negative and not self.fee_assessed(acct, d):
                self._post(f"EOD{day}", acct, "FEE", -OVERDRAFT_FEE[ccy], d, day, ref=f"day{d}")
            elif not negative and self.fee_active(acct, d):
                self._post(f"EOD{day}", acct, "FEE_REVERSAL", OVERDRAFT_FEE[ccy], d, day, ref=f"day{d}")

    def end_of_day(self, day):
        """Re-evaluate overdraft fees for every day <= `day`. Back-valued entries can create fees on closed days;
        their correction reverses them. All-or-nothing across accounts. Finalized accounts are skipped."""
        if not _is_day(day):
            raise ValueError(f"day must be a whole number in 1..{WINDOW}")
        mark = len(self._journal)
        with self._atomic():
            for acct in self.accounts:
                if not self._finalized(acct):
                    self._assess_fees(acct, day)
        return self._journal[mark:]

    def accruals(self, acct, last_day=WINDOW):
        """Per-day rounded interest on the final restated closing balance (capitalization excluded)."""
        ccy = self.accounts[acct]
        out = {}
        for d in range(1, last_day + 1):
            bal = self.balance(acct, d, skip=lambda e: e.kind == "INTEREST")
            out[d] = (bal, money(bal * DAILY_RATE, ccy) if bal > 0 else money(0, ccy))
        return out

    def capitalize_interest(self, acct, day=WINDOW):
        """Finalize an account: one controlled operation, Day 6 only. Fees are reconciled first, then the rounded
        daily accruals are summed into one credit (value_date Day 6), then the window closes: later inputs are
        rejected, so accruals can never drift from what was capitalized. Repeating it does nothing.
        The CAPITALIZATION event is logged even when the total is zero, so finalization is always on record."""
        if day != WINDOW:
            raise ValueError(f"interest is capitalized only at the end of Day {WINDOW}, not Day {day!r}")
        if acct not in self.accounts:
            raise KeyError(acct)
        if self._finalized(acct):
            return None
        with self._atomic():
            self._assess_fees(acct, day)
            total = sum((a for _, a in self.accruals(acct, day).values()), Decimal(0))
            entry = self._post(f"EOD{day}", acct, "INTEREST", total, day, day) if total else None
            self._log(f"EOD{day}", "CAPITALIZATION", acct, day, "ACCEPTED", f"capitalized {money(total, self.accounts[acct])}")
        return entry
