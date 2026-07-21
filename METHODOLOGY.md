# Cash Cow Ledger · Index Methodology

**Version: v1.0** ｜ Status: **Final, pending effect** ｜ **Inception: 2026-07-20 (Monday)**

> This version was published and third-party timestamped **before** the inception date — which is
> precisely the evidence that the rules were not adjusted after the fact.

> This document is the **sole authoritative source of rules** for Book One (the mechanical index).
> `config.yaml` is its executable copy; if the two ever conflict, this document governs and the
> config must be corrected immediately.
>
> **Change discipline:** any modification to this file must be a **standalone commit** whose title
> begins with `methodology:` and which states what changed and why. **Rule-change commits are kept
> strictly separate from data-update commits**, so that any third party can walk the commit history
> and verify that the rules went untouched across any given period.

_中文版见 [METHODOLOGY.zh.md](METHODOLOGY.zh.md)._

---

## 0. What this is, and what it does not promise

A **forward-tracked paper index**: a fixed set of rules selects "quality cash flow" companies from
US equities, the index level is computed every trading day, and constituents are reviewed twice a year.

**It does not promise to outperform any index.** It promises exactly two things:

1. **Reproducibility** — anyone with this document plus the public SEC EDGAR data can compute results
   identical to this ledger.
2. **No backfilling** — every computation after inception is timestamp-anchored; **rules are never
   revised because results look good or bad**. Losing periods are published the same as winning ones;
   losers are never deleted.

**Backtests are not the product.** Should any backtest be published later, it must be prominently
labeled as carrying look-ahead and survivorship bias.

---

## 1. Data sources

| Purpose | Source | Notes |
|---|---|---|
| US fundamentals | **SEC EDGAR `companyfacts` API** | Free, no key, full XBRL history (from ~2009) |
| Prices / valuation | yfinance | Price, market cap and trailing P/E only |
| Constituent universe | S&P 500 snapshot | See §2 |

**Only `us-gaap` tags are used, and only annual values from `10-K` / `10-K/A` filings with `fp=FY`.**
Foreign private issuers (those filing solely 20-F and using the `ifrs-full` taxonomy) are
**structurally out of scope** — their statements cannot be machine-reproduced by this pipeline.

### 1.1 Missing data

If any required field is missing, the security is flagged `data_incomplete`, **excluded from scoring
and from selection**, and the reason is recorded. **Filling with zero, estimates or industry averages
is forbidden.**

### 1.2 Known XBRL pitfalls and their handling (critical to reproducibility)

1. **A concept changes tags across accounting standards** (e.g. revenue uses `Revenues` before ASC 606
   and `RevenueFromContractWithCustomerExcludingAssessedTax` after) → each concept is given a list of
   candidate tags; earlier tags take precedence, and later tags are used **only to fill years the
   earlier ones are missing** (rather than "take the first non-empty tag and stop").
2. **Magnitude shifts within one series** (some companies start reporting in millions from a given
   year) → normalization is applied **only** when a year deviates from the series median by
   approximately an exact factor of 1000ⁿ (tolerance 0.12 log units); non-thousand-fold changes caused
   by splits or growth are left alone.
3. **Stock-split discontinuities** (share-count series) → adjacent-year ratios are scanned newest to
   oldest and back-adjusted when close to a common split factor (2 / 3 / 1.5 / 4 / 5 / 6 / 7 / 8 / 10 /
   15 / 20). **Share counts always use weighted diluted shares**
   (`WeightedAverageNumberOfDilutedSharesOutstanding`).
4. **Stale values after a company stops reporting a subtotal** (e.g. gross profit no longer filed from
   some year onward) → **staleness gate**: any metric's source year must be
   ≥ `that company's latest revenue fiscal year − 1`, otherwise it is treated as missing.
5. **Single-step income statements that omit operating income** (`OperatingIncomeLoss` absent) →
   fall back to **EBIT = pre-tax income + interest expense**.
6. **Currency mismatch**: yfinance reports market cap in the trading currency but financials in the
   reporting currency; when the two differ (e.g. ADRs), **FCF yield is not computed** and is treated
   as unavailable.

---

## 2. L1 · Constituent universe

An S&P 500 snapshot, **excluding**:

