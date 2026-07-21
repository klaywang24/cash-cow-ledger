"""Regression test for the 2026-07-20 inception defect (ERRATA.md): dual-class
deduplication must run BEFORE the top-N cut, and the published ranking must keep
ranks beyond N so the §7.2 exit buffer (removal only past rank 2N) can be computed.

Pinned to the inception-day funnel committed in the repo; no network access.
Run: python -m tests.test_dedup
"""
import csv, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.screen import dedup_dual_class

ROOT = pathlib.Path(__file__).resolve().parents[1]
FUNNEL = ROOT / "output/funnel_2026-07-20.csv"


def test_inception_funnel():
    rows = [r for r in csv.DictReader(open(FUNNEL)) if r["status"] == "pass_valuation"]
    rows.sort(key=lambda r: float(r["score"]), reverse=True)

    ranking = dedup_dual_class(rows)
    tickers = [r["ticker"] for r in ranking]

    # 23 L4 survivors that day; FOX + FOXA are one company, so 22 distinct seats
    assert len(rows) == 23, len(rows)
    assert len(ranking) == 22, len(ranking)
    assert "FOX" in tickers and "FOXA" not in tickers, tickers

    # With one seat per company, the top 20 holds 20 DISTINCT companies and the
    # seat FOXA burned at inception goes to GRMN
    top20 = tickers[:20]
    assert len(set(top20)) == 20, top20
    assert "GRMN" in top20, top20

    # Deduplication is idempotent: a clean list must pass build_portfolio's guard
    assert len(dedup_dual_class(ranking)) == len(ranking)


if __name__ == "__main__":
    test_inception_funnel()
    print("OK: dedup-before-cut yields 20 distinct companies; GRMN takes the seat FOXA burned.")
