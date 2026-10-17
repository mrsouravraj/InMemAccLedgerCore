# WORKLOG

Real timestamps (local, +04), appended as work happens.

- 2026-10-17 16:17  Repo initialised. Design agreed: event-sourced core (events log + frozen journal entries, all state derived), Decimal money, Python stdlib.
- 2026-10-17 16:19  Core + replay.py done. Output matches hand-derived numbers: EOD5 -230.00, 3 fees (D2/D4/D5), Auth-B declined (-245 after hold), E6 unmatched, EOD6 3 fee reversals, interest 1.03 AED / 0.008 BHD, finals 466.03 / 10.008.
- 2026-10-17 16:21  Tests (16 green + 1 annotated known failure), NUMBERS, AMBIGUITIES, REJECTED written. Test bug found+fixed: BHD floor is 1.250 not 1.249 (see REJECTED #9).
- 2026-10-17 16:23  ARCHITECTURE.md + PDF (3 pages), README written. Banned-wording grep clean (one hit in replay.py docstring fixed).
- 2026-10-17 16:25  Review fixes: known-failure test now uses available (ledger - holds); PDF regenerated (3 pages). Next: user creates empty public repo, I push.
- 2026-10-17 16:31  Self-review fixes: read-only history views, structured Event.ref, future-date guard on all commands, declined-auth settlement message, generated docs/arch.html untracked. 19 green + 1 known failure.
- 2026-10-17 16:38  Added event-id validation (@command: DUPLICATE for same payload, REJECTED for conflicting payload); 4 new tests; docs + PDF updated.
- 2026-10-17 17:01  Final checks green (23 tests + 1 known failure). Pushing to origin/main.
