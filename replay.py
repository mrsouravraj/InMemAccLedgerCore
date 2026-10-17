"""Replays the event stream in the stated order and prints, per day: closing ledger balance,
fee assessments, authorization states and errors. Owns the clock; ledger.py owns the rules."""
from decimal import Decimal

from ledger import Ledger, WINDOW

ACCOUNTS = {"ACC-001": "AED", "ACC-002": "BHD"}
A1, A2 = "ACC-001", "ACC-002"

# (label, declared day, command, args...) in stream order. value_date is explicit per event.
STREAM = [
    ("E1", 1, "credit", A1, "1200.00", 1),
    ("E2", 1, "debit", A1, "950.00", 1),
    ("E3", 2, "authorize", A1, "Auth-A", "200.00", 2),
    ("E4", 3, "credit", A1, "400.00", 3),
    ("E5", 4, "settle", A1, "Auth-A", "185.00", 4),
    ("E6", 4, "settle", A1, "Auth-Z", "180.00", 4),
    ("E7", 5, "debit", A1, "620.00", 2),
    ("E8", 5, "authorize", A1, "Auth-B", "90.00", 5),
    ("E9", 6, "reverse", A1, "E7", 2),
    ("E10", 5, "instalments", A2, "10.000", 3, 5),
]
AUTHS = {"Auth-A": A1, "Auth-B": A1, "Auth-Z": A1}


def run():
    lg = Ledger(ACCOUNTS)
    clock = 1
    reports = {}   # day -> printable lines

    def close(day):
        mark = len(lg.journal)
        lg.end_of_day(day)
        if day == WINDOW:
            for acct in ACCOUNTS:
                lg.capitalize_interest(acct, day)
        sys_entries = lg.journal[mark:]
        out = [f"=== DAY {day} close ==="]
        for ev in (e for e in lg.events if e.day == day):
            out.append(f"  {ev.event:<4} {ev.kind:<18} {ev.account} {ev.outcome:<8} {ev.detail}")
        for e in sys_entries:
            out.append(f"  {e.event:<4} {e.kind:<18} {e.account} value Day {e.value_day}  {e.amount:>9}")
        for acct, ccy in ACCOUNTS.items():
            out.append(f"  {acct} {ccy}  ledger {lg.balance(acct, day):>9}  "
                       f"available {lg.available(acct, day):>9}  holds {lg.active_holds(acct)}")
        out.append("  auths: " + ", ".join(f"{a}={lg.auth_state(a)}" for a in AUTHS if lg.auth_state(a) != "UNKNOWN"))
        errs = [e for e in lg.events if e.day == day and e.outcome in ("REJECTED", "DECLINED")]
        out.append("  errors: " + ("; ".join(f"{e.event} {e.outcome}: {e.detail}" for e in errs) or "none"))
        reports[day] = out

    for label, declared, cmd, acct, *args in STREAM:
        while clock < declared:          # the business day advances: close the previous one
            close(clock)
            clock += 1
        if declared < clock:             # late arrival: processed today, value_date honoured
            lg.late_arrival(label, acct, declared, clock)
        if cmd == "reverse":
            lg.reverse(label, acct, args[0], args[1], clock)
        elif cmd == "instalments":
            lg.credit_instalments(label, acct, args[0], args[1], args[2], clock)
        else:
            getattr(lg, cmd)(label, acct, *args[:-1], args[-1], clock)
    while clock <= WINDOW:
        close(clock)
        clock += 1
    return lg, reports


def main():
    lg, reports = run()
    for d in sorted(reports):
        print("\n".join(reports[d]), end="\n\n")

    print("=== RESTATED closing ledger balance per value day (all knowledge at end of Day 6, interest excluded) ===")
    for acct, ccy in ACCOUNTS.items():
        row = {d: lg.balance(acct, d, skip=lambda e: e.kind == "INTEREST") for d in range(1, WINDOW + 1)}
        print(f"  {acct} {ccy}: " + "  ".join(f"D{d}={b}" for d, b in row.items()))

    print("\n=== INTEREST (0.04%/day on restated closing balance, positive only) ===")
    for acct, ccy in ACCOUNTS.items():
        acc = lg.accruals(acct)
        total = sum((a for _, a in acc.values()), Decimal(0))
        cap = sum((e.amount for e in lg.journal if e.account == acct and e.kind == "INTEREST"), Decimal(0))
        print(f"  {acct} {ccy}: " + "  ".join(f"D{d} {b}->{a}" for d, (b, a) in acc.items()))
        print(f"    sum of rounded accruals {total}  capitalized {cap}  exact match: {total == cap}")

    print("\n=== INVARIANTS ===")
    ids = [e.id for e in lg.journal]
    print(f"  journal entries {len(ids)}, ids unique and sequential: {ids == [f'J{i}' for i in range(1, len(ids) + 1)]}")
    print(f"  events logged {len(lg.events)} (rejected/declined kept: "
          f"{sum(e.outcome in ('REJECTED', 'DECLINED') for e in lg.events)})")
    for acct, ccy in ACCOUNTS.items():
        print(f"  {acct} final ledger balance {lg.balance(acct, WINDOW)} {ccy}")


if __name__ == "__main__":
    main()
