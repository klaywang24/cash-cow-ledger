# Errata

Defects are recorded here with dates, in the open. Recorded **values and judgments** are
never rewritten: when one is wrong, the mechanism changes and the record of the mistake
stays. **Identification errors** (a label pointing at the wrong object) are correctable
only under the numbered procedure of METHODOLOGY §9.1 — independent recomputation,
errata first, ordinary commits only. An errata history is an asset, not a liability.

**Last reviewed 2026-08-06.** Two entries below. Every published level from 2026-07-21
through 2026-08-06 has been independently recomputed from an exchange price source,
agreeing with the published figure to within 0.0001; `constituents.csv` remains
byte-identical to the inception commit. This line records that the log is current, not
merely un-updated.

---

## 2026-08-06 · A row published under the wrong date — Correction No. 1 under §9.1

**What happened.** The 2026-08-06 close was published as `2026-08-07,104.1012,19` —
labelled with a trading day that had not happened yet, while 2026-08-06 itself appeared
missing. The value was right; the label was wrong.

**Cause, three layers deep.**

1. *Trigger.* A nine-hour GitHub Actions incident (15:22–00:05 UTC, per the public
   status history: "workflow runs are failing to start or delayed") swallowed the day's
   scheduled run. The manual re-run happened at 00:42 UTC — after midnight UTC, still
   2026-08-06 in New York.
2. *Defect.* `daily_level.py` stamped rows with `date.today()` — the runner's UTC clock —
   instead of the trading day the bars themselves belong to. Every prior run had been
   correct only because the schedule (21:30 UTC = afternoon New York) kept both calendars
   on the same day. The correctness was a property of the timetable, not of the code.
3. *Process.* The identical defect had already been found and fixed in a sibling
   repository weeks earlier, with a comment spelling out the exact failure mode
   ("CI crossing UTC midnight"). The fix was never propagated here. Bugs must be swept
   as a class, not as instances.

**Evidence for the correction.** An independent implementation (standard library only,
straight to the exchange-data endpoint, no shared code with the pipeline) reproduces
the published levels for 08-03 / 08-04 / 08-05 to four decimals and computes
104.1012 for **2026-08-06**; all 19 constituents' latest bar is dated 2026-08-06;
no 2026-08-07 trading session existed at publication time.

**The correction.** Under METHODOLOGY §9.1 (adopted in the same commit series), the row's
date field was corrected `2026-08-07 → 2026-08-06`. The value was not touched. Ordinary
commit, no history rewritten: the erroneous commit and this correction are both
permanently visible (`git log -p data/ledger/index_level.csv`). Had the row been left
standing, the next real 2026-08-07 close would have been skipped as "already recorded" —
a knowingly false record does not preserve integrity, it compounds.

**What changed so this class dies.**

- Date labels now come from the bars' own timestamps, converted to US-Eastern. The clock
  can veto (a "future" bar refuses to write) but can never supply a date.
- Two refuse-to-write gates: constituents disagreeing on the last trading day, and a
  bar dated in the future, both abort the run loudly. Verified against negative samples.
- Every remaining `date.today()` in the repository (5 sites) pinned to America/New_York.
- The freshness monitor now compares the ledger against the last **completed trading
  day** read off SPY's own bars, runs shortly after midnight New York, and alerts the
  same night a day goes missing or a future-dated row appears — staleness can no longer
  hide for days behind the old 4-day calendar threshold.
- A backup schedule trigger two hours after the primary (the pipeline is idempotent), so
  a single dropped cron event no longer costs the day.

**What did not change.** Every published level. `constituents.csv`. Git history.

---

## 2026-07-21 · Inception opened with 19 constituents instead of 20

**What happened.** At inception (2026-07-20) the book was recorded with 19
constituents against a target of N = 20 (`data/ledger/index_level.csv`:
`n_constituents=19`).

**Cause.** A deterministic code defect. Not a data, network or vendor failure:
EDGAR, the price source and the CI run were all correct that day, and every entry
price matches the exchange close to the cent. The screen truncated the candidate
list to the top 20 **before** dual-class deduplication ran. FOX and FOXA, two share
classes of Fox Corporation ranked 10 and 11, each held a seat; deduplication then
removed FOXA and no successor was pulled up. 23 names passed L4 that day, so the
shortfall was avoidable: by composite score the 20th seat belonged to GRMN
(Garmin, 0.2503). The min-holdings floor (15) is far below 19, so no guard fired.

**Second defect on the same seam, fixed pre-emptively.** The truncated candidates
file also starved the §7.2 exit buffer: an incumbent ranked 21–40 at a review would
have read as "no longer passes L2/L3/L4" and been wrongly removed, because ranks
beyond 20 did not exist in the file.

**What changed (commit `fb940ea`).**

- Deduplication now runs before any truncation, in one shared function
  (`src/screen.py::dedup_dual_class`); a duplicate share class can no longer burn a seat.
- `candidates_*.csv` now carries the **full** deduplicated ranking of L4 survivors,
  not just the top 20, so the §7.2 exit buffer has the ranks it needs.
- `build_portfolio` hard-refuses a candidates file that still contains a duplicate
  share class: a recurrence fails the pipeline loudly instead of shrinking the index silently.
- A regression test pinned to the inception-day funnel (`tests/test_dedup.py`)
  locks the behavior: 20 distinct companies, GRMN in seat 20.

**What did not change.** The ledger. The 2026-07-20 rows stand exactly as recorded:
19 constituents, weights summing to 1, base level 100. GRMN is **not** inserted
retroactively — rewriting an opening entry would defeat the point of a forward-only
record. Published levels are correct for the book as recorded. The vacancy persists
until the January 2027 review, when the standard §7 entry rules (rank ≤ 20, momentum
gate) fill the book back to N = 20.
