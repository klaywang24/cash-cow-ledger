"""
Ledger freshness monitor — guarding against silent pipeline death.

This targets one specific failure mode: the pipeline reports SUCCESS while producing no
data at all. In that state every green light is false, and the gap goes unnoticed until
someone happens to open the ledger.

So the test is data against data: the ledger's last row against the most recent COMPLETED
US trading day, read off SPY's own daily bars. Neither side of the comparison comes from
a wall clock or from any workflow's self-report — a clock cannot know about holidays, and
a workflow's own status is exactly the thing this monitor exists to distrust. Run shortly
after midnight New York, it alerts the same night a trading day goes missing: staleness is
never allowed to exceed one day (2026-08-06: a nine-hour GitHub Actions incident swallowed
the daily cron, and the old calendar-days threshold would have stayed green for days).

Exit codes: 0 = fresh (or not yet at inception); 1 = ledger stale, a human is needed now;
2 = could not measure. 2 is deliberately non-zero: a monitor that cannot see and stays
quiet is indistinguishable from a green one.
"""
from __future__ import annotations
import sys, csv, json, time, pathlib, urllib.request, datetime as dt
from zoneinfo import ZoneInfo
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEVELS = ROOT / "data/ledger/index_level.csv"
ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (cash-cow-ledger freshness monitor)"}
SPY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=10d&interval=1d"


def last_completed_trading_day():
    """The most recent COMPLETED US trading day, read off SPY's daily bars. Weekends and
    holidays fall out automatically: SPY simply has no bar. A bar counts as completed if
    its day is over in New York, or it is today's bar after the 16:00 close (16:05 for
    slack) — so an evening manual run does not mistake today's legitimate row for a
    future-dated one. Returns an ISO date string, or None if the source is unreachable."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(SPY_URL, headers=UA), timeout=30) as r:
                res = json.load(r)["chart"]["result"][0]
            now_et = dt.datetime.now(ET)
            today = now_et.date().isoformat()
            after_close = (now_et.hour, now_et.minute) >= (16, 5)
            days = sorted(
                dt.datetime.fromtimestamp(t, ET).date().isoformat()
                for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"])
                if c is not None
            )
            done = [d for d in days if d < today or (d == today and after_close)]
            return done[-1] if done else None
        except Exception as e:
            print(f"  (attempt {attempt + 1}: SPY fetch failed: {e})")
            time.sleep(15)
    return None


def main():
    inception = cfg["meta"].get("inception_date")
    if not inception:
        print("No inception date set — nothing to monitor."); return 0

    expected = last_completed_trading_day()
    if expected is None:
        print("ALERT: could not determine the last completed trading day (price source "
              "unreachable after 3 attempts). The monitor is blind — this is a failure, "
              "not an all-clear.")
        return 2

    if expected < str(inception):
        print(f"No completed trading day since inception ({inception}) yet — nothing to monitor.")
        return 0

    if not LEVELS.exists():
        print(f"ALERT: past inception ({inception}) and the ledger file does not exist. "
              f"Inception most likely failed and nobody noticed.")
        return 1

    rows = list(csv.DictReader(open(LEVELS)))
    if not rows:
        print("ALERT: the ledger file exists but holds no records — the pipeline may be spinning idle.")
        return 1

    last = rows[-1]["date"]
    if last < expected:
        print(f"ALERT: the last completed US trading day is {expected} but the ledger stops "
              f"at {last} ({len(rows)} rows).\n   The pipeline may have died silently — a "
              f"workflow can keep reporting success while producing no data.\n   Check the "
              f"recent daily.yml runs immediately.")
        return 1
    if last > expected:
        # A row dated later than any completed trading day can only be mislabelled or fabricated.
        print(f"ALERT: the ledger's last row is dated {last}, but the last completed US trading "
              f"day is {expected}. A future-dated row is an identification error (see "
              f"METHODOLOGY §9.1 and ERRATA 2026-08-06).")
        return 1

    print(f"Ledger is fresh: last row {last} matches the last completed trading day ({len(rows)} rows).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
