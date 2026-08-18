"""
Semi-annual reconstitution (METHODOLOGY §7). 100% mechanical: no human confirmation step.

Core invariant: AN INCUMBENT'S UNITS ARE NEVER TRIMMED BY A RECONSTITUTION. Retained names
carry their units through untouched — this is how "never rebalanced after entry, let winners
drift" is implemented. Only removed names are sold, and the market value they release funds
the entrants; any remainder is distributed pro rata across incumbents (pro-rata distribution
preserves the relative weights among incumbents, so it does not violate "never trim winners").

Rules:
  - Removal: rank > 2N (40), or no longer passing L2/L3/L4
  - Momentum veto: a name due for removal whose price is still above its 200d MA is deferred
    one review period (once only)
  - Entry: rank <= N (20), and price above the 200d MA
  - Entrant weights are allocated in proportion to composite score, capped at 8% of post-
    reconstitution total market value
"""
from __future__ import annotations
import sys, csv, glob, pathlib, datetime as dt
from zoneinfo import ZoneInfo
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.screen import dedup_dual_class
from src.divisor import divisor_on, record as record_divisor

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEDGER = ROOT / "data/ledger"
CONSTITUENTS = LEDGER / "constituents.csv"
DECISIONS = ROOT / "data/decisions_log.csv"
REVIEW_LOG = ROOT / "data/ledger/review_log.csv"
REVIEW_FIELDS = ["period", "review_date", "executed_at",
                 "retained", "removed", "entered", "turnover", "note"]
ET = ZoneInfo("America/New_York")

DRY_RUN = False   # set by main(); when true nothing on disk is touched

FIELDS = ["ticker", "entity", "entry_date", "entry_price", "entry_weight",
          "units", "status", "exit_date", "deferred_since"]


# ---------- helpers ----------
def ma200(ticker: str, closes) -> float | None:
    """200-day moving average. Returns None with fewer than 200 bars, in which case the
    momentum gate counts as unavailable and no entry is allowed."""
    s = closes.dropna()
    if len(s) < 200:
        return None
    return float(s.tail(200).mean())


def fetch_history(tickers: list[str]):
    """Return {ticker: (latest close, 200d MA)}; a ticker that cannot be resolved maps to (None, None)."""
    import yfinance as yf
    out = {}
    data = yf.download(tickers, period="18mo", auto_adjust=True,
                       progress=False, group_by="ticker")
    for t in tickers:
        try:
            s = (data[t]["Close"] if len(tickers) > 1 else data["Close"]).dropna()
            out[t] = (float(s.iloc[-1]), ma200(t, s)) if len(s) else (None, None)
        except Exception:
            out[t] = (None, None)
    return out


def load_constituents():
    if not CONSTITUENTS.exists():
        return []
    return list(csv.DictReader(open(CONSTITUENTS)))


def latest_ranking():
    """Read the latest run_screen candidates (already sorted by descending score).
    Returns [(ticker, entity, score)]."""
    files = sorted(glob.glob(str(ROOT / "output/candidates_*.csv")))
    if not files:
        return []
    rows = list(csv.DictReader(open(files[-1])))
    return [(r["ticker"], r["entity"], float(r["score"])) for r in dedup_dual_class(rows)]


def cap_and_redistribute(weights: dict, cap: float) -> dict:
    w = dict(weights)
    for _ in range(100):
        over = [k for k, v in w.items() if v > cap + 1e-12]
        if not over:
            break
        excess = sum(w[k] - cap for k in over)
        for k in over:
            w[k] = cap
        under = [k for k, v in w.items() if v < cap - 1e-12]
        if not under:
            break
        tot = sum(w[k] for k in under)
        for k in under:
            w[k] += excess * (w[k] / tot)
    return w


def log_decision(date, ticker, action, rank, price, reason):
    if DRY_RUN:
        print(f"  [dry-run] {action:14s} {ticker:6s} rank={rank} px={price}  {reason}")
        return
    with open(DECISIONS, "a", newline="") as f:
        csv.writer(f).writerow([date, ticker, action, rank, price, reason])


