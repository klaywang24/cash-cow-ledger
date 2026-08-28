# ops/ — local fallback alarm for the daily pipeline

## What broke

`daily.yml` is driven by GitHub `schedule` events. Those are best-effort, and in
late August 2026 they degraded three days running:

| trading day | primary due (21:30 UTC) | actually fired | how the row landed |
| --- | --- | --- | --- |
| 2026-08-25 | 21:30 UTC | 21:54 UTC | on its own |
| 2026-08-26 | 21:30 UTC | next day 00:59 UTC (+3.5h) | late, on its own |
| 2026-08-27 | 21:30 UTC | next day 05:34 UTC (+8h) | **manual dispatch** |
| 2026-08-28 | 21:30 UTC | never | **manual dispatch** |

The workflow already carries a backup trigger two hours after the primary, but
both live inside the same trading day: a delay long enough to swallow the first
swallows the second too. Both of the last two rows were recovered by a human
noticing the freshness-monitor email and pressing the button.

## What this is

`fallback_dispatch.py` is that button, pressed by `launchd` on this machine
rather than by a person. It does not replace GitHub's schedule; it notices when
the schedule failed to produce a row and dispatches the workflow itself.

Three design choices are load-bearing:

* **It owns no trading-calendar logic.** "What is the last completed trading
  day" already has exactly one implementation — `src/check_freshness.py`,
  running in CI. A second copy here could disagree with the first, and two
  judges that disagree are worse than one judge. This script only compares the
  last recorded date against today's Eastern date and lets the pipeline decide
  whether there is anything to record. On a market holiday it dispatches, the
  pipeline answers "already recorded, skipping", and nothing happens. The wasted
  run is the price of not owning a second calendar.
* **It waits until 18:00 ET.** A fallback that races its primary is not a
  fallback. It also must not sample prices minutes after the bell: the pipeline
  reads closes from yfinance, and an early run can catch a preliminary print. A
  late row is an inconvenience; a wrong row is an error in the ledger.
* **It verifies the row, not the button-press.** Dispatching is not the goal. It
  waits for the run it started, checks the conclusion, re-reads the ledger, and
  escalates to a desktop notification if the run failed or no run appeared. A
  fallback that fails silently is not a fallback.

It never touches the local clone — the ledger is read over the GitHub API — so a
dirty working tree cannot make the alarm misfire, and the alarm cannot dirty the
working tree.

## Schedule

`StartInterval` of 3600s (hourly), deliberately *not* `StartCalendarInterval`
pinned to a local wall-clock time. A pinned local time silently shifts into the
US trading day the moment this laptop changes timezone: the alarm would still be
loaded, still be "running", and never once reach its guard. Hourly is
timezone-independent; before 18:00 ET the script exits in a fraction of a second
after one API read.

## Install / reinstall

```bash
./ops/install_launchd.sh
```

Renders `com.klay.cashcow-fallback.plist.template` against wherever this
checkout actually lives, installs it to `~/Library/LaunchAgents`, and loads it.
Idempotent — re-run it after moving the checkout or editing the template.

The rendered plist is not committed: this repository is public and the checkout
sits under a private working directory. The template is the source of truth; the
installed plist is a build artefact.

`/opt/homebrew/bin/python3` is not a stylistic choice — Apple's command-line
python is blocked by TCC from reading `~/Documents`, which is where this checkout
lives.

## Checking on it

```bash
launchctl print gui/$(id -u)/com.klay.cashcow-fallback | grep -E "state|runs|last exit"
tail -20 ops/fallback.log
```

`ops/fallback.log` is gitignored. Exit 0 means nothing was needed or the row was
recorded; exit 1 means it tried and failed, and it will have raised a desktop
notification saying so.

## Tests

```bash
python3 -m tests.test_fallback_alarm   # from the repo root
```

14 assertions, no network. They live in `tests/` and run in CI on every
daily.yml invocation alongside the other regression invariants -- a guard nobody
runs is a guard that does not exist, which is the exact defect this repo already
fixed once for tests/test_dedup.py. Most of them are negative samples — an alarm only ever
observed staying quiet has been shown to be quiet, not to work — so they force
the stale ledger, the failed run, the run that never appears, and assert it
actually goes off each time.

## What this does not fix

GitHub's scheduler. The rows will keep landing late; this only guarantees they
land. The freshness monitor remains the independent check, and it is still the
thing that should be believed over any workflow's own self-report.
