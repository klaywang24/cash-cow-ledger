# Cash Cow Ledger · 现金牛台账

> A **forward-tracked paper index** of U.S. quality-cash-flow companies. Rules are
> published, frozen, and third-party timestamped **before** the record begins —
> so they cannot be tuned after the fact. **It does not promise to beat anything.**
> It promises to be reproducible, and never backfilled.

[![daily-index](https://github.com/klaywang24/cash-cow-ledger/actions/workflows/daily.yml/badge.svg)](https://github.com/klaywang24/cash-cow-ledger/actions/workflows/daily.yml)
[![freshness-monitor](https://github.com/klaywang24/cash-cow-ledger/actions/workflows/monitor.yml/badge.svg)](https://github.com/klaywang24/cash-cow-ledger/actions/workflows/monitor.yml)
[![License](https://img.shields.io/badge/license-PolyForm--Noncommercial--1.0.0-4a5d3a)](LICENSE)
[![Methodology](https://img.shields.io/badge/methodology-v1.0-2b5f8f)](METHODOLOGY.md)
[![Data](https://img.shields.io/badge/data-SEC%20EDGAR-6b4c9a)](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
[![Inception](https://img.shields.io/badge/inception-2026--07--20-a0392f)](data/ledger/)
[![中文](https://img.shields.io/badge/%E4%B8%AD%E6%96%87-README-2b5f8f)](README.zh.md)

**Methodology: [METHODOLOGY.md](METHODOLOGY.md) (v1.0)** — `config.yaml` is its executable copy.
_Chinese version: [METHODOLOGY.zh.md](METHODOLOGY.zh.md)_

---

## What it promises, and what it does not

**It does not promise** to outperform the S&P 500 or any other index. That is not
its goal, and it makes no such claim.

**It promises exactly two things:**

1. **Reproducibility** — anyone with this repository's methodology and the free,
   public SEC EDGAR API can compute **identical** results. Every data source,
   every threshold, and every known XBRL pitfall (with its handling) is documented.
2. **No backfilling** — the rules were published and timestamped *before* inception
   and are never revised to flatter results. Losing periods are published the same
   as winning ones; **losers are never deleted**.

**This is an index, not a trading record.** Like every index, it excludes slippage,
taxes, and liquidity costs.

**Backtests are not the product.** Any backtest published later must be prominently
labeled as carrying look-ahead and survivorship bias.

---

## Why this exists

Any stock-selection rule that can be written down has already been productized by
Wall Street (COWZ, QUAL, and dozens more), and factor premia decay by roughly half
after publication. So the scarce thing here is **not the rules** — those are public,
and you are welcome to copy them.

What is scarce is **the forward, tamper-evident ledger**: rules nailed down in public
*before* any result existed, and every day since recorded under third-party timestamps.
**A snapshot can be copied. The ledger cannot.**

---

## The five-layer funnel

| Layer | What it does | The trap it blocks |
|---|---|---|
| **L1 Universe** | S&P 500 snapshot, excluding balance-sheet financials (banks / insurers / consumer finance) and REITs | The FCF–margin–ROIC frame is meaningless for them |
| **L2 Landmines** | Drop on: earnings far exceeding operating cash flow · receivables growing >2× revenue · share count rising despite buybacks · net debt/EBITDA too high | Accounting games and financial engineering |
| **L3 Quality** | Require all of: FCF positive ≥7 consecutive years with low variance · gross margin ≥30% and not declining over 10 years · 5-year average ROIC ≥12% · asset growth ≤ revenue growth | Cyclical peaks masquerading as cash cows; empire-building |
| **L4 Valuation** | FCF yield ≥ 10-year Treasury **or** P/E ≤ 30 | Paying too much for a good business |
| **L5 Count** | Top 20 by composite score | — |

**Weighting** — score-weighted at entry, capped at 8% per name; **never rebalanced
afterwards**, so winners are allowed to drift upward.

**Reconstitution** — first trading day of January and July only. A rank buffer
(enter at ≤20, exit only past 40) keeps turnover low.

The choice of N = 20 is derived, not arbitrary: diversification benefits saturate
between 15 and 25 names; a mechanical screen has low information coefficient per
name, so by IR ≈ IC × √breadth it should not be concentrated; but sector
concentration caps *effective* breadth near 25. The two forces cross at 20–25.

---

## Data sources

- **U.S. fundamentals** — [SEC EDGAR `companyfacts` API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
  (free, no key, full XBRL history from ~2009)
- **Prices / valuation** — yfinance (price, market cap, trailing P/E only)

Only `us-gaap` tags and annual `10-K` / `10-K/A` values are used. Foreign private
issuers filing solely 20-F are **structurally out of scope** — their statements
cannot be reproduced by this pipeline.

**Missing data is flagged `data_incomplete` and excluded. Never filled with zero,
estimates, or industry averages.**

Six known EDGAR pitfalls — tag changes across accounting standards, magnitude shifts
within a series, stock-split discontinuities, stale values after a company stops
reporting a subtotal, single-step income statements, and currency mismatches — are
documented with their handling in [METHODOLOGY.md §1.2](METHODOLOGY.md).

---

## Usage

```bash
python -m src.run_screen       # run the L1–L5 funnel; writes candidates + full rejection ledger
python -m src.build_portfolio  # score-weighted constituents and weights
python -m src.open_books       # one-time: open the ledger on the inception date
python -m src.daily_level      # compute today's index level (exits safely before inception)
```

Data health checks (run these after touching extraction or metric logic):

```bash
python tests/probe_edgar.py    # raw annual series
python tests/probe_metrics.py  # derived signals + normalization checks
```

---

## Tamper-evidence

- Public repository; commit times are attested by GitHub as a third party.
- Every data update is snapshotted to the [Internet Archive](https://web.archive.org/).
- **Commit types are strictly separated**: `methodology:` = rule changes, `data:` =
  data updates. Anyone can walk the history and verify that the rules went untouched
  across any given period.
- A separate **freshness monitor** fails loudly if the ledger stops updating — a
  silent gap in the record is the one thing that would undermine all of the above.

---

## Ledger

| | |
|---|---|
| Inception | **2026-07-20** |
| Base level | 100 |
| Constituents | 20 |
| Reconstitution | 1st trading day of January and July |
| Files | [`data/ledger/`](data/ledger/) — `constituents.csv`, `index_level.csv` |

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free for noncommercial use; commercial use
requires a separate license.

## Disclaimer

Everything in this repository is for informational and research purposes only. It is
**not investment advice** and recommends no security. Markets carry risk; make your
own decisions.
