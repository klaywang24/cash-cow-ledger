"""
Main screening program: run the L1-L5 funnel, emit the current candidate list plus the
full rejection ledger (with reasons).

NOTE: the product is a forward-tracked paper index, not a backtest. This output is the
candidate list as screened from the CURRENT fundamentals snapshot. It is not a trading
recommendation and makes no prediction.
"""
from __future__ import annotations
import sys, json, pathlib, csv, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.edgar import Edgar, extract_all
from src.screen import screen_us, apply_valuation, dedup_dual_class

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
TODAY = dt.date.today().isoformat()


def load_universe():
    """L1: S&P 500 snapshot, dropping balance-sheet financials and REITs (the framework does
    not apply to them) while keeping asset-light financials."""
    u = json.load(open(ROOT / "data/universe/sp500.json"))
    L1 = cfg["L1_universe"]
    exc_sectors = set(L1.get("exclude_sectors", []))
    keep_fin_sub = set(L1.get("keep_financials_sub", []))
    out, dropped = [], 0
    for r in u["rows"]:
        if r["sector"] in exc_sectors:
            dropped += 1; continue
        if r["sector"] == "Financials" and r["sub"] not in keep_fin_sub:
            dropped += 1; continue
        out.append(r["ticker"])
    print(f"L1 dropped {dropped} balance-sheet financials/REITs -> {len(out)} remain")
    return out


def get_ust10y():
    try:
        import yfinance as yf
        h = yf.Ticker("^TNX").history(period="5d")
        if len(h):
            return float(h["Close"].iloc[-1]) / 100.0
    except Exception:
        pass
    return cfg["L4_valuation"]["ust10y_fallback"]


