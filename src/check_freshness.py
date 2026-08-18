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


def last_completed_from(res, now_et):
    """The pure half of the monitor: given a Yahoo chart payload and the current New York
    time, return the most recent COMPLETED US trading day as an ISO date string, or None.

    A date counts as a real session when SPY actually traded that day — volume above zero
    with an opening print. It deliberately does NOT test the close. On 2026-08-18 Yahoo
    served the 08-17 bar with open/high/low/volume all present and `close: null` for hours
    after the 16:00 close; that dropped an ordinary Monday out of the calendar and made the
    monitor denounce the ledger's honest 08-17 row as future-dated. `close is None` was
    carrying two meanings at once — "no session that day" and "the session happened but the
    field is not populated yet" — and only the first was ever intended. Weekends and
    holidays still fall out on their own: Yahoo emits no bar at all for them, so the
    calendar is still read off the data rather than off a wall clock.

    A session counts as completed once its day is over in New York, or it is today's bar
    after the 16:00 close (16:05 for slack), so an evening manual run does not mistake
    today's legitimate row for a future-dated one."""
    q = res["indicators"]["quote"][0]
    today = now_et.date().isoformat()
    after_close = (now_et.hour, now_et.minute) >= (16, 5)
    days = sorted(
        dt.datetime.fromtimestamp(t, ET).date().isoformat()
        for i, t in enumerate(res["timestamp"])
        if (q["volume"][i] or 0) > 0 and q["open"][i] is not None
    )
    done = [d for d in days if d < today or (d == today and after_close)]
    return done[-1] if done else None


def last_completed_trading_day():
    """Fetch SPY's daily bars and hand them to last_completed_from. Returns an ISO date
    string, or None if the source is unreachable after three attempts."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(SPY_URL, headers=UA), timeout=30) as r:
                res = json.load(r)["chart"]["result"][0]
            return last_completed_from(res, dt.datetime.now(ET))
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


def selftest():
    """Prove the calendar reader is alive before trusting it, and prove the 2026-08-18 fix
    did not simply loosen it. Most cases below are negative: things that must NOT be counted
    as a completed trading day. A monitor that only ever gets shown the happy path is not a
    monitor. Run with --selftest; exit 0 means every case matched."""
    def bar(date, volume, open_, close):
        ts = dt.datetime.fromisoformat(f"{date}T09:30").replace(tzinfo=ET).timestamp()
        return int(ts), volume, open_, close

    def payload(bars):
        return {"timestamp": [b[0] for b in bars],
                "indicators": {"quote": [{"volume": [b[1] for b in bars],
                                          "open":   [b[2] for b in bars],
                                          "close":  [b[3] for b in bars]}]}}

    def at(s):
        return dt.datetime.fromisoformat(s).replace(tzinfo=ET)

    NORMAL = [bar("2026-08-13", 31_000_000, 775.0, 777.88),
              bar("2026-08-14", 30_000_000, 777.0, 776.34)]

    cases = [
        # (name, bars, now_et, expected)
        ("+ 回归案 2026-08-18：周一 bar 的 close 是 null，但成交量 3328 万",
         NORMAL + [bar("2026-08-17", 33_285_717, 776.18, None)], at("2026-08-18T01:41"), "2026-08-17"),
        ("+ 正常日：close 有值照常算",
         NORMAL + [bar("2026-08-17", 33_285_717, 776.18, 772.67)], at("2026-08-18T01:41"), "2026-08-17"),
        ("+ 真·节假日：7/4 当天 Yahoo 根本没有 bar，应停在 7/3",
         [bar("2026-07-01", 3e7, 770.0, 771.0), bar("2026-07-02", 3e7, 771.0, 772.0),
          bar("2026-07-03", 2e7, 772.0, 773.0)], at("2026-07-06T09:00"), "2026-07-03"),
        ("+ 周末：周六凌晨跑，应停在周五",
         NORMAL, at("2026-08-15T03:00"), "2026-08-14"),
        ("+ 今天盘中不算完成：11:00 跑，今天那根不能算",
         NORMAL + [bar("2026-08-17", 3e7, 776.0, 772.67), bar("2026-08-18", 1e7, 773.0, None)],
         at("2026-08-18T11:00"), "2026-08-17"),
        ("+ 今天收盘后算完成：16:30 跑，今天那根算数",
         NORMAL + [bar("2026-08-17", 3e7, 776.0, 772.67), bar("2026-08-18", 3e7, 773.0, 775.0)],
         at("2026-08-18T16:30"), "2026-08-18"),
        ("- 负向：volume=0 的占位行不是交易日",
         NORMAL + [bar("2026-08-17", 0, 776.18, None)], at("2026-08-18T01:41"), "2026-08-14"),
        ("- 负向：volume=None 的占位行不是交易日",
         NORMAL + [bar("2026-08-17", None, 776.18, None)], at("2026-08-18T01:41"), "2026-08-14"),
        ("- 负向：有量但连开盘价都没有的残行不是交易日",
         NORMAL + [bar("2026-08-17", 33_285_717, None, None)], at("2026-08-18T01:41"), "2026-08-14"),
        ("- 负向：整个 payload 全是空行 ⇒ 测不了，必须返回 None（main 据此退 2，不许当绿灯）",
         [bar("2026-08-13", 0, None, None), bar("2026-08-14", None, None, None)],
         at("2026-08-18T01:41"), None),
    ]

    ok = 0
    for name, bars, now_et, want in cases:
        got = last_completed_from(payload(bars), now_et)
        mark = "✅" if got == want else "❌"
        if got == want:
            ok += 1
        print(f"  {mark} {name}\n       期望 {want} / 实得 {got}")
    neg = sum(1 for c in cases if c[0].startswith("-"))
    print(f"\n{'✅' if ok == len(cases) else '❌'} selftest {ok}/{len(cases)} 过"
          f"（负向 {neg} 正向 {len(cases) - neg}·计数实算非硬编码）")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
