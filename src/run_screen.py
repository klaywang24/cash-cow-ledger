"""
Phase 1 主程序：跑 L1-L5 漏斗，输出当前候选名单 + 全量淘汰台账（带原因）。

⚠️ 本系统主产品是向前跟踪的观察指数，不是回测。本次输出是【今天这一刻】用
当前财务快照筛出的候选名单，供人工核对，不构成任何交易建议、不预测。
"""
from __future__ import annotations
import sys, json, pathlib, csv, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.edgar import Edgar, extract_all
from src.screen import screen_us, apply_valuation

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
TODAY = dt.date.today().isoformat()


def load_universe():
    u = json.load(open(ROOT / "data/universe/sp500.json"))
    return u["tickers"]


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
    """返回 (pe, fcf_yield, mktcap)。取不到返回 (None,None,None)。"""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        pe = info.get("trailingPE")
        mktcap = info.get("marketCap")
        fcfy = (fcf_latest / mktcap) if (fcf_latest and mktcap) else None
        return pe, fcfy, mktcap
    except Exception:
        return None, None, None


def norm(vals):
    lo, hi = min(vals), max(vals)
    rng = hi - lo
    return [(v - lo) / rng if rng else 0.5 for v in vals]


def score_pool(recs):
    """L5：在 L4 幸存池内做因子 min-max 归一 + 加权。就地写 rec['score']。"""
    if not recs:
        return
    w = cfg["L5_count"]["score_weights"]
    fcfy = norm([r["fcf_yield"] or 0 for r in recs])
    roic = norm([r["roic_avg"] or 0 for r in recs])
    gm = norm([r["gross_margin_latest"] or 0 for r in recs])
    stab = norm([-(r["fcf_cv"] or 0) for r in recs])          # CV 越小越好
    lowg = norm([-(r["asset_cagr"] or 0) for r in recs])      # 资产增速越低越好
    for i, r in enumerate(recs):
        r["score"] = round(
            w["fcf_yield"]*fcfy[i] + w["roic"]*roic[i] + w["gross_margin"]*gm[i]
            + w["fcf_stability"]*stab[i] + w["low_asset_growth"]*lowg[i], 4)


def main():
    e = Edgar(**{k: cfg["edgar"][k] for k in
                 ("user_agent", "rate_limit_per_sec", "cache_dir", "cache_days")})
    universe = load_universe()
    ust10y = get_ust10y()
    print(f"[{TODAY}] L1 宇宙 = {len(universe)} 只美股 (标普500快照) + 手工白名单")
    print(f"L4 估值锚：10年期美债 = {ust10y*100:.2f}%\n")

    all_recs, quality_pass = [], []
    for i, t in enumerate(universe, 1):
        d = extract_all(e, t)
        if d is None:
            all_recs.append({"ticker": t, "entity": None, "stage": "L1",
                             "status": "no_edgar", "data_incomplete": True,
                             "reason": "EDGAR无此CIK/取数失败"})
            continue
        rec = screen_us(d, cfg)
        rec["ticker"] = t
        all_recs.append(rec)
        if rec["status"] == "pass_quality":
            quality_pass.append(rec)
        if i % 100 == 0:
            print(f"  ...已处理 {i}/{len(universe)}，通过质量层 {len(quality_pass)}")

    print(f"\n通过 L2防雷 + L3质量 = {len(quality_pass)} 只，进入 L4 估值（取价）...")

    # L4：只对质量幸存者取价
    val_pass = []
    for r in quality_pass:
        pe, fcfy, mktcap = yf_valuation(r["ticker"], r["fcf_latest"])
        r["mktcap"] = mktcap
        apply_valuation(r, pe, fcfy, ust10y, cfg)
        if r["status"] == "pass_valuation":
            val_pass.append(r)

    # 手工白名单（爱马仕等）：走同一 L4 估值逻辑，质量为人工核对
    manual = load_manual_watchlist(ust10y)
    for r in manual:
        all_recs.append(r)
        if r["status"] == "pass_valuation":
            val_pass.append(r)

    # L5：打分、排名、取前 N
    score_pool(val_pass)
    val_pass.sort(key=lambda r: r["score"], reverse=True)
    lo, hi = cfg["L5_count"]["min_holdings"], cfg["L5_count"]["max_holdings"]
    candidates = val_pass[:hi]

    write_outputs(all_recs, candidates, val_pass, ust10y)
    print_summary(all_recs, quality_pass, val_pass, candidates, lo, hi)


