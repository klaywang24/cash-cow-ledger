"""The index divisor (METHODOLOGY §8).

A paper index holds no cash. When a constituent must be added and no removal released any
value to fund it — the seat FOXA burned at inception is exactly this case, and ERRATA
2026-07-21 promises it is refilled in January 2027 — the value has to come from somewhere,
and every candidate dilutes somebody.

Two mechanisms give mathematically IDENTICAL weights afterwards:

  pro-rata dilution   scale every incumbent's units by (1 - w). No new state, no formula
                      change — and it TRIMS incumbent units.
  divisor             leave every incumbent's units untouched and absorb the change in a
                      denominator: level = sum(units x price) / divisor.

They are indistinguishable in weights, in level, and in every future reading. The tiebreak
is an invariant this repository already enforces and audits: A RECONSTITUTION NEVER TRIMS AN
INCUMBENT'S UNITS (src/reconstitute.py, §7.3). Dilution breaks it; the divisor is the only
option that keeps it true. That, and not elegance, is why the divisor exists here.

What the divisor is NOT: it is not a free lunch. Incumbent UNITS are untouched, but incumbent
PERCENTAGE WEIGHTS fall, because a twentieth name cannot appear without everyone's share
getting smaller. §7.3 promises holdings are never trimmed; it never promised a fixed share.
Anyone replicating this index with real money would have to sell a slice of everything to buy
the entrant. The index is a measurement, and this is where measurement and replication part
company — stated here rather than buried.

Reproducibility: the divisor is 1.0 from inception, so every level published before the first
adjustment reproduces from `sum(units x price)` exactly as before. Adjustments are recorded
forward-only, in their own file, and no historical row is ever touched — adding a column to an
append-only tamper-evident file would itself be a rewrite of history.
"""
from __future__ import annotations
import csv, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIVISOR_FILE = ROOT / "data/ledger/divisor.csv"
FIELDS = ["effective_date", "divisor", "reason"]


def history():
    if not DIVISOR_FILE.exists():
        return []
    return list(csv.DictReader(open(DIVISOR_FILE)))


def divisor_on(date_str, rows=None):
    """The divisor in effect for a trading date: the last row effective on or before it.

    Returns 1.0 when nothing applies — which is not a fallback but the correct value, and
    exactly what reproduces every level published before the first adjustment."""
    d = 1.0
    for r in sorted(rows if rows is not None else history(), key=lambda r: r["effective_date"]):
        if r["effective_date"] <= date_str:
            d = float(r["divisor"])
    return d


def record(effective_date, divisor, reason):
    fresh = not DIVISOR_FILE.exists()
    DIVISOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DIVISOR_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(FIELDS)
        w.writerow([effective_date, f"{divisor:.10f}", reason])
