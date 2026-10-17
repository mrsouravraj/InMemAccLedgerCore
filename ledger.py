"""In-memory account ledger core.

Event-sourced: two append-only lists (`events`, `journal`) are the only stored
state. Balances, available balance, auth state and active fees are all derived
from them on demand. No I/O and no clock here: the caller passes the business
`day` a command is processed on (see replay.py).
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

# --- constants (see NUMBERS.md) -------------------------------------------
PRECISION = {"AED": 2, "BHD": 3}          # minor units per currency
ROUNDING = ROUND_HALF_UP
OVERDRAFT_FEE = {"AED": Decimal("25.00")}  # no BHD fee defined -> raise, never default
DAILY_RATE = Decimal("0.0004")             # 0.04% per day
WINDOW = 6                                 # Day 1 .. Day 6

MEMO = {"HOLD", "HOLD_RELEASE"}            # affect available balance, not ledger balance


def _canon(x):
    """Canonical text of a payload field, so "100.0" and "100.00" compare equal."""
    try:
        return str(Decimal(str(x)).normalize())
    except InvalidOperation:
        return str(x)


def command(fn):
    """Event-id check for every input command. An id is consumed on first receipt, whatever the outcome.
    Same id + same payload = redelivery: logged DUPLICATE, no effect. Same id + different payload: REJECTED.
    The processing day (last argument) is not part of the payload, so a redelivery on a later day is still a duplicate."""
    def wrapper(self, ev, *args):
        if not isinstance(ev, str) or not ev.strip():
            raise ValueError("event id must be a non-empty string")
        payload = (fn.__name__, tuple(_canon(a) for a in args[:-1]))
        kind, acct, day = fn.__name__.upper(), args[0], args[-1]
        seen = self._received.get(ev)
        if seen is None:
            self._received[ev] = payload
            return fn(self, ev, *args)
        if seen == payload:
            return self._log(ev, kind, acct, day, "DUPLICATE", "redelivery of an event id already received; ignored")
        return self._log(ev, kind, acct, day, "REJECTED", f"event id already used for a different payload ({seen[0]})")
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


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

    def fee_active(self, acct, d):
        return sum((e.amount for e in self._journal if e.account == acct
                    and e.kind in ("FEE", "FEE_REVERSAL") and e.value_day == d), Decimal(0)) != 0

    # ---- commands: validate, then append ----------------------------------
    def late_arrival(self, ev, acct, declared, day):
        """Audit note: event declared on a closed day, processed on `day`. Closed days are never reopened."""
        return self._log(ev, "LATE_ARRIVAL", acct, day, "ACCEPTED", f"declared Day {declared}, processed Day {day}")

    def _future(self, ev, kind, acct, vd, day, ref=""):
        """Future-dated value is out of scope: returns the REJECTED outcome, or None if vd is acceptable."""
        if vd > day:
            return self._log(ev, kind, acct, day, "REJECTED", f"value_date Day {vd} is after processing Day {day}", ref)
        return None

    @command
    def credit(self, ev, acct, amount, vd, day):
        return self._simple(ev, "CREDIT", acct, money(amount, self.accounts[acct]), vd, day)

    @command
    def debit(self, ev, acct, amount, vd, day):
        return self._simple(ev, "DEBIT", acct, -money(amount, self.accounts[acct]), vd, day)

    def _simple(self, ev, kind, acct, signed, vd, day):
        if bad := self._future(ev, kind, acct, vd, day):
            return bad
        self._post(ev, acct, kind, signed, vd, day)
        return self._log(ev, kind, acct, day, "ACCEPTED", f"{signed}")

    @command
    def credit_instalments(self, ev, acct, total, n, vd, day):
        """Equal split rounded down; the residual goes on the last instalment."""
        if bad := self._future(ev, "CREDIT_INSTALMENTS", acct, vd, day):
            return bad
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
        if bad := self._future(ev, "AUTHORIZATION", acct, vd, day, auth_id):
            return bad
        if self.auth_state(auth_id) != "UNKNOWN":
            return self._log(ev, "AUTHORIZATION", acct, day, "REJECTED", f"{auth_id} duplicate id", auth_id)
        after = self.available(acct, vd) - amount
        if after < 0:   # approved only if available stays >= 0 after the hold
            return self._log(ev, "AUTHORIZATION", acct, day, "DECLINED",
                             f"{detail}; available after hold would be {after}", auth_id)
        self._post(ev, acct, "HOLD", amount, vd, day, ref=auth_id)
        return self._log(ev, "AUTHORIZATION", acct, day, "ACCEPTED",
                         f"{detail}; available after hold {after}", auth_id)

    @command
    def settle(self, ev, acct, auth_id, amount, vd, day):
        amount = money(amount, self.accounts[acct])
        if bad := self._future(ev, "SETTLEMENT", acct, vd, day, auth_id):
            return bad
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
        if bad := self._future(ev, "REVERSAL", acct, vd, day):
            return bad
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
        if any(e.kind == "INTEREST" and e.account == acct for e in self._journal):
            return None
        total = sum((a for _, a in self.accruals(acct, day).values()), Decimal(0))
        return self._post(f"EOD{day}", acct, "INTEREST", total, day, day) if total else None
