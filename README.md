# In-Memory Account Ledger Core

Append-only, event-sourced, in-memory ledger. Python 3.13, standard library only (`decimal`, `unittest`). No web layer, persistence, UI or database.

## Run

```bash
python3 replay.py                                   # replays the event stream, prints per-day report
python3 -m unittest tests.test_ledger               # 32 tests, all green
python3 -m unittest discover -s tests -t .          # 33 tests: exactly 1 FAILS on purpose (see below)
python3 -m unittest tests.test_known_failure        # the annotated failing test on its own
```

## Reading the `replay.py` output

For each `=== DAY n close ===` block:

- **Event lines** `E3 AUTHORIZATION ACC-001 ACCEPTED …`: every input event processed that day with its outcome (`ACCEPTED` / `REJECTED` / `DECLINED` / `DUPLICATE`) and the numbers behind the decision. A `LATE_ARRIVAL` line marks an event declared on an earlier day but processed today.
- **`EODn FEE` / `FEE_REVERSAL` / `INTEREST` lines**: postings the system made at that close. The `value Day d` column is the day the posting counts for, which can be earlier than the day shown.
- **`ledger` / `available` / `holds`**: closing balance (entries with value date ≤ that day, as known at that close), balance minus active holds, and total active holds.
- **`auths`**: state of each authorization (`APPROVED`, `DECLINED`, `SETTLED`).
- **`errors`**: rejected and declined events of the day, with reasons.

After the daily blocks:

- **RESTATED**: each value day's closing balance with everything known at the end of Day 6. Differs from the printed day reports where back-valued or late items arrived.
- **INTEREST**: the per-day balance → rounded accrual, the sum, the capitalized credit, and an exact-match flag.
- **INVARIANTS**: journal ids unique and sequential, event counts, final balances.

## Hand-check (the numbers the run should reproduce)

ACC-001 (AED): Day 5 close −230.00 = 250.00 + 400.00 − 185.00 − 620.00 − 3 × 25.00. Fee days: Day 2 (−370.00), Day 4 (−180.00 with the Day-2 fee in the base), Day 5 (−205.00 with both). Day 3 is 30.00 − 25.00 = 5.00, no fee. After the Day-6 reversal: restated 250 / 250 / 650 / 465 / 465 / 465. Interest 0.10 + 0.10 + 0.26 + 0.19 + 0.19 + 0.19 = 1.03. Final 466.03.
ACC-002 (BHD): 3.333 + 3.333 + 3.334 = 10.000 on Day 5. Interest 0.004 + 0.004 = 0.008. Final 10.008.

## Layout

| File | What it is |
|---|---|
| `ledger.py` | The ledger: frozen `Entry`/`Event` records, two append-only lists, derived balances, commands, end-of-day |
| `replay.py` | Owns the business-day clock, the event stream and all printing |
| `tests/test_ledger.py` | Criterion checks and rule tests (green) |
| `tests/test_known_failure.py` | One deliberate failure, annotated with what it reveals |
| `NUMBERS.md` | Every constant, given vs chosen, with half-value results |
| `AMBIGUITIES.md` | Every ambiguity found and how it was resolved |
| `REJECTED.md` | Criteria refused with reasons, plus approaches dropped |
| `WORKLOG.md` | Timestamped record of the build |
| `ARCHITECTURE.md` / `.pdf` | Architecture and trade-offs document (3 pages) |
| `docs/arch.css` | Print stylesheet used to render the PDF |

Regenerate the PDF (`docs/arch.html` is a generated intermediate and is gitignored; `docs/arch.css` is hand-written source and stays tracked):

```bash
pandoc ARCHITECTURE.md -s --metadata pagetitle="Ledger Core" -c docs/arch.css -o docs/arch.html
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --no-pdf-header-footer --print-to-pdf=ARCHITECTURE.pdf "file://$PWD/docs/arch.html"
```
