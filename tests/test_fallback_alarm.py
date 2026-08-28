#!/usr/bin/env python3
"""Negative-sample tests for ops/fallback_dispatch.py.

An alarm that has only ever been observed staying quiet has not been shown to
work; it has been shown to be quiet. Every test here forces the condition the
alarm exists for and asserts it actually goes off -- and one asserts it stays
quiet when it should. No network, no dispatches: `gh` is stubbed.
"""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location(
    "fb", Path(__file__).resolve().parent.parent / "ops" / "fallback_dispatch.py")
fb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fb)

ET = ZoneInfo("America/New_York")
FAILURES = []


class Clock:
    """Freeze datetime.now(ET) at a chosen moment."""
    def __init__(self, when): self.when = when
    def now(self, tz=None): return self.when


def harness(now, ledger_last, run_seq, monkey_ledger_after=None):
    """Run main() with everything external stubbed. Returns (exit, calls, log)."""
    calls, lines = [], []
    state = {"n": 0}

    def fake_gh(*args, check=True):
        calls.append(args)
        if args[0] == "workflow":
            return ""
        raise AssertionError(f"unexpected gh call {args}")

    def fake_last():
        if calls and any(a[0] == "workflow" for a in calls) and monkey_ledger_after:
            return monkey_ledger_after
        return ledger_last

    def fake_latest():
        r = run_seq[min(state["n"], len(run_seq) - 1)]
        state["n"] += 1
        return r

    # A virtual clock: sleep() advances it instead of burning wall time, so the
    # "run never appears" case reaches its timeout instantly rather than in ten
    # real minutes. Patching the real time module would slow the test suite by
    # exactly the amount of patience the script is designed to have.
    class VirtualTime:
        def __init__(self): self.t = 0.0
        def time(self): return self.t
        def sleep(self, s): self.t += s

    fb.datetime = Clock(now)
    fb.gh = fake_gh
    fb.last_recorded_date = fake_last
    fb.latest_run = fake_latest
    fb.time = VirtualTime()
    fb.notify = lambda t, m: lines.append(f"NOTIFY: {t} | {m}")
    fb.log = lambda m: lines.append(m)
    code = fb.main()
    return code, calls, lines


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        FAILURES.append(name)


print("negative samples (the alarm must go off):")

# 1. Exactly today's situation before the manual rescue: ledger a day behind.
code, calls, lines = harness(
    now=datetime(2026, 8, 28, 18, 30, tzinfo=ET),
    ledger_last="2026-08-27",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"},
             {"databaseId": 2, "status": "completed", "conclusion": "success"}],
    monkey_ledger_after="2026-08-28",
)
check("stale ledger at 18:30 ET -> dispatches",
      any(a[0] == "workflow" for a in calls), f"calls={calls}")
check("stale ledger -> exits 0 once the row lands", code == 0, f"code={code}")
check("stale ledger -> log says recovered",
      any("recovered" in l for l in lines), lines)

# 2. Dispatched run fails: must escalate, never report a quiet success.
code, calls, lines = harness(
    now=datetime(2026, 8, 28, 18, 30, tzinfo=ET),
    ledger_last="2026-08-27",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"},
             {"databaseId": 9, "status": "completed", "conclusion": "failure"}],
)
check("failed run -> exit 1", code == 1, f"code={code}")
check("failed run -> desktop notification",
      any(l.startswith("NOTIFY:") for l in lines), lines)

# 3. Run never appears: silence must not be read as success.
code, calls, lines = harness(
    now=datetime(2026, 8, 28, 18, 30, tzinfo=ET),
    ledger_last="2026-08-27",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"}],
)
check("no new run -> exit 1", code == 1, f"code={code}")
check("no new run -> desktop notification",
      any(l.startswith("NOTIFY:") for l in lines), lines)

# 4. Holiday: run succeeds, records nothing. Not a fault, and must say so.
code, calls, lines = harness(
    now=datetime(2026, 11, 26, 18, 30, tzinfo=ET),   # Thanksgiving, a Thursday
    ledger_last="2026-11-25",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"},
             {"databaseId": 2, "status": "completed", "conclusion": "success"}],
)
check("holiday -> exit 0, not a fault", code == 0, f"code={code}")
check("holiday -> log names it as recording nothing",
      any("recorded nothing" in l for l in lines), lines)

print("positive samples (the alarm must stay quiet):")

# 5. Schedule worked: no dispatch at all.
code, calls, lines = harness(
    now=datetime(2026, 8, 28, 18, 30, tzinfo=ET),
    ledger_last="2026-08-28",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"}],
)
check("up-to-date ledger -> no dispatch", not calls, f"calls={calls}")
check("up-to-date ledger -> exit 0", code == 0, f"code={code}")

# 6. Weekend.
code, calls, lines = harness(
    now=datetime(2026, 8, 29, 18, 30, tzinfo=ET),    # Saturday
    ledger_last="2026-08-28",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"}],
)
check("Saturday -> no dispatch", not calls and code == 0, f"calls={calls} code={code}")

# 7. Before the close -- guard is on Eastern time, so a travelling laptop is safe.
code, calls, lines = harness(
    now=datetime(2026, 8, 28, 6, 30, tzinfo=ET),
    ledger_last="2026-08-27",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"}],
)
check("06:30 ET -> no dispatch", not calls and code == 0, f"calls={calls} code={code}")

# 8. 17:00 ET: the primary trigger has not even fired yet. A fallback that
#    dispatches here is not a fallback, it is a second primary racing the first --
#    and it would sample prices before the official close has settled.
code, calls, lines = harness(
    now=datetime(2026, 8, 28, 17, 0, tzinfo=ET),
    ledger_last="2026-08-27",
    run_seq=[{"databaseId": 1, "status": "completed", "conclusion": "success"}],
)
check("17:00 ET -> does not race the primary trigger",
      not calls and code == 0, f"calls={calls} code={code}")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all 14 assertions passed")
