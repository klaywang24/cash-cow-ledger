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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEDGER = ROOT / "data/ledger"
CONSTITUENTS = LEDGER / "constituents.csv"
DECISIONS = ROOT / "data/decisions_log.csv"

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
    # Dual-class deduplication (same rule as build_portfolio)
    seen, out = set(), []
    for r in rows:
        co = r["entity"].replace(" INC", "").replace(".", "").strip()[:14]
        if co in seen:
            continue
        seen.add(co)
        out.append((r["ticker"], r["entity"], float(r["score"])))
    return out


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
    with open(DECISIONS, "a", newline="") as f:
        csv.writer(f).writerow([date, ticker, action, rank, price, reason])


# ---------- main ----------
def main():
    R = cfg["rules"]
    N = cfg["L5_count"]["target_holdings"]
    enter_rank, exit_rank = R["buffer_enter_rank"], R["buffer_exit_rank"]
    cap = R["entry_weight_cap"]
    today = dt.date.today()

    if today.month not in R["review_months"]:
        print(f"Month {today.month} is not a review month ({R['review_months']}) — constituents unchanged.")
        return
    cur = load_constituents()
    if not cur:
        print("Ledger not yet open — reconstitution does not apply."); return
    if any(r["entry_date"][:7] == today.isoformat()[:7] for r in cur if r["status"] == "active"):
        print("Already reconstituted this month — this is a once-per-review action, skipping."); return

    ranking = latest_ranking()
    if not ranking:
        print("ERROR: no candidate list found; run run_screen first — aborting (no guessing, no relaxing)."); sys.exit(1)
    rank_of = {t: i + 1 for i, (t, _, _) in enumerate(ranking)}
    score_of = {t: s for t, _, s in ranking}
    entity_of = {t: e for t, e, _ in ranking}

    active = [r for r in cur if r["status"] == "active"]
    universe = sorted(set([r["ticker"] for r in active] + list(rank_of)))
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
    with open(CONSTITUENTS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    turnover = freed / total_value if total_value else 0
    print(f"Reconstitution complete {today}: retained {len(retain)} · removed {len(exits)} · "
          f"entered {len(new_rows)} · one-way turnover {turnover*100:.1f}%")
    if turnover > R["turnover_budget_annual"]:
        print(f"WARNING: turnover {turnover*100:.1f}% exceeds the annual budget of "
              f"{R['turnover_budget_annual']*100:.0f}% — recorded as an alert only; no rule is adjusted.")


if __name__ == "__main__":
    main()
