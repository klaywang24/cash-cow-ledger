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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

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


def fetch_closes(tickers):
    """Fetch today's adjusted closes. If any is unavailable, return None and record nothing
    for the day (never substitute a stale price)."""
    import yfinance as yf
    out = {}
    data = yf.download(tickers, period="5d", auto_adjust=True,
                       progress=False, group_by="ticker")
    for t in tickers:
        try:
            s = data[t]["Close"].dropna() if len(tickers) > 1 else data["Close"].dropna()
            if len(s) == 0:
                return None, f"{t}: no price"
            out[t] = float(s.iloc[-1])
        except Exception as e:
            return None, f"{t}: price fetch failed: {e}"
    return out, None


def already_logged(date_str):
    if not LEVELS.exists():
        return False
    return any(r["date"] == date_str for r in csv.DictReader(open(LEVELS)))


def main():
    if not cfg["meta"].get("inception_date"):
        print("Not yet at inception (config.meta.inception_date is empty) — nothing recorded, exiting normally.")
        return

    active = load_active()
    if not active:
        print("Ledger has no active constituents — exiting.")
        return

    today = dt.date.today().isoformat()
    if already_logged(today):
        print(f"{today} already recorded, skipping.")
        return

    closes, err = fetch_closes([t for t, _ in active])
    if closes is None:
        print(f"WARNING: nothing recorded today: {err} (better a missing day than a stale or estimated price)")
        return

    level = sum(units * closes[t] for t, units in active)

    LEDGER.mkdir(parents=True, exist_ok=True)
    new = not LEVELS.exists()
    with open(LEVELS, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "level", "n_constituents"])
        w.writerow([today, round(level, 4), len(active)])
    print(f"{today} index level {level:.4f} ({len(active)} constituents)")


if __name__ == "__main__":
    main()
