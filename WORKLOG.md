# WORKLOG

Real timestamps (local, +04), appended as work happens.

- 2026-10-17 16:17  Repo initialised. Design agreed: event-sourced core (events log + frozen journal entries, all state derived), Decimal money, Python stdlib.
- 2026-10-17 16:19  Core + replay.py done. Output matches hand-derived numbers: EOD5 -230.00, 3 fees (D2/D4/D5), Auth-B declined (-245 after hold), E6 unmatched, EOD6 3 fee reversals, interest 1.03 AED / 0.008 BHD, finals 466.03 / 10.008.