def load_manual_watchlist(ust10y):
    recs = []
    path = ROOT / "data/global_watchlist.csv"
    for row in csv.DictReader(l for l in open(path) if not l.startswith("#")):
        r = {"ticker": row["ticker"], "entity": row["name"], "source": "manual",
             "fcf_positive_streak": None,
             "gross_margin_latest": float(row["gross_margin"]),
             "roic_avg": float(row["roic_avg"]),
             "fcf_cv": None, "asset_cagr": 0.0, "rev_cagr": None,
             "net_debt_ebitda": float(row["net_debt_ebitda"]),
             "fcf_latest": None, "mktcap": None, "data_incomplete": False,
             "note": row.get("note", "")}
        apply_valuation(r, float(row["pe"]), float(row["fcf_yield"]), ust10y, cfg)
        recs.append(r)
    return recs


def write_outputs(all_recs, candidates, val_pass, ust10y):
    outdir = ROOT / "output"; outdir.mkdir(exist_ok=True)
    cols = ["ticker", "entity", "stage", "status", "fcf_positive_streak", "fcf_cv",
            "gross_margin_latest", "roic_avg", "asset_cagr", "rev_cagr",
            "net_debt_ebitda", "pe", "fcf_yield", "score", "reason"]
    # 全量台账（含淘汰原因）
    with open(outdir / f"funnel_{TODAY}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in sorted(all_recs, key=lambda x: (x.get("status") or "", x.get("ticker") or "")):
            w.writerow(r)
    # 候选名单
    with open(outdir / f"candidates_{TODAY}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in candidates:
            w.writerow(r)
    print(f"\n已写出：output/candidates_{TODAY}.csv  与  output/funnel_{TODAY}.csv")


def print_summary(all_recs, quality_pass, val_pass, candidates, lo, hi):
    from collections import Counter
    c = Counter(r["status"] for r in all_recs)
    print("\n" + "="*70)
    print("漏斗汇总：")
    for k in ["no_edgar", "data_incomplete", "rejected", "pass_quality",
              "pass_valuation"]:
        if c.get(k):
            print(f"  {k:16}: {c[k]}")
    print(f"\n候选名单（L4通过后按综合得分取前{hi}，实得{len(candidates)}）：")
    print(f"{'排名':>3} {'票':7} {'得分':>6} {'FCF收益率':>8} {'ROIC':>6} "
          f"{'毛利率':>6} {'PE':>6} {'FCF连正':>6}  公司")
    for i, r in enumerate(candidates, 1):
        print(f"{i:>3} {r['ticker']:7} {r.get('score',0):>6.3f} "
              f"{_pct(r.get('fcf_yield')):>8} {_pct(r.get('roic_avg')):>6} "
              f"{_pct(r.get('gross_margin_latest')):>6} {_num(r.get('pe')):>6} "
              f"{str(r.get('fcf_positive_streak') or '手工'):>6}  {r.get('entity')}")
    if len(candidates) < lo:
        print(f"\n⚠️ 候选不足下限 {lo}：当前规则偏严或估值层杀太多，需人工判断是否放宽。")


def _pct(v): return f"{v*100:.1f}%" if v is not None else "--"
def _num(v): return f"{v:.0f}" if v is not None else "--"


if __name__ == "__main__":
    main()
