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

Exit codes carry ATTRIBUTION, not just severity (METHODOLOGY §11):

  0 — fresh, or not yet at inception
  1 — THE LEDGER is wrong: stale tail, a gap in the middle, or a future-dated row.
      A human is needed now.
  2 — THE SOURCE is unreadable, so freshness cannot be established either way. Deliberately
      non-zero — a monitor that cannot see and stays quiet is indistinguishable from a green
      one — but it says nothing against the ledger.

Keeping 1 and 2 apart is the whole point. On 2026-08-18 this monitor twice reported a real
problem and aimed it at the wrong party, telling its reader an honest row was fabricated
when the truth was a vendor hole. A misattributed alert costs an hour and teaches the reader
to ignore the next one, which is exactly how a monitor dies.
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
# 3mo, not 10d: the window has to be wide enough to spot a MIDDLE gap in the ledger, not
# just a stale tail, and wide enough for the reconstitution calendar to find the first
# trading day of a review month. Same payload serves both — one definition of a session.
SPY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=3mo&interval=1d"


def sessions_from(res):
    """The exchange's trading calendar as the payload reports it: every date carrying a
    timestamp, ascending, weekends vetoed.

    THE DISCRIMINATOR IS THE TIMESTAMP, NOT ANY FIELD'S CONTENTS. Yahoo emits no bar at all
    for a weekend or a holiday — that is the one case where absence really does mean the
    session never happened. A timestamp whose fields are null means something entirely
    different: "a session existed on this date and I have nothing about it." Reading the
    second as the first is what broke the monitor twice in one day (see calendar_from).

    Weekday arithmetic is a property of the date itself, not a reading of the clock, so
    vetoing Saturdays and Sundays keeps the clock's veto-only role intact: it can strike a
    date out, it can never supply one.

    This is the single definition of "the exchange had a session that day" in this repo.
    The reconstitution calendar reads it to find the first trading day of a review month —
    where a hole must NOT shift the review date, because the session did happen."""
    out = []
    for t in res["timestamp"]:
        d = dt.datetime.fromtimestamp(t, ET).date()
        if d.weekday() < 5:
            out.append(d.isoformat())
    return sorted(out)


def calendar_from(res, now_et):
    """Read the calendar in THREE states, not two. Returns (traded, holes): both ascending
    ISO date lists, restricted to days already COMPLETED in New York.

        traded — the session happened and the payload carries its data
        holes  — the session happened and the payload carries NOTHING for it

    A date with no timestamp at all appears in neither: weekend or holiday.

    Why three. Twice on 2026-08-18 this monitor denounced an honest ledger row as
    future-dated, because a single signal was carrying two meanings and the code silently
    collapsed the one it could not name:

      01:41 UTC — Yahoo served 08-17 with volume/open present and `close: null`. The test
                  was `close is not None`, so an ordinary Monday fell out of the calendar.
                  Fixed (fb… 9d696b3) by testing volume/open instead.
      13:34 UTC — Yahoo served 08-17 with open, close AND volume all null. The new test
                  excluded it too. The fix had moved the ambiguity from one field to
                  another rather than killing the class.

    Both times the missing state was the same: "a session happened and I cannot see it."
    Collapsing that into "no session" makes the monitor accuse the ledger of fabricating a
    row, when the truth is the vendor has a hole. Attribution matters as much as detection:
    a wrongly-aimed alert costs an hour and teaches you to ignore the next one.

    A session counts as completed once its day is over in New York, or it is today's bar
    after the 16:00 close (16:05 for slack), so an evening manual run does not mistake
    today's legitimate row for a future-dated one."""
    q = res["indicators"]["quote"][0]
    today = now_et.date().isoformat()
    after_close = (now_et.hour, now_et.minute) >= (16, 5)
    by_date = {}
    for i, t in enumerate(res["timestamp"]):
        d = dt.datetime.fromtimestamp(t, ET).date()
        if d.weekday() >= 5:
            continue
        iso = d.isoformat()
        if not (iso < today or (iso == today and after_close)):
            continue                      # today, still trading — neither traded nor a hole
        has_data = (q["volume"][i] or 0) > 0 and q["open"][i] is not None
        by_date[iso] = by_date.get(iso, False) or has_data
    traded = sorted(d for d, ok in by_date.items() if ok)
    holes = sorted(d for d, ok in by_date.items() if not ok)
    return traded, holes


def last_completed_from(res, now_et):
    """Most recent COMPLETED trading day the payload actually carries data for, or None."""
    traded, _ = calendar_from(res, now_et)
    return traded[-1] if traded else None


