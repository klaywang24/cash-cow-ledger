#!/usr/bin/env python3
"""Local fallback alarm for the daily index pipeline.

Why this exists
---------------
daily.yml is triggered by GitHub's `schedule` events, which are best-effort and
nothing more. Over 2026-08-26/27/28 the primary 21:30 UTC trigger slipped by
3.5h, then 8h, then failed to fire at all -- both the primary and the 23:30 UTC
backup live inside the same trading day, so a delay long enough to miss one
misses both. On 08-27 and 08-28 a human had to press the button by hand.

This script is that button, pressed by launchd instead of by a person. It runs
on the local machine, so it does not depend on GitHub's scheduler being awake.

Deliberate design choices
-------------------------
* It owns NO trading-calendar logic. Deciding what "the last completed trading
  day" means already has exactly one implementation (src/check_freshness.py,
  running in CI). A second copy here could disagree with the first, and two
  judges that disagree are worse than one. This script only compares the last
  recorded date against today's Eastern date and lets the pipeline itself
  decide whether there is anything to record. On a market holiday it dispatches,
  the pipeline says "already recorded, skipping", and nothing happens -- the
  wasted run is the price of not owning a second calendar.
* It never touches the local clone. The ledger is read over the GitHub API, so a
  dirty or half-rebased working tree cannot make this alarm misfire, and the
  alarm can never make the working tree dirty.
* It verifies the outcome instead of reporting the button-press. Dispatching is
  not the goal; a recorded row is. A run that fails is escalated to a desktop
  notification, because a fallback that fails silently is not a fallback.

Exit codes: 0 = nothing needed / row recorded. 1 = dispatched run failed, or the
ledger could not be read. Non-zero is meant to be noticed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

GH = "/opt/homebrew/bin/gh"
REPO = "klaywang24/cash-cow-ledger"
WORKFLOW = "daily.yml"
LEDGER_PATH = "data/ledger/index_level.csv"
ET = ZoneInfo("America/New_York")

# This is a fallback, so it must not race the thing it is backing up. GitHub's
# primary trigger is 17:30 ET and the pipeline normally commits by 17:55; the
# alarm stays out of the way until 18:00 ET and only then decides the primary has
# missed. Firing earlier would also be actively dangerous: the pipeline reads
# closing prices from yfinance, and a run started minutes after the 16:00 bell can
# see a preliminary print rather than the official close. A late row is an
# inconvenience; a wrong row is an error in the ledger, and the ledger is the
# whole asset. The guard is on Eastern time rather than the machine's clock, so it
# still holds if this laptop travels.
EARLIEST_ET_HOUR = 18

# The pipeline takes 1-4 minutes. Ten minutes is slack, not an expectation.
VERIFY_TIMEOUT_S = 600
VERIFY_POLL_S = 20


def log(msg: str) -> None:
    stamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    print(f"[{stamp}] {msg}", flush=True)


def gh(*args: str, check: bool = True) -> str:
    r = subprocess.run([GH, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed ({r.returncode}): {r.stderr.strip()}")
    return r.stdout


def notify(title: str, message: str) -> None:
    """Desktop notification. Best-effort: a missing notifier must not mask the
    real failure, so this never raises."""
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e",
             f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
            capture_output=True, timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        log(f"(notification failed, ignoring: {e})")


def last_recorded_date() -> str:
    """Last date in the committed ledger, read from origin over the API."""
    raw = gh("api", f"repos/{REPO}/contents/{LEDGER_PATH}",
             "-H", "Accept: application/vnd.github.raw")
    rows = [ln for ln in raw.splitlines() if ln.strip()]
    if len(rows) < 2:
        raise RuntimeError(f"ledger looks empty or malformed ({len(rows)} lines)")
    return rows[-1].split(",")[0].strip()


def latest_run() -> dict:
    out = gh("run", "list", "--repo", REPO, "--workflow", WORKFLOW,
             "--limit", "1", "--json", "databaseId,status,conclusion,createdAt")
    runs = json.loads(out)
    return runs[0] if runs else {}


def main() -> int:
    now = datetime.now(ET)
    today = now.strftime("%Y-%m-%d")

    if now.weekday() >= 5:
        log(f"{today} is a weekend in New York; nothing to do.")
        return 0

    if now.hour < EARLIEST_ET_HOUR:
        log(f"{now:%H:%M} ET is before the {EARLIEST_ET_HOUR}:00 close; too early to judge. Skipping.")
        return 0

    try:
        last = last_recorded_date()
    except Exception as e:  # noqa: BLE001
        log(f"FAULT: cannot read the ledger from origin: {e}")
        notify("Cash-cow fallback", f"Cannot read the ledger from origin: {e}")
        return 1

    if last == today:
        log(f"{today} already recorded on origin; GitHub's own schedule did its job. Nothing to do.")
        return 0

    log(f"ledger stops at {last}, today is {today} -> the scheduled run has not landed. Dispatching {WORKFLOW}.")
    before = latest_run().get("databaseId")
    gh("workflow", "run", WORKFLOW, "--repo", REPO)

    # Wait for a *new* run id, then for it to finish. Reading the previous run's
    # conclusion would report yesterday's success as today's.
    run = {}
    deadline = time.time() + VERIFY_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(VERIFY_POLL_S)
        r = latest_run()
        if r.get("databaseId") and r["databaseId"] != before:
            run = r
            if r.get("status") == "completed":
                break

    if not run:
        log("FAULT: dispatched, but no new run appeared within the timeout.")
        notify("Cash-cow fallback", "Dispatched daily.yml but no run appeared. Check Actions.")
        return 1

    rid = run["databaseId"]
    if run.get("status") != "completed":
        log(f"run {rid} still running at the timeout; not calling it good. Check Actions.")
        notify("Cash-cow fallback", f"Run {rid} still running after {VERIFY_TIMEOUT_S//60}m. Check Actions.")
        return 1

    if run.get("conclusion") != "success":
        log(f"FAULT: run {rid} finished as {run.get('conclusion')}.")
        notify("Cash-cow LEDGER FAULT", f"daily.yml run {rid} {run.get('conclusion')}. A human is needed.")
        return 1

    # Success is not the same as "a row was written" -- on a holiday the pipeline
    # succeeds and records nothing. Say which one actually happened.
    try:
        now_last = last_recorded_date()
    except Exception as e:  # noqa: BLE001
        log(f"run {rid} succeeded but the ledger could not be re-read: {e}")
        return 1

    if now_last == today:
        log(f"recovered: run {rid} recorded {today}.")
    else:
        log(f"run {rid} succeeded and recorded nothing; ledger still stops at {now_last}. "
            f"Expected on a market holiday.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