- All of GICS sector `Real Estate` (REITs are measured by FFO rather than FCF; the framework does not apply)
- GICS sector `Financials`, **except** the sub-industries `Transaction & Payment Processing Services`
  and `Financial Exchanges & Data`

**Rationale**: the free-cash-flow / gross-margin / ROIC framework is meaningless for **balance-sheet
financials** (banks, insurers, consumer finance) — their operating cash flow embeds deposit, loan and
float movements, and the "invested capital" denominator in ROIC does not hold. **Asset-light financials**
(payment networks, exchanges and data vendors) do have genuine margins and free cash flow, and are kept.

---

## 3. L2 · Landmine exclusions (any single hit removes the name)

| Rule | Threshold |
|---|---|
| Net income / operating cash flow above threshold for N consecutive years | ratio > 1.3, 3 consecutive years |
| Receivables growth exceeding revenue growth by a multiple | > 2.0×, 2 consecutive years |
| Net share count rising rather than falling over N years (2% tolerance) | 3-year lookback |
| Net debt / EBITDA | > 3.0 |

---

## 4. L3 · Quality inclusion (all must be satisfied)

| Rule | Threshold |
|---|---|
| Consecutive years of positive FCF | ≥ 7 years |
| FCF coefficient of variation (trailing 7 years, σ/μ) | ≤ 0.60 |
| Gross margin | ≥ 30% |
| 10-year gross-margin trend | last ≥ first × 0.95 (not declining) |
| 5-year average ROIC | ≥ 12% |
| Asset growth ≤ revenue growth (3-year CAGR, 1% tolerance) | capital discipline |

**Operating-margin fallback**: if a company **does not report gross-margin line items** (e.g. payment
networks have no COGS concept), an **operating margin ≥ 20%** substitutes for the two gross-margin rules
above. This fallback engages only when gross margin is unavailable.