def fetch_payload():
    """SPY's daily bars. None when the source is unreachable after three attempts — which is
    'I cannot see', never 'nothing happened'."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(SPY_URL, headers=UA), timeout=30) as r:
                return json.load(r)["chart"]["result"][0]
        except Exception as e:
            print(f"  (attempt {attempt + 1}: SPY fetch failed: {e})")
            time.sleep(15)
    return None


def last_completed_trading_day():
    res = fetch_payload()
    return last_completed_from(res, dt.datetime.now(ET)) if res else None


def verdict_from(traded, holes, have, last, inception):
    """The whole judgement, pure and testable. Returns (exit_code, label, info).

    The order of the tests is not cosmetic — each is placed where its conclusion still holds
    despite whatever the source got wrong:

      1. STALE   — a hole can only push `expected` EARLIER, never later, so `last < expected`
                   survives any amount of source damage. Safe to conclude first.
      2. GAP     — a completed day the source DOES carry, with no ledger row behind it. Only
                   asked once the tail is known current, because a stale tail would report
                   every recent day as missing and bury the real signal.
      3. BLIND   — a hole after `expected` means the true last trading day may be later than
                   measured, so nothing beyond `expected` can be judged at all. This must be
                   asked BEFORE future-dating, or an honest row gets denounced (2026-08-18).
      4. FUTURE  — only now, with the calendar trustworthy right up to `expected`, does a
                   later row have no innocent explanation left.

    Exactly one of these is a source fault and it is the only one that returns 2. Detection
    and attribution are separate jobs: getting the first right and the second wrong still
    costs an hour and still teaches the reader to ignore the next alert."""
    expected = traded[-1]
    if last < expected:
        return 1, "STALE", {"expected": expected}
    missing = [d for d in traded if d > str(inception) and d not in have]
    if missing:
        return 1, "GAP", {"missing": missing}
    blinding = [h for h in holes if h > expected]
    if blinding:
        return 2, "BLIND_HOLE", {"blinding": blinding, "expected": expected}
    if last > expected:
        return 1, "FUTURE_DATED", {"expected": expected}
    return 0, "FRESH", {"expected": expected, "holes": holes}


def main():
    inception = cfg["meta"].get("inception_date")
    if not inception:
        print("No inception date set — nothing to monitor."); return 0

    res = fetch_payload()
    if res is None:
        print("BLIND (source): the price source was unreachable after 3 attempts, so the "
              "trading calendar could not be read.\n   This is a failure, not an all-clear — "
              "but the fault is the SOURCE, not the ledger. Nothing here says the ledger is wrong.")
        return 2

    traded, holes = calendar_from(res, dt.datetime.now(ET))
    if not traded:
        print("BLIND (source): the payload carries no populated session at all.\n"
              "   The monitor cannot see; it is not reporting that nothing happened.")
        return 2
    expected = traded[-1]

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

    have = {r["date"] for r in rows}
    last = rows[-1]["date"]
    code, label, info = verdict_from(traded, holes, have, last, inception)

    if label == "STALE":
        print(f"STALE (ledger): the last completed US trading day is {info['expected']} but the "
              f"ledger stops at {last} ({len(rows)} rows).\n   The pipeline may have died "
              f"silently — a workflow can keep reporting success while producing no data.\n"
              f"   Check the recent daily.yml runs immediately.")
    elif label == "GAP":
        print(f"GAP (ledger): {len(info['missing'])} completed trading day(s) inside the payload "
              f"window have no ledger row: {', '.join(info['missing'])}.\n   The ledger's tail is "
              f"current, so this was never going to show up in a last-row check. Each missing day "
              f"needs a deliberate backfill decision — see METHODOLOGY §9.1; do not let a later "
              f"run paper over it.")
    elif label == "BLIND_HOLE":
        print(f"BLIND (source): the price source has no data for {', '.join(info['blinding'])}, "
              f"which is after the last day it does carry ({info['expected']}).\n   The exchange "
              f"traded on those dates — the payload has their timestamps and null fields — so the "
              f"calendar cannot be trusted past {info['expected']} and freshness is UNVERIFIABLE "
              f"right now.\n   The ledger's last row is {last}. NOTHING HERE SAYS IT IS WRONG; "
              f"the fault is the vendor's. Re-run once the source backfills.")
    elif label == "FUTURE_DATED":
        print(f"FUTURE-DATED (ledger): the last row is dated {last}, but the last completed US "
              f"trading day is {info['expected']} and the source has no hole after it. This is an "
              f"identification error (see METHODOLOGY §9.1 and ERRATA 2026-08-06).")
    else:
        note = (f"  (source holes at {', '.join(info['holes'])}, already in the ledger)"
                if info["holes"] else "")
        print(f"Ledger is fresh: last row {last} matches the last completed trading day "
              f"({len(rows)} rows).{note}")
    return code


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
        # These three stay as they are, and the reason is worth stating: they were never
        # wrong. last_completed_from answers "the last day the payload CARRIES DATA for",
        # and for a null bar that answer really is 08-14. The 2026-08-18 defect was that
        # main() took this answer to a different question — "what was the last trading
        # day?" — and then accused the ledger with it. The function name asserted more than
        # the function knew. Hence the verdict cases below, which test what is DONE with
        # this answer, not just the answer.
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
    print(f"\n{'✅' if ok == len(cases) else '❌'} 日历判读 {ok}/{len(cases)} 过"
          f"（负向 {neg} 正向 {len(cases) - neg}·计数实算非硬编码）")

    # ---- Part two: three-state calendar, and what the VERDICT does with each state ----
    print("\n三态日历（有交易且有数据 / 有交易但源无数据 / 根本没开市）:")
    cal = [
        ("+ 空 bar 是「洞」，不是「没开市」——08-17 必须落进 holes，不是消失",
         NORMAL + [bar("2026-08-17", None, None, None)], at("2026-08-18T09:34"),
         (["2026-08-13", "2026-08-14"], ["2026-08-17"])),
        ("+ 周末根本没有 timestamp ⇒ 既不在 traded 也不在 holes",
         NORMAL, at("2026-08-15T03:00"), (["2026-08-13", "2026-08-14"], [])),
        ("- 负向：周六居然带了 timestamp（源发疯）⇒ 星期几否决，绝不算 session",
         NORMAL + [bar("2026-08-15", 3e7, 776.0, 777.0)], at("2026-08-17T03:00"),
         (["2026-08-13", "2026-08-14"], [])),
        ("+ 今天盘中：既不算 traded 也不算 hole（还没结束，不是缺数据）",
         NORMAL + [bar("2026-08-17", None, None, None), bar("2026-08-18", 3e6, 776.1, 769.0)],
         at("2026-08-18T09:34"), (["2026-08-13", "2026-08-14"], ["2026-08-17"])),
    ]
    for name, bars, now_et, want in cal:
        got = calendar_from(payload(bars), now_et)
        got = (list(got[0]), list(got[1]))
        mark = "✅" if got == want else "❌"
        ok += got == want
        print(f"  {mark} {name}\n       期望 {want}\n       实得 {got}")

    print("\n裁决（检测对了还不够——归属必须对，否则等于诬告台账）:")
    INC = "2026-07-20"
    verdicts = [
        ("🔴 2026-08-18 事故重演：源对 08-17 有洞，台账 08-17 诚实"
         " ⇒ 必须 BLIND 退 2 指向源，绝不能 FUTURE_DATED 退 1 指控台账",
         (["2026-08-14"], ["2026-08-17"]), {"2026-08-14", "2026-08-17"}, "2026-08-17",
         (2, "BLIND_HOLE")),
        ("+ 真·未来日期造假：源无洞，台账多出一行 ⇒ FUTURE_DATED 退 1（ERRATA 08-06 那道闸不许丢）",
         (["2026-08-14"], []), {"2026-08-14", "2026-08-17"}, "2026-08-17", (1, "FUTURE_DATED")),
        ("+ 真·陈旧：管线静默死亡 ⇒ STALE 退 1",
         (["2026-08-14", "2026-08-17"], []), {"2026-08-14"}, "2026-08-14", (1, "STALE")),
        ("+ 陈旧的判断不受洞影响：洞只会让 expected 更早，last<expected 照样成立",
         (["2026-08-14"], ["2026-08-17"]), {"2026-08-13"}, "2026-08-13", (1, "STALE")),
        ("🔴 中间缺一天：尾巴是新的，旧闸只看最后一行 ⇒ 必须 GAP 退 1",
         (["2026-08-13", "2026-08-14", "2026-08-17"], []), {"2026-08-13", "2026-08-17"},
         "2026-08-17", (1, "GAP")),
        ("+ 洞在 expected 之前且台账已有该行 ⇒ 不是问题，FRESH 退 0",
         (["2026-08-17"], ["2026-08-13"]), {"2026-08-13", "2026-08-17"}, "2026-08-17",
         (0, "FRESH")),
        ("+ 开账日之前的交易日不算缺口（台账那时还不存在）",
         (["2026-07-15", "2026-08-17"], []), {"2026-08-17"}, "2026-08-17", (0, "FRESH")),
    ]
    for name, (traded, holes), have, last, want in verdicts:
        code, label, _ = verdict_from(traded, holes, have, last, INC)
        got = (code, label)
        mark = "✅" if got == want else "❌"
        ok += got == want
        print(f"  {mark} {name}\n       期望 {want} / 实得 {got}")

    total = len(cases) + len(cal) + len(verdicts)
    print(f"\n{'✅' if ok == total else '❌'} selftest {ok}/{total} 过")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
