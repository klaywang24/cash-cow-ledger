"""
Open the books: convert today's constituents from target weights into UNITS, lock them
into the ledger, and set the base level to 100.

Runs exactly once, when today >= inception_date and the ledger does not yet exist; on any
later day it exits immediately — opening the books is not a repeatable action.

Units mechanism: units_i = target_weight_i × 100 / entry_price_i. Thereafter
level = sum(units_i × today's price); weights drift freely with price and not a single
share is touched (METHODOLOGY §7.3, "never rebalanced after entry").
"""
from __future__ import annotations
import sys, csv, glob, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEDGER = ROOT / "data/ledger"
CONSTITUENTS = LEDGER / "constituents.csv"
LEVELS = LEDGER / "index_level.csv"


def main():
    inception = cfg["meta"].get("inception_date")
    if not inception:
        print("No inception date set — no action."); return
    today = dt.date.today()
    if today < dt.date.fromisoformat(str(inception)):
        print(f"Before inception ({inception}) — no action."); return
    if CONSTITUENTS.exists():
        print("Ledger already open — opening is a one-time action, skipping."); return

    # Take the constituents from the latest build_portfolio run (produced in the same workflow)
    files = sorted(glob.glob(str(ROOT / "output/book1_index_*.csv")))
    if not files:
        print("ERROR: no constituent file found; run run_screen + build_portfolio first."); sys.exit(1)
    rows = list(csv.DictReader(open(files[-1])))
    if not rows:
        print("ERROR: constituent file is empty, aborting."); sys.exit(1)

    tickers = [r["ticker"] for r in rows]
    import yfinance as yf
    data = yf.download(tickers, period="5d", auto_adjust=True,
                       progress=False, group_by="ticker")
    prices = {}
    for t in tickers:
        try:
            s = data[t]["Close"].dropna()
            prices[t] = float(s.iloc[-1])
        except Exception:
            print(f"ERROR: price fetch failed for {t} — aborting inception (better not to open than to open at an estimated price).")
            sys.exit(1)

    LEDGER.mkdir(parents=True, exist_ok=True)
    opened = today.isoformat()
    with open(CONSTITUENTS, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "entity", "entry_date", "entry_price",
                    "entry_weight", "units", "status", "exit_date"])
        for r in rows:
            wt = float(r["weight"]); px = prices[r["ticker"]]
            w.writerow([r["ticker"], r["entity"], opened, round(px, 6),
                        round(wt, 6), round(wt * 100.0 / px, 8), "active", ""])

    with open(LEVELS, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "level", "n_constituents"])
        w.writerow([opened, "100.0000", len(rows)])

    print(f"Books opened {opened}: {len(rows)} constituents, base level 100.0000")
    print("   The level is computed automatically each trading day; constituents change only on the first trading day of January and July.")


if __name__ == "__main__":
    main()
