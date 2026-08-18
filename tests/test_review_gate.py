"""Negative samples for the METHODOLOGY §7.1 review gate.

A gate that has never been shown to go red does not exist. Each test here feeds the gate
the exact situation it is supposed to catch and asserts that it catches it; several also
re-run the OLD predicate on the same sample to show it stayed green, so the regression
cannot quietly return.

The defect these pin down: the old guard inferred "this period already ran" from a side
effect — whether any ACTIVE constituent carried an entry_date in the current month. A review
that removed nothing and added nothing leaves no such trace, so the guard stayed open and
every remaining day of January became eligible to reconstitute. §7.1 says the FIRST TRADING
DAY. The event is now recorded as an event, not inferred from its outcome.

No network. Run: python -m tests.test_review_gate
"""
import csv, io, pathlib, sys, datetime as dt
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import src.reconstitute as rc

ROOT = pathlib.Path(__file__).resolve().parents[1]

# A plausible January 2027 session calendar: Jan 1 is a holiday, so the review day is Jan 4.
JAN_2027 = ["2026-12-30", "2026-12-31",
            "2027-01-04", "2027-01-05", "2027-01-06", "2027-01-07", "2027-01-08"]
FTD = "2027-01-04"
REVIEW_MONTHS = [1, 7]


def old_guard_says_already_ran(constituents, today):
    """The predicate as it stood before this fix, kept verbatim so the samples below can
    demonstrate it staying green where the new gate goes red."""
    return any(r["entry_date"][:7] == today.isoformat()[:7]
               for r in constituents if r["status"] == "active")


def test_gate_fires_only_on_the_first_trading_day():
    for day, expected in [("2027-01-01", "TOO_EARLY"),   # holiday, before the session
                          ("2027-01-04", "DUE"),          # the review day itself
                          ("2027-01-05", "MISSED"),       # one day late
                          ("2027-01-29", "MISSED")]:      # late in the month
        verdict, period, ftd = rc.review_due(
            dt.date.fromisoformat(day), JAN_2027, [], REVIEW_MONTHS)
        assert verdict == expected, (day, verdict, expected)
        assert period == "2027-01" and ftd == FTD, (period, ftd)

    verdict, _, _ = rc.review_due(dt.date(2026, 8, 17), JAN_2027, [], REVIEW_MONTHS)
    assert verdict == "NOT_REVIEW_MONTH", verdict


def test_a_review_that_changed_nothing_still_closes_the_gate():
    """THE regression. Nineteen incumbents, all entered at inception, nothing added or
    removed on review day: the register closes the gate, the old entry_date predicate does
    not."""
    unchanged = [{"ticker": t, "entry_date": "2026-07-20", "status": "active"}
                 for t in ("ADBE", "MO", "NTAP")]
    day_after = dt.date(2027, 1, 5)

    # Old predicate on this sample: green — nothing carries a 2027-01 entry_date, so the
    # whole rest of January stayed eligible to reconstitute on a later day's ranking.
    assert old_guard_says_already_ran(unchanged, day_after) is False

    # New gate, with the review recorded as the event it is: shut.
    register = [{"period": "2027-01", "review_date": FTD, "entered": "0", "removed": "0"}]
    verdict, _, _ = rc.review_due(day_after, JAN_2027, register, REVIEW_MONTHS)
    assert verdict == "ALREADY_DONE", verdict

    # And with no register entry it must NOT silently proceed on the wrong day either.
    verdict, _, _ = rc.review_due(day_after, JAN_2027, [], REVIEW_MONTHS)
    assert verdict == "MISSED", verdict


def test_a_removal_only_review_also_closes_the_gate():
    """Second face of the same defect: a review that dropped a name but funded no entrant
    also leaves no new entry_date behind."""
    after_a_drop = [{"ticker": "MO", "entry_date": "2026-07-20", "status": "active"},
                    {"ticker": "TTD", "entry_date": "2026-07-20", "status": "removed"}]
    day_after = dt.date(2027, 1, 5)
    assert old_guard_says_already_ran(after_a_drop, day_after) is False
    register = [{"period": "2027-01", "review_date": FTD, "entered": "0", "removed": "1"}]
    assert rc.review_due(day_after, JAN_2027, register, REVIEW_MONTHS)[0] == "ALREADY_DONE"


def test_a_blind_calendar_is_never_an_all_clear():
    """The exchange calendar is unreachable, or the payload does not reach into the review
    month. Either way the gate must refuse, not shrug."""
    assert rc.review_due(dt.date(2027, 1, 4), None, [], REVIEW_MONTHS)[0] == "BLIND"
    assert rc.review_due(dt.date(2027, 1, 4), ["2026-12-30"], [], REVIEW_MONTHS)[0] == "BLIND"


def test_first_trading_day_reads_the_exchange_calendar_not_the_clock():
    assert rc.first_trading_day(JAN_2027, 2027, 1) == FTD
    assert rc.first_trading_day(JAN_2027, 2027, 2) is None     # month absent -> blind, not "day 1"


def test_an_unfundable_vacancy_is_shouted_not_swallowed():
    """POLICY GAP, deliberately left failing-loud rather than silently fixed.

    ERRATA 2026-07-21 promises the seat FOXA burned at inception is refilled to N=20 at the
    January 2027 review. But entry is funded only by value a removal frees (§7.3). Give the
    reconstitution a review where every incumbent is retained — freed == 0 — and a qualified
    entrant waiting: the entrant cannot be funded. That is a mandate question, not a bug to
    paper over, so the requirement asserted here is that the run SAYS SO."""
    actives = [r for r in csv.DictReader(open(ROOT / "data/ledger/constituents.csv"))
               if r["status"] == "active"]
    held = [r["ticker"] for r in actives]
    assert len(held) == 19 and "GRMN" not in held, held

    # Ranking: the 19 incumbents comfortably inside the buffer, GRMN taking seat 20.
    ranking = [(t, t, 0.9 - i * 0.01) for i, t in enumerate(held)] + [("GRMN", "GARMIN LTD", 0.25)]
    # Every price above its 200d MA, so the momentum veto never fires and the only thing
    # that can stop GRMN is funding.
    px = {t: (100.0, 80.0) for t, _, _ in ranking}

    real_ranking = rc.latest_ranking
    rc.latest_ranking = lambda: ranking
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc.main(today=dt.date(2027, 1, 4), sessions=JAN_2027, px=px, dry_run=True)
        out = buf.getvalue()
    finally:
        rc.latest_ranking = real_ranking

    assert "retained 19" in out and "entered 0" in out, out
    assert "1 seat(s) unfilled" in out, out
    assert "GRMN" in out and "POLICY GAP" in out, out
    assert "[dry-run]" in out, out          # and it wrote nothing


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK: {name}")
    print("\nAll review-gate negative samples go red where they must.")
