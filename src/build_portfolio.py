"""
Build the constituents and weights of Book One (the mechanical index).
100% mechanical: there is no human confirmation step anywhere.

Weighting rules (config.rules):
  - Composite-score weighted (not absolute FCF, which systematically favors large,
    mature cash cows)
  - Capped at 8% at entry only, with the excess redistributed pro rata among uncapped
    names (iterated to convergence)
  - Never rebalanced after entry: weights drift with price and winners are allowed to grow

This index contains no discretionary component; discretionary holdings, if any, are
recorded outside this repository.
"""
from __future__ import annotations
import sys, csv, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
TODAY = dt.date.today().isoformat()


def dedup_dual_class(rows):
    """Dual-class deduplication: keep one ticker per company (candidates are already
    score-sorted, so the higher-scoring one wins)."""
    seen, out = set(), []
    for r in rows:
        co = r["entity"].replace(" INC", "").replace(".", "").strip()[:14]
        if co in seen:
            continue
        seen.add(co); out.append(r)
    return out


def cap_and_redistribute(weights: dict, cap: float) -> dict:
    """Cap weights above `cap` and redistribute the excess pro rata among uncapped
    names, iterating to convergence."""
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


def main():
    cand = list(csv.DictReader(open(ROOT / f"output/candidates_{TODAY}.csv")))
    rows = dedup_dual_class(cand)

    N = cfg["L5_count"]["target_holdings"]
    minN = cfg["L5_count"]["min_holdings"]
    cap = cfg["rules"]["entry_weight_cap"]

    if len(rows) < minN:
        print(f"WARNING: only {len(rows)} qualifiers, below the floor of {minN} — recorded per the rules; not backfilled, rules not relaxed.")
    book1 = rows[:N]                      # top N by composite score

    # Score weighting + entry cap
    raw = {r["ticker"]: float(r["score"]) for r in book1}
    tot = sum(raw.values())
    w = cap_and_redistribute({k: v / tot for k, v in raw.items()}, cap)
    for r in book1:
        r["weight"] = w[r["ticker"]]

    write_book1(book1)
    show(book1)


def write_book1(rows):
    (ROOT / "output").mkdir(exist_ok=True)
    with open(ROOT / f"output/book1_index_{TODAY}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "ticker", "entity", "weight", "score", "fcf_yield",
                     "roic", "margin", "pe", "fcf_positive_years"])
        for i, r in enumerate(rows, 1):
            wr.writerow([i, r["ticker"], r["entity"], round(r["weight"], 5), r["score"],
                         r["fcf_yield"], r["roic_avg"],
                         r["gross_margin_latest"] or "", r["pe"], r["fcf_positive_streak"]])


def _p(v):
    try:
        return f"{float(v)*100:.0f}%"
    except (TypeError, ValueError):
        return "--"


def show(rows):
    print(f"\n[Book One - mechanical index] {TODAY} · N={len(rows)} · score-weighted · "
          f"entry cap {cfg['rules']['entry_weight_cap']*100:.0f}% · never rebalanced after entry")
    print(f"{'#':>2} {'ticker':7} {'wt':>6} {'score':>6} {'FCFyld':>7} {'ROIC':>6} {'margin':>6} {'P/E':>5}  company")
    for i, r in enumerate(rows, 1):
        pe = f"{float(r['pe']):.0f}" if r["pe"] else "--"
        print(f"{i:>2} {r['ticker']:7} {r['weight']*100:5.1f}% {float(r['score']):6.3f} "
              f"{_p(r['fcf_yield']):>7} {_p(r['roic_avg']):>6} {_p(r['gross_margin_latest']):>6} "
              f"{pe:>5}  {r['entity'][:30]}")
    print(f"Total weight {sum(r['weight'] for r in rows)*100:.1f}%"
          f" · max {max(r['weight'] for r in rows)*100:.1f}%"
          f" · min {min(r['weight'] for r in rows)*100:.1f}%")



if __name__ == "__main__":
    main()
