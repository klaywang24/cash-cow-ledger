"""Negative samples for the §7.3 vacancy clause and the §8 divisor.

Driven end to end through the real reconstitute.main(), writing to a throwaway ledger, then
reading back what it actually wrote. Nothing here restates the arithmetic — a test that
recomputes the thing it is checking only proves the author is self-consistent.

The three properties that matter, and what breaks if each is lost:

  level continuity   adding a constituent is not a return. If the level jumps on the review
                     date, every performance figure spanning that date is wrong by the jump.
  units untouched    "a reconstitution never trims an incumbent's units" (§7.3). This is the
                     invariant that made the divisor necessary at all instead of the simpler
                     pro-rata dilution — if it can be broken, the divisor bought nothing.
  the promise kept   ERRATA 2026-07-21 says the seat FOXA burned is refilled to N in January
                     2027. Before this clause, freed == 0 meant the seat stayed empty and
                     nothing said so.

No network. Run: python -m tests.test_vacancy_divisor
"""
import csv, io, pathlib, sys, tempfile, datetime as dt
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import src.reconstitute as rc
import src.divisor as D

ROOT = pathlib.Path(__file__).resolve().parents[1]
JAN = ["2026-12-31", "2027-01-04", "2027-01-05"]
DAY = dt.date(2027, 1, 4)
PRICE = 100.0


