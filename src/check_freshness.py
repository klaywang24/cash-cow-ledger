"""
Ledger freshness monitor — guarding against silent pipeline death.

This targets one specific failure mode: the workflow reports SUCCESS while producing no
data at all. In that state every green light is false, and the gap goes unnoticed until
someone happens to open the ledger.

So the test here is THE FRESHNESS OF THE LEDGER DATA ITSELF, never a workflow's own status
report. It fails with a NON-ZERO EXIT CODE, which triggers GitHub Actions' native failure
notification by email — rather than posting to a channel nobody reads.

Exit codes: 0 = fresh, or not yet at inception; 1 = ledger is stale and needs a human now.
"""
from __future__ import annotations
import sys, csv, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEVELS = ROOT / "data/ledger/index_level.csv"


def main():
    inception = cfg["meta"].get("inception_date")
    if not inception:
        print("No inception date set — nothing to monitor."); return 0

    today = dt.date.today()
    inc = dt.date.fromisoformat(str(inception))
    if today < inc:
        print(f"Before inception ({inception}) — nothing to monitor."); return 0

    max_stale = cfg.get("monitoring", {}).get("max_stale_days", 4)

    # On or after inception the ledger file must exist
    if not LEVELS.exists():
        days = (today - inc).days
        if days > max_stale:
            print(f"ALERT: {days} days past inception ({inception}) and the ledger file still "
                  f"does not exist.\n   Inception most likely failed and nobody noticed.")
            return 1
        print(f"Only {days} days past inception ({inception}) and the ledger is not yet created — not alerting.")
        return 0

    rows = list(csv.DictReader(open(LEVELS)))
    if not rows:
        print("ALERT: the ledger file exists but holds no records — the pipeline may be spinning idle.")
        return 1

    last = dt.date.fromisoformat(rows[-1]["date"])
    stale = (today - last).days
    if stale > max_stale:
        print(f"ALERT: the ledger has not updated for {stale} days (last row {last}, "
              f"threshold {max_stale} days).\n   {len(rows)} rows total. The pipeline may have "
              f"died silently — the workflow may keep reporting success while producing no data."
              f"\n   Check the recent daily.yml runs immediately.")
        return 1

    print(f"Ledger is fresh: last updated {last} ({stale} days ago), {len(rows)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