def yf_valuation(ticker, fcf_latest):
    """Return (pe, fcf_yield, mktcap). Unavailable values return None, never 0.

    CURRENCY GUARD: yfinance reports marketCap in the trading currency but financials in
    the reporting currency. When they differ (some ADRs: USD market cap against local-currency
    statements), dividing one by the other inflates the yield by roughly 32x. In that case
    FCF yield is treated as unavailable.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        pe = info.get("trailingPE")
        mktcap = info.get("marketCap")
        cur, fcur = info.get("currency"), info.get("financialCurrency")
        if cur and fcur and cur != fcur:
            return pe, None, mktcap          # currency mismatch -> do not compute a yield
        fcfy = (fcf_latest / mktcap) if (fcf_latest and mktcap) else None
        return pe, fcfy, mktcap
    except Exception:
        return None, None, None


def norm(vals):
    lo, hi = min(vals), max(vals)
    rng = hi - lo
    return [(v - lo) / rng if rng else 0.5 for v in vals]


def score_pool(recs):
    """L5: min-max normalize factors within the L4 survivor pool and weight them.
    Writes rec['score'] in place."""
    if not recs:
        return
    w = cfg["L5_count"]["score_weights"]
    fcfy = norm([r["fcf_yield"] or 0 for r in recs])
    roic = norm([r["roic_avg"] or 0 for r in recs])
    gm = norm([r["gross_margin_latest"] or 0 for r in recs])
    stab = norm([-(r["fcf_cv"] or 0) for r in recs])          # lower CV is better
    lowg = norm([-(r["asset_cagr"] or 0) for r in recs])      # lower asset growth is better
    for i, r in enumerate(recs):
        r["score"] = round(
            w["fcf_yield"]*fcfy[i] + w["roic"]*roic[i] + w["gross_margin"]*gm[i]
            + w["fcf_stability"]*stab[i] + w["low_asset_growth"]*lowg[i], 4)


def main():
    e = Edgar(**{k: cfg["edgar"][k] for k in
                 ("user_agent", "rate_limit_per_sec", "cache_dir", "cache_days")})
    universe = load_universe()
    ust10y = get_ust10y()
    print(f"[{TODAY}] L1 universe = {len(universe)} US names (S&P 500 snapshot, balance-sheet financials and REITs removed)")
    print(f"L4 valuation anchor: 10y US Treasury = {ust10y*100:.2f}%\n")

    all_recs, quality_pass = [], []
    for i, t in enumerate(universe, 1):
        d = extract_all(e, t)
        if d is None:
            all_recs.append({"ticker": t, "entity": None, "stage": "L1",
                             "status": "no_edgar", "data_incomplete": True,
                             "reason": "No CIK in EDGAR / fetch failed"})
            continue
        rec = screen_us(d, cfg)
        rec["ticker"] = t
        all_recs.append(rec)
        if rec["status"] == "pass_quality":
            quality_pass.append(rec)
        if i % 100 == 0:
            print(f"  ...processed {i}/{len(universe)}, passed quality: {len(quality_pass)}")

    print(f"\nPassed L2 landmines + L3 quality = {len(quality_pass)}; fetching prices for L4...")

    # L4: fetch prices only for quality survivors
    val_pass = []
    for r in quality_pass:
        pe, fcfy, mktcap = yf_valuation(r["ticker"], r["fcf_latest"])
        r["mktcap"] = mktcap
        apply_valuation(r, pe, fcfy, ust10y, cfg)
        if r["status"] == "pass_valuation":
            val_pass.append(r)

    # L5: score, rank, take the top N.
    # NOTE: this index covers US names via EDGAR only (METHODOLOGY §1). Any security whose
    # fundamentals require manual entry (foreign private issuers and the like) cannot be
    # machine-reproduced and is therefore structurally out of scope.
    score_pool(val_pass)
    val_pass.sort(key=lambda r: r["score"], reverse=True)
    lo, hi = cfg["L5_count"]["min_holdings"], cfg["L5_count"]["target_holdings"]
    # One seat per company, enforced BEFORE the top-N cut (see ERRATA.md 2026-07-21).
    # The FULL deduplicated ranking is written out, not just the top N: §7.2 removal
    # needs ranks beyond N (exit buffer at 2N), which a truncated file cannot provide.
    ranking = dedup_dual_class(val_pass)
    candidates = ranking[:hi]

    write_outputs(all_recs, ranking, val_pass, ust10y)
    print_summary(all_recs, quality_pass, val_pass, candidates, lo, hi)


def write_outputs(all_recs, ranking, val_pass, ust10y):
    outdir = ROOT / "output"; outdir.mkdir(exist_ok=True)
    cols = ["ticker", "entity", "stage", "status", "fcf_positive_streak", "fcf_cv",
            "gross_margin_latest", "roic_avg", "asset_cagr", "rev_cagr",
            "net_debt_ebitda", "pe", "fcf_yield", "score", "reason"]
    # Full ledger (including rejection reasons)
    with open(outdir / f"funnel_{TODAY}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in sorted(all_recs, key=lambda x: (x.get("status") or "", x.get("ticker") or "")):
            w.writerow(r)
    # Deduplicated ranking of all L4 survivors (one row per company; the top N are the
    # candidates, deeper ranks feed the §7.2 exit buffer at reconstitution)
    with open(outdir / f"candidates_{TODAY}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in ranking:
            w.writerow(r)
    print(f"\nWrote output/candidates_{TODAY}.csv and output/funnel_{TODAY}.csv")


def print_summary(all_recs, quality_pass, val_pass, candidates, lo, hi):
    from collections import Counter
    c = Counter(r["status"] for r in all_recs)
    print("\n" + "="*70)
    print("Funnel summary:")
    for k in ["no_edgar", "data_incomplete", "rejected", "pass_quality",
              "pass_valuation"]:
        if c.get(k):
            print(f"  {k:16}: {c[k]}")
    print(f"\nCandidates (top {hi} by composite score after L4; {len(candidates)} found):")
    print(f"{'#':>3} {'ticker':7} {'score':>6} {'FCFyld':>8} {'ROIC':>6} "
          f"{'margin':>6} {'P/E':>6} {'FCFyrs':>6}  company")
    for i, r in enumerate(candidates, 1):
        print(f"{i:>3} {r['ticker']:7} {r.get('score',0):>6.3f} "
              f"{_pct(r.get('fcf_yield')):>8} {_pct(r.get('roic_avg')):>6} "
              f"{_pct(r.get('gross_margin_latest')):>6} {_num(r.get('pe')):>6} "
              f"{str(r.get('fcf_positive_streak') or '--'):>6}  {r.get('entity')}")
    if len(candidates) < lo:
        print(f"\nWARNING: only {len(candidates)} candidates, below the floor of {lo}. Recorded as an anomaly; rules are NOT relaxed.")


def _pct(v): return f"{v*100:.1f}%" if v is not None else "--"
def _num(v): return f"{v:.0f}" if v is not None else "--"


if __name__ == "__main__":
    main()