def run_review(constituents, ranking, prices, tmp):
    """Drive the real main() against a throwaway ledger; return what it wrote."""
    tmp = pathlib.Path(tmp)
    con = tmp / "constituents.csv"
    with open(con, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rc.FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in constituents:
            w.writerow({k: r.get(k, "") for k in rc.FIELDS})

    saved = (rc.CONSTITUENTS, rc.DECISIONS, rc.REVIEW_LOG, D.DIVISOR_FILE, rc.latest_ranking)
    rc.CONSTITUENTS, rc.DECISIONS, rc.REVIEW_LOG = con, tmp / "dec.csv", tmp / "rev.csv"
    D.DIVISOR_FILE = tmp / "divisor.csv"
    rc.latest_ranking = lambda: ranking
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc.main(today=DAY, sessions=JAN, px=prices)
        out = buf.getvalue()
        rows = list(csv.DictReader(open(con)))
        div = D.divisor_on(DAY.isoformat())
        decisions = list(csv.reader(open(tmp / "dec.csv"))) if (tmp / "dec.csv").exists() else []
    finally:
        (rc.CONSTITUENTS, rc.DECISIONS, rc.REVIEW_LOG, D.DIVISOR_FILE, rc.latest_ranking) = saved
    return rows, div, decisions, out


def fixture():
    """Nineteen incumbents holding a book worth 100, one vacant seat, one qualified entrant
    and no removal anywhere — the January 2027 shape."""
    held = [f"T{i:02d}" for i in range(19)]
    incumbents = [{"ticker": t, "entity": t, "entry_date": "2026-07-20",
                   "entry_price": PRICE, "entry_weight": 1 / 19,
                   "units": 100.0 / 19 / PRICE, "status": "active", "exit_date": ""}
                  for t in held]
    ranking = [(t, t, 0.5) for t in held] + [("NEW", "NEW CO", 0.5)]
    prices = {t: (PRICE, PRICE * 0.5) for t, _, _ in ranking}   # all above their 200d MA
    return incumbents, ranking, prices, held


def test_vacancy_is_filled_and_the_level_does_not_jump():
    incumbents, ranking, prices, held = fixture()
    with tempfile.TemporaryDirectory() as tmp:
        rows, div, decisions, out = run_review(incumbents, ranking, prices, tmp)

    active = [r for r in rows if r["status"] == "active"]
    assert len(active) == 20, [r["ticker"] for r in active]
    assert "NEW" in {r["ticker"] for r in active}, out

    # 1. Level continuity. Before: 20 equal-scored names would each be 5%, so the entrant
    #    takes 1/20 and the basket grows by exactly that share.
    before = sum(float(r["units"]) * PRICE for r in incumbents) / 1.0
    after = sum(float(r["units"]) * PRICE for r in active) / div
    assert abs(after - before) < 1e-9, (before, after, div)

    # 2. Not one incumbent unit moved.
    was = {r["ticker"]: float(r["units"]) for r in incumbents}
    now = {r["ticker"]: float(r["units"]) for r in active if r["ticker"] in was}
    assert was == now, [k for k in was if was[k] != now[k]]

    # 3. The entrant really holds the weight the entry rule gives it: equal scores across 20
    #    names means 1/20, and the divisor moves by about 20/19.
    #
    #    Note the two different tolerances, and that the difference is the point. Units are
    #    stored to 8 decimals, so the WEIGHT and the DIVISOR carry that rounding (~1e-8).
    #    Continuity above does not, because the divisor is derived from the units actually
    #    written rather than from the pre-rounding ideal. Asserting 1e-9 on all three would
    #    have quietly forced the divisor back to the theoretical value and reintroduced the
    #    ~1e-7 jump in the level — the property that actually matters.
    new = next(r for r in active if r["ticker"] == "NEW")
    weight = float(new["units"]) * PRICE / sum(float(r["units"]) * PRICE for r in active)
    assert abs(weight - 0.05) < 1e-7, weight
    assert abs(div - 20 / 19) < 1e-7, div
    assert any(d and d[2] == "ADD_VACANCY" for d in decisions), decisions


def test_the_momentum_veto_still_outranks_the_vacancy():
    """A vacancy is not a licence to buy a falling knife. §7.2's veto comes first, the seat
    stays empty, the divisor does not move, and the shortfall is said out loud."""
    incumbents, ranking, prices, held = fixture()
    prices["NEW"] = (PRICE, PRICE * 1.5)          # below its 200d MA
    with tempfile.TemporaryDirectory() as tmp:
        rows, div, decisions, out = run_review(incumbents, ranking, prices, tmp)

    active = [r for r in rows if r["status"] == "active"]
    assert len(active) == 19, [r["ticker"] for r in active]
    assert div == 1.0, div
    assert "1 seat(s) unfilled" in out, out
    assert any(d and d[2] == "SKIP_MOMENTUM" for d in decisions), decisions


def test_a_full_book_moves_nothing():
    """No vacancy, no removal: the review happens, is recorded, and touches nothing."""
    incumbents, ranking, prices, held = fixture()
    incumbents.append({"ticker": "NEW", "entity": "NEW CO", "entry_date": "2026-07-20",
                       "entry_price": PRICE, "entry_weight": 0.0, "units": 0.01,
                       "status": "active", "exit_date": ""})
    with tempfile.TemporaryDirectory() as tmp:
        rows, div, decisions, out = run_review(incumbents, ranking, prices, tmp)

    assert len([r for r in rows if r["status"] == "active"]) == 20, out
    assert div == 1.0, div
    was = {r["ticker"]: float(r["units"]) for r in incumbents}
    now = {r["ticker"]: float(r["units"]) for r in rows}
    assert was == now, [k for k in was if was[k] != now[k]]


def test_published_levels_are_unaffected_before_any_adjustment():
    """The divisor is 1.0 from inception, so every level already published reproduces from
    sum(units x price) exactly as before. A divisor that quietly rewrote history would be a
    far worse defect than the empty seat it was introduced to fill."""
    rows = [r for r in csv.DictReader(open(ROOT / "data/ledger/constituents.csv"))
            if r["status"] == "active"]
    levels = list(csv.DictReader(open(ROOT / "data/ledger/index_level.csv")))
    for r in levels:
        assert D.divisor_on(r["date"]) == 1.0, (r["date"], D.divisor_on(r["date"]))
    assert len(rows) == 19, len(rows)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK: {name}")
    print("\nVacancy clause and divisor hold: level continuous, incumbent units untouched.")