**FCF = cash flow from operations − payments to acquire property, plant and equipment.**
**ROIC = EBIT × (1 − effective tax rate) / (shareholders' equity + total debt − cash)**; the effective
rate is income tax expense / pre-tax income, replaced by 21% when it falls outside [0, 0.6] or is
unavailable; years with invested capital ≤ 0 are skipped.

---

## 5. L4 · Valuation constraint (either one suffices)

- FCF yield (FCF / market cap) **≥ the 10-year US Treasury yield**, **or**
- Trailing P/E **≤ 30**

The 10-year Treasury yield is taken from `^TNX`, falling back to 4.5% when unavailable.

---

## 6. L5 · Constituent count and composite score

**Target constituent count N = 20.** If fewer than 15 names qualify, all qualifiers are held and the
anomaly is recorded — **the shortfall is not filled and no rule is relaxed**.

**Composite score** = min-max normalized factors within the qualifying pool, weighted:

| Factor | Weight |
|---|---|
| FCF yield | 0.30 |
| ROIC | 0.25 |
| Gross margin | 0.20 |
| FCF stability (negated coefficient of variation) | 0.15 |
| Low asset growth (negated) | 0.10 |

**Dual-class deduplication**: where one company has multiple tickers, only the highest-scoring
one is kept — applied **before** the top-N cut, so a duplicate share class can never burn a
seat (see [ERRATA.md](ERRATA.md), 2026-07-21).

---

## 7. Reconstitution rules

### 7.1 Review dates

**The first trading day of January and of July. At every other time, constituents do not change** —
including on mid-period landmine signals, earnings changes or violent price moves. Simplicity is what
makes it auditable.

### 7.2 Rank buffer (hysteresis)

- **Entry**: composite-score rank **≤ 20**, and price **above the 200-day moving average** (momentum veto).
- **Removal**: an incumbent falls to rank **> 40**, or no longer passes L2/L3/L4.
  **Exception**: an incumbent due for removal whose price is still above its 200-day moving average is
  **deferred by one review period**.
- Vacancies created by removals are filled by the highest-ranked non-holders, up to N = 20.

Absolute ranks are used rather than percentiles: when the qualifying pool holds only twenty-odd names,
"top 20%" degenerates to four or five.

### 7.3 Weighting

- **Weights are set at entry only**: entrants are allocated in proportion to composite score, **capped
  at 8% per name**, with the excess redistributed pro rata among uncapped names (iterated to convergence).
- **Never rebalanced after entry.** Weights drift freely with price; winners are not trimmed as they grow.
- Weight released by removals goes first to entrants; any remainder is distributed pro rata across
  incumbents (**pro-rata distribution preserves the relative weights among incumbents**, so it does not
  violate "never trim the winners").

**Why score-weighted rather than absolute-FCF-weighted**: weighting by absolute FCF systematically
favors large mature companies (measured: Altria's weight fell from 11.8% to 6.8%, and the max/min spread
compressed from 16× to 2.3×). A satellite sleeve should bet on **attractiveness**, not **size**.

### 7.4 Turnover

Annual turnover above 25% **records an alert**. The alert is a record only and **triggers no rule change**.

---

## 8. Index level

- Base level **100**, starting on the inception date.
- Computed from **adjusted close prices** (approximating total return including dividends).
- Computed and stored **once per trading day**. While constituents are unchanged, the level moves with
  price alone.

---

## 9. Tamper-evidence

- All data and this methodology are hosted in a **public Git repository**; commit times are attested by
  GitHub as a third party.
- Every data update is snapshotted to the **Internet Archive (Wayback Machine)**.
- Commit types are strictly separated: `methodology:` (rule change) / `data:` (data update) / other.
- **When an official source later revises historical data, the existing ledger records stay as they are**;
  the revision is noted separately and never silently overwrites.

---

## 10. Explicitly not done

- No automated trading; no brokerage API of any kind.
- Rules are never relaxed because a backtest looks good.
- Computation never continues on estimated values when data is missing.
- Nothing here constitutes investment advice.

---

## Appendix A: Human discretion is not part of this methodology

This index contains **no discretionary component**. The author's personal discretionary holdings, if any,
are recorded outside this repository and are **entirely isolated from the index, affecting none of its
calculations**. The separation exists to keep the index 100% mechanical, reproducible and auditable —
any discretion would destroy all three.

---

## Appendix B: Implementation status (guarding against "documented but not built")

This table verifies that every clause of the methodology is actually implemented in code. **It must be
updated whenever a rule is added** — "promised in the document but absent from the code" is the class of
defect most likely to turn into a rule violation six months later.

| Methodology clause | Implemented in | Status |
|---|---|---|
| §1 Data sources and the six XBRL pitfalls | `src/edgar.py` / `src/metrics.py` | ✅ |
| §2 L1 universe, financials/REIT exclusion | `src/run_screen.py::load_universe` | ✅ |
| §3 L2 landmines | `src/screen.py::screen_us` | ✅ |
| §4 L3 quality + operating-margin fallback | `src/screen.py::screen_us` | ✅ |
| §5 L4 valuation | `src/screen.py::apply_valuation` | ✅ |
| §6 L5 count and composite score | `src/run_screen.py::score_pool` / `build_portfolio` | ✅ |
| §6 Dual-class dedup, before the top-N cut | `src/screen.py::dedup_dual_class` + guard in `build_portfolio` | ✅ |
| §7.1 Review dates (January / July) | `src/reconstitute.py` + `daily.yml` | ✅ |
| §7.2 Rank buffer + two-sided momentum veto | `src/reconstitute.py` | ✅ |
| §7.3 Weighting and "never rebalanced after entry" | `src/build_portfolio.py` / `open_books.py` / `reconstitute.py` | ✅ |
| §7.4 Turnover budget alert | `src/reconstitute.py` | ✅ |
| §8 Index level (units mechanism) | `src/daily_level.py` | ✅ |
| §9 Tamper-evidence (public repo / Wayback / commit typing) | `.github/workflows/daily.yml` | ✅ |
| — Ledger freshness monitor | `src/check_freshness.py` + `monitor.yml` | ✅ |

---

## Change history

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-07-18 | First version. Finalized and publicly anchored before inception; inception set to 2026-07-20. |
| v1.0.1 | 2026-07-21 | Mechanism fix only, no rule or threshold change: dual-class dedup moved before the top-N cut; candidates file carries the full ranking; duplicate-seat guard added ([ERRATA.md](ERRATA.md)). |