def load_review_log():
    if not REVIEW_LOG.exists():
        return []
    return list(csv.DictReader(open(REVIEW_LOG)))


def record_review(period, review_date, executed_at, retained, removed, entered, turnover, note=""):
    """A review that changed nothing is still a review, and gets recorded as one.

    The previous guard inferred "this period already ran" from a side effect: whether any
    active constituent carried an entry_date in the current month. A review that added
    nothing — because nothing was removed to fund an entry, or because the ranking was
    unchanged — leaves no such trace, so the guard stayed open and every remaining day of
    the review month became eligible. METHODOLOGY §7.1 says the FIRST TRADING DAY; the code
    said any day in January. Recording the EVENT instead of inferring it from its OUTCOME
    is what closes that hole, and the register doubles as the audit trail of every review
    ever held."""
    if DRY_RUN:
        print(f"  [dry-run] would record review {period} ({review_date}): "
              f"retained {retained} · removed {removed} · entered {entered}")
        return
    fresh = not REVIEW_LOG.exists()
    with open(REVIEW_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(REVIEW_FIELDS)
        w.writerow([period, review_date, executed_at, retained, removed, entered,
                    f"{turnover:.6f}", note])


def fetch_sessions():
    """The exchange's own trading calendar, read off SPY's daily bars — never off a wall
    clock, which cannot know about holidays (ERRATA 2026-08-06).

    One fetch and one definition of "a session happened", shared with the freshness monitor,
    so the two can never drift into disagreeing about what a trading day is. Note this wants
    sessions_from, NOT the monitor's populated-day list: if the source has a hole on the
    first trading day of a review month, the session still happened and the review date must
    not slide to the next day the vendor managed to populate."""
    from src.check_freshness import fetch_payload, sessions_from
    res = fetch_payload()
    return sessions_from(res) if res else None


def first_trading_day(sessions, year, month):
    """First real session of that month. None when the payload does not reach back into the
    month at all — a calendar that cannot see must never be read as 'today is not it'."""
    prefix = f"{year:04d}-{month:02d}-"
    days = [d for d in sessions if d.startswith(prefix)]
    return days[0] if days else None


def review_due(today, sessions, register, review_months):
    """Pure gate for METHODOLOGY §7.1. Returns (verdict, period, review_date).

    verdict ∈ {DUE, NOT_REVIEW_MONTH, ALREADY_DONE, TOO_EARLY, MISSED, BLIND}.
    MISSED and BLIND are failures to be shouted, never skipped: a review that silently did
    not happen is indistinguishable from one that happened and changed nothing, and that
    ambiguity is exactly what the register exists to destroy."""
    period = f"{today.year:04d}-{today.month:02d}"
    if today.month not in review_months:
        return ("NOT_REVIEW_MONTH", period, None)
    if sessions is None:
        return ("BLIND", period, None)
    ftd = first_trading_day(sessions, today.year, today.month)
    if ftd is None:
        return ("BLIND", period, None)
    if any(r["period"] == period for r in register):
        return ("ALREADY_DONE", period, ftd)
    iso = today.isoformat()
    if iso < ftd:
        return ("TOO_EARLY", period, ftd)
    if iso > ftd:
        return ("MISSED", period, ftd)
    return ("DUE", period, ftd)


# ---------- main ----------
def main(today=None, sessions=None, px=None, dry_run=False):
    global DRY_RUN
    DRY_RUN = dry_run
    R = cfg["rules"]
    N = cfg["L5_count"]["target_holdings"]
    enter_rank, exit_rank = R["buffer_enter_rank"], R["buffer_exit_rank"]
    cap = R["entry_weight_cap"]
    # US-Eastern, pinned: the runner's own zone is UTC and crosses midnight four hours
    # early — see ERRATA 2026-08-06. The clock only ever has veto power here; which day is
    # the review day is read off the exchange calendar, not off the clock.
    if today is None:
        today = dt.datetime.now(ET).date()

    cur = load_constituents()
    if not cur:
        print("Ledger not yet open — reconstitution does not apply."); return 0

    # §7.1 gate. Cheap month test first so ordinary days never touch the network.
    if today.month not in R["review_months"]:
        print(f"Month {today.month} is not a review month ({R['review_months']}) — constituents unchanged.")
        return 0
    if sessions is None:
        sessions = fetch_sessions()
    verdict, period, ftd = review_due(today, sessions, load_review_log(), R["review_months"])

    if verdict == "ALREADY_DONE":
        print(f"Review {period} already held on {ftd} (see {REVIEW_LOG.name}) — "
              f"a review is a once-per-period event, skipping."); return 0
    if verdict == "TOO_EARLY":
        print(f"Review {period} falls on {ftd}; today is {today} — not yet."); return 0
    if verdict == "BLIND":
        print("ERROR: could not read the exchange calendar, so the review date is unknown. "
              "Aborting — a blind gate must never be read as 'not today'."); return 2
    if verdict == "MISSED":
        print(f"ERROR: review {period} was due on {ftd} and never ran (nothing recorded in "
              f"{REVIEW_LOG.name}); today is already {today}.\n"
              f"   §7.1 pins the review to the first trading day, so this run must NOT quietly "
              f"reconstitute on a later day's ranking.\n"
              f"   A human decision is required — see the missed-review policy gap flagged "
              f"alongside this gate."); return 1

    ranking = latest_ranking()
    if not ranking:
        print("ERROR: no candidate list found; run run_screen first — aborting (no guessing, no relaxing)."); sys.exit(1)
    rank_of = {t: i + 1 for i, (t, _, _) in enumerate(ranking)}
    score_of = {t: s for t, _, s in ranking}
    entity_of = {t: e for t, e, _ in ranking}

    active = [r for r in cur if r["status"] == "active"]
    universe = sorted(set([r["ticker"] for r in active] + list(rank_of)))
    if px is None:
        px = fetch_history(universe)

    missing = [t for t in [r["ticker"] for r in active] if px.get(t, (None,))[0] is None]
    if missing:
        print(f"ERROR: price fetch failed for incumbents {missing} — aborting reconstitution (better no change than an estimated price).")
        sys.exit(1)

    # ---- 1. Decide retain / remove / defer ----
    retain, exits = [], []
    for r in active:
        t = r["ticker"]
        rk = rank_of.get(t)
        price, ma = px[t]
        if rk is not None and rk <= exit_rank:
            r["deferred_since"] = ""          # back inside the buffer: clear any deferral flag
            retain.append(r); continue
        # Removal condition triggered
        why = f"rank {rk} > {exit_rank}" if rk else "no longer passes L2/L3/L4"
        if ma is not None and price > ma and not r.get("deferred_since"):
            r["deferred_since"] = today.isoformat()
            retain.append(r)
            log_decision(today, t, "DEFER", rk or "", round(price, 4),
                         f"{why}, but price is above the 200d MA -> deferred one review period (rule-driven, not discretionary)")
        else:
            exits.append((r, price, why))

    # ---- 2. Portfolio value and value held by retained names ----
    total_value = sum(float(r["units"]) * px[r["ticker"]][0] for r in active)
    freed = sum(float(r["units"]) * p for r, p, _ in exits)

    # ---- 3. Select entrants (rank <= N, passes the momentum gate, not currently held) ----
    held = {r["ticker"] for r in retain}
    vacancies = max(0, N - len(retain))
    entrants = []
    for t, _, _ in ranking:
        if len(entrants) >= vacancies:
            break
        if t in held or rank_of[t] > enter_rank:
            continue
        price, ma = px.get(t, (None, None))
        if price is None:
            continue
        if ma is None or price <= ma:          # momentum veto: no falling knives; insufficient history also blocks
            log_decision(today, t, "SKIP_MOMENTUM", rank_of[t],
                         round(price, 4) if price else "",
                         "rank qualifies but price is not above the 200d MA (or fewer than 200 bars) -> no purchase this period")
            continue
        entrants.append(t)

    # ---- 4. Allocate: entrants take the freed value in proportion to score, capped at 8% of
    #         total value; any remainder goes pro rata to incumbents ----
    new_rows = []
    if entrants and freed > 0:
        raw = {t: score_of[t] for t in entrants}
        tot = sum(raw.values())
        target_w = cap_and_redistribute(
            {t: (v / tot) * (freed / total_value) for t, v in raw.items()}, cap)
        for t in entrants:
            alloc = target_w[t] * total_value
            price = px[t][0]
            new_rows.append({
                "ticker": t, "entity": entity_of[t], "entry_date": today.isoformat(),
                "entry_price": round(price, 6), "entry_weight": round(target_w[t], 6),
                "units": round(alloc / price, 8), "status": "active",
                "exit_date": "", "deferred_since": ""})
            log_decision(today, t, "ADD", rank_of[t], round(price, 4),
                         f"rank {rank_of[t]} <= {enter_rank} and above the 200d MA; score-allocated {target_w[t]*100:.2f}%")
        leftover = freed - sum(r["units"] * px[r["ticker"]][0] for r in new_rows)
    else:
        leftover = freed

    # ---- 4b. §7.3 vacancy clause: a seat with no removal behind it, funded by the divisor.
    #
    # §7.3 funds an entrant out of the value a REMOVAL releases. A seat can also fall vacant
    # with nothing removed — the one FOXA burned at inception is exactly that, and ERRATA
    # 2026-07-21 promises it is refilled to N at the January 2027 review. Without this clause
    # the promise cannot be kept: freed == 0 means the branch above never runs, and the seat
    # stays empty forever, silently.
    #
    # The entrant takes the weight the ENTRY RULE ALREADY GIVES IT — its score share of the
    # post-entry constituents, capped at entry_weight_cap. No new parameter is introduced:
    # the seat is empty because of a defect, and a defect must not get to edit the mandate.
    #
    # The value is created by moving the divisor, not by trimming anybody. Pro-rata dilution
    # of incumbent units yields identical weights with less machinery, and is rejected for
    # one reason: it trims incumbent units, and "a reconstitution never trims an incumbent's
    # units" is an invariant this repository enforces and audits. See src/divisor.py.
    divisor_before = divisor_on(today.isoformat())
    divisor_after = divisor_before
    if entrants and freed <= 0:
        incumbent_value = sum(float(r["units"]) * px[r["ticker"]][0] for r in retain)
        post = [r["ticker"] for r in retain] + entrants
        denom = sum(score_of[t] for t in post if t in score_of)
        if denom <= 0:
            print("ERROR: nothing scored to weight a vacancy entry against — aborting "
                  "(no guessing, no relaxing)."); sys.exit(1)
        w = {t: min(score_of[t] / denom, cap) for t in entrants}
        W = sum(w.values())
        if not (0 < W < 1):
            print(f"ERROR: vacancy entry weights sum to {W:.4f}, outside (0,1) — aborting.")
            sys.exit(1)
        needed = W / (1 - W) * incumbent_value     # so entrants hold exactly W afterwards
        for t in entrants:
            price = px[t][0]
            alloc = (w[t] / W) * needed
            new_rows.append({
                "ticker": t, "entity": entity_of[t], "entry_date": today.isoformat(),
                "entry_price": round(price, 6), "entry_weight": round(w[t], 6),
                "units": round(alloc / price, 8), "status": "active",
                "exit_date": "", "deferred_since": ""})
            log_decision(today, t, "ADD_VACANCY", rank_of[t], round(price, 4),
                         f"rank {rank_of[t]} <= {enter_rank} and above the 200d MA; no removal "
                         f"funded it, so the §7.3 vacancy clause applies: score-allocated "
                         f"{w[t]*100:.2f}%, absorbed by the divisor, no incumbent units touched")
        # Level continuity: the basket grew, and that is not a return. Scale the divisor by
        # exactly the factor the basket grew by, so the published level does not jump on the
        # review date. Incumbent units are untouched; incumbent PERCENTAGE weights fall by
        # (1 - W), which is unavoidable and was never what §7.3 promised.
        #
        # Derived from the units ACTUALLY WRITTEN, not from `needed`: units are stored to 8
        # decimals, and a divisor computed from the pre-rounding ideal leaves the level off by
        # the rounding error (~1e-7 in testing). Small, but continuity is the one property
        # this mechanism exists to provide, so it is made exact rather than nearly exact.
        added = sum(r["units"] * px[r["ticker"]][0] for r in new_rows)
        divisor_after = divisor_before * (incumbent_value + added) / incumbent_value
        if not DRY_RUN:
            record_divisor(today.isoformat(), divisor_after,
                           f"§7.3 vacancy clause: {'/'.join(entrants)} entered at "
                           f"{W*100:.2f}% with no removal to fund it")
        print(f"Divisor {divisor_before:.10f} -> {divisor_after:.10f} "
              f"(entrants take {W*100:.2f}% of the post-entry book; incumbent units untouched)")

    # Remainder distributed pro rata to incumbents (relative weights preserved -> winners not trimmed)
    if leftover > 1e-9 and retain:
        held_val = sum(float(r["units"]) * px[r["ticker"]][0] for r in retain)
        if held_val > 0:
            for r in retain:
                r["units"] = round(float(r["units"]) * (1 + leftover / held_val), 8)

    # ---- 5. Persist ----
    for r, price, why in exits:
        r["status"] = "removed"; r["exit_date"] = today.isoformat()
        log_decision(today, r["ticker"], "DROP", rank_of.get(r["ticker"], ""),
                     round(price, 4), f"{why} (rule-driven, not discretionary)")

    # `cur` already holds every historical row (active + previously removed), and retain/exits
    # are in-place references into it, so the status and unit edits above are already applied.
    # Write cur + entrants: every row exactly once.
    # (An earlier version wrote removed_before + exits + retain + new_rows, which duplicated
    # this period's removals.)
    out = cur + new_rows
    if not DRY_RUN:
        with open(CONSTITUENTS, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in out:
                w.writerow({k: r.get(k, "") for k in FIELDS})

    turnover = freed / total_value if total_value else 0

    # An unfilled seat must never pass quietly. ERRATA 2026-07-21 promises the seat FOXA
    # burned at inception is refilled to N at the January 2027 review; §7.3 only says how an
    # entrant is funded BY A REMOVAL, and is silent when a vacancy exists with nothing
    # removed. Until a human closes that policy gap, this shouts rather than shrugs.
    seats_short = N - (len(retain) + len(new_rows))
    note = "" if divisor_after == divisor_before else \
        f"divisor {divisor_before:.10f}->{divisor_after:.10f}"
    if seats_short > 0:
        # Funding is no longer a reason for this (see 4b), so whatever is left is the
        # momentum veto or an exhausted candidate pool — both rule-driven and both fine.
        # It is still said out loud: a book quietly running under N is how "N = 20" decays
        # into whatever happened to fit.
        note = (note + "; " if note else "") + f"{seats_short} seat(s) short of N={N}"
        print(f"WARNING: holding {len(retain) + len(new_rows)} names against N={N} — "
              f"{seats_short} seat(s) unfilled.\n"
              f"   Funding is not the reason (the §7.3 vacancy clause covers that): either no "
              f"further name cleared the 200d MA, or the candidate pool ran out. Both are "
              f"rule-driven, and neither is silently acceptable — check the SKIP_MOMENTUM "
              f"lines above.")

    record_review(period, ftd, dt.datetime.now(ET).isoformat(timespec="seconds"),
                  len(retain), len(exits), len(new_rows), turnover, note)

    print(f"Reconstitution complete {today}: retained {len(retain)} · removed {len(exits)} · "
          f"entered {len(new_rows)} · one-way turnover {turnover*100:.1f}%")
    if turnover > R["turnover_budget_annual"]:
        print(f"WARNING: turnover {turnover*100:.1f}% exceeds the annual budget of "
              f"{R['turnover_budget_annual']*100:.0f}% — recorded as an alert only; no rule is adjusted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv) or 0)
