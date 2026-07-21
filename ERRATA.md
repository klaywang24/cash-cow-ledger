# Errata

Defects are recorded here with dates, in the open. Ledger rows are never rewritten:
when something is wrong, the mechanism changes and the record of the mistake stays
(see METHODOLOGY §9, tamper-evidence). An errata history is an asset, not a liability.

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
