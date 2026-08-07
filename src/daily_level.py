"""
Compute the index level once per trading day and append it to the ledger.

Core mechanism: the ledger stores UNITS, not weights — this is what makes "never
rebalanced after entry" correct. At inception units_i = target_weight_i × 100 / entry_price_i;
thereafter
    level = sum(units_i × today's adjusted close)
Weights drift with price and winners grow on their own; not a single share is touched.

If inception_date is unset the script exits safely — nothing is recorded before inception.
"""
from __future__ import annotations
import sys, csv, pathlib, datetime as dt
from zoneinfo import ZoneInfo
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ET = ZoneInfo("America/New_York")

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEDGER = ROOT / "data/ledger"
CONSTITUENTS = LEDGER / "constituents.csv"
LEVELS = LEDGER / "index_level.csv"


def load_active():
    """Return [(ticker, units)] for active constituents only."""
    if not CONSTITUENTS.exists():
        return []
    rows = list(csv.DictReader(open(CONSTITUENTS)))
    return [(r["ticker"], float(r["units"])) for r in rows
            if r.get("status", "active") == "active"]


def bar_date(ts):
    """The US trading date a daily bar belongs to, as America/New_York."""
    ts = ts.to_pydatetime()
    if ts.tzinfo is not None:
        ts = ts.astimezone(ET)
    return ts.date().isoformat()


def fetch_closes(tickers):
    """Fetch the latest adjusted closes AND the trading date they belong to.

    The date is read off the bar, never off the clock. The runner is UTC, so any run
    after 20:00 New York falls on the next UTC day; taking the date from the clock
    mislabelled such a row (this happened on 2026-08-06, see ERRATA). The clock can
    only ever veto a date here, never supply one.

    If any close is unavailable, or the constituents do not all report the same last
    bar, nothing is recorded — a missing day beats a stale, estimated or mislabelled one.
    """
    import yfinance as yf
    out, dates = {}, {}
    data = yf.download(tickers, period="5d", auto_adjust=True,
                       progress=False, group_by="ticker")
    for t in tickers:
        try:
            s = data[t]["Close"].dropna() if len(tickers) > 1 else data["Close"].dropna()
            if len(s) == 0:
                return None, None, f"{t}: no price"
            out[t] = float(s.iloc[-1])
            dates[t] = bar_date(s.index[-1])
        except Exception as e:
            return None, None, f"{t}: price fetch failed: {e}"

    if len(set(dates.values())) > 1:
        spread = ", ".join(f"{t}={d}" for t, d in sorted(dates.items()))
        return None, None, f"constituents disagree on the last trading day ({spread})"

    date_str = next(iter(dates.values()))
    et_today = dt.datetime.now(ET).date().isoformat()
    if date_str > et_today:
        return None, None, (f"last bar is dated {date_str}, which is still in the future "
                            f"in New York ({et_today})")
    return out, date_str, None


def last_logged():
    """The most recent date already in the ledger, or None."""
    if not LEVELS.exists():
        return None
    dates = [r["date"] for r in csv.DictReader(open(LEVELS))]
    return max(dates) if dates else None


def main():
    if not cfg["meta"].get("inception_date"):
        print("Not yet at inception (config.meta.inception_date is empty) — nothing recorded, exiting normally.")
        return

    active = load_active()
    if not active:
        print("Ledger has no active constituents — exiting.")
        return

    # Fetch first: the date comes out of the data, so there is nothing to compare
    # against the ledger until the prices are in hand.
    closes, date_str, err = fetch_closes([t for t, _ in active])
    if closes is None:
        print(f"WARNING: nothing recorded: {err} (better a missing day than a stale or estimated price)")
        return

    prev = last_logged()
    if prev is not None and date_str <= prev:
        # Equal = today is already in. Earlier = the ledger is append-only and ordered,
        # so an out-of-order row is a bug somewhere upstream, not a backfill.
        verb = "already recorded" if date_str == prev else f"older than the last row ({prev})"
        print(f"{date_str} {verb}, skipping.")
        return

    level = sum(units * closes[t] for t, units in active)

    LEDGER.mkdir(parents=True, exist_ok=True)
    new = not LEVELS.exists()
    with open(LEVELS, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "level", "n_constituents"])
        w.writerow([date_str, round(level, 4), len(active)])
    print(f"{date_str} index level {level:.4f} ({len(active)} constituents)")


if __name__ == "__main__":
    main()
