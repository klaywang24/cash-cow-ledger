"""
把筛选候选 + 手工例外 组装成带权重的观察组合。
权重规则(config.rules)：规则票按 FCF 绝对额加权；例外每只 ≤8% 上限。
输出=卫星仓内的权重(× 卫星占总仓比例 = 占总仓)。纯展示，不是下单。
"""
from __future__ import annotations
import sys, csv, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.edgar import Edgar, extract_all
from src.metrics import fcf_series, derive

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
TODAY = dt.date.today().isoformat()

# 用户裁定：科技大盘股不进(核心仓已有)；只加这三只估值例外
EXCEPTIONS = ["V", "MA", "RMS.PA"]
HERMES_FCF_USD = 4.04e9 * 1.08          # 爱马仕 FCF €4.04B × EUR/USD≈1.08


def fcf_latest_usd(e, ticker):
    d = extract_all(e, ticker)
    if d is None:
        return None
    f = fcf_series(d)
    return f[max(f)] if f else None


def main():
    e = Edgar(**{k: cfg["edgar"][k] for k in
                 ("user_agent", "rate_limit_per_sec", "cache_dir", "cache_days")})
    cand = list(csv.DictReader(open(ROOT / f"output/candidates_{TODAY}.csv")))
    rules = [r for r in cand if r["ticker"] not in EXCEPTIONS]
    # 双重股权去重：同公司只留一只（候选已按得分排序，留先出现的高分那只）
    seen_co, dedup = set(), []
    for r in rules:
        co = r["entity"].replace(" INC", "").replace(".", "").strip()[:14]
        if co in seen_co:
            continue
        seen_co.add(co); dedup.append(r)
    rules = dedup

    rows = []
    # 规则票
    for r in rules:
        fcf = fcf_latest_usd(e, r["ticker"])
        rows.append({"ticker": r["ticker"], "entity": r["entity"], "kind": "规则",
                     "fcf": fcf, "roic": r["roic_avg"], "gm": r["gross_margin_latest"],
                     "pe": r["pe"], "fcf_yield": r["fcf_yield"], "streak": r["fcf_positive_streak"],
                     "score": r["score"]})
    # 例外
    for t in EXCEPTIONS:
        if t == "RMS.PA":
            wl = next(csv.DictReader(l for l in open(ROOT/"data/global_watchlist.csv") if not l.startswith("#")))
            rows.append({"ticker": "RMS.PA", "entity": wl["name"], "kind": "例外",
                         "fcf": HERMES_FCF_USD, "roic": wl["roic_avg"], "gm": wl["gross_margin"],
                         "pe": wl["pe"], "fcf_yield": wl["fcf_yield"], "streak": "手工", "score": None})
        else:
            d = extract_all(e, t); m = derive(d, cfg)
            rows.append({"ticker": t, "entity": m["entity"], "kind": "例外",
                         "fcf": fcf_latest_usd(e, t), "roic": m["roic_avg"],
                         "gm": m["gross_margin_latest"], "om": m["op_margin_latest"],
                         "pe": None, "fcf_yield": None, "streak": m["fcf_positive_streak"], "score": None})

    # ---- 权重：例外先按FCF算再封顶8%，剩余预算规则票按FCF分 ----
    cap = cfg["rules"]["exception_cap_pct"]
    total_fcf = sum(r["fcf"] for r in rows if r["fcf"])
    exc = [r for r in rows if r["kind"] == "例外"]
    rul = [r for r in rows if r["kind"] == "规则"]
    for r in exc:
        r["weight"] = min(r["fcf"]/total_fcf, cap) if r["fcf"] else 0
    budget = 1 - sum(r["weight"] for r in exc)
    rul_fcf = sum(r["fcf"] for r in rul if r["fcf"])
    for r in rul:
        r["weight"] = (r["fcf"]/rul_fcf) * budget if r["fcf"] else 0

    rows.sort(key=lambda r: r["weight"], reverse=True)
    write(rows)
    show(rows)


def write(rows):
    with open(ROOT/f"output/portfolio_{TODAY}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker","entity","kind","weight_in_satellite","fcf_usd_b","roic","margin","pe","fcf_positive_years"])
        for r in rows:
            w.writerow([r["ticker"], r["entity"], r["kind"], round(r["weight"],4),
                        round((r["fcf"] or 0)/1e9,1), r["roic"], r.get("gm") or r.get("om"),
                        r["pe"], r["streak"]])


def show(rows):
    print(f"\n观察组合 {TODAY} · 卫星仓内权重 · 共 {len(rows)} 只")
    print(f"{'#':>2} {'票':7} {'权重':>6} {'FCF(B)':>7} {'ROIC':>6} {'利润率':>6} {'PE':>5} {'类':>4}  公司")
    exc_w = 0
    for i, r in enumerate(rows, 1):
        m = r.get("gm") or r.get("om")
        mval = f"{float(m)*100:.0f}%" if m not in (None,"") else "--"
        rv = f"{float(r['roic'])*100:.0f}%" if r['roic'] not in (None,"") else "--"
        pe = f"{float(r['pe']):.0f}" if r['pe'] not in (None,"") else "--"
        if r["kind"]=="例外": exc_w += r["weight"]
        print(f"{i:>2} {r['ticker']:7} {r['weight']*100:5.1f}% {(r['fcf'] or 0)/1e9:7.1f} "
              f"{rv:>6} {mval:>6} {pe:>5} {r['kind']:>4}  {r['entity'][:32]}")
    print(f"\n规则票 {sum(1 for r in rows if r['kind']=='规则')} 只，例外 {sum(1 for r in rows if r['kind']=='例外')} 只(合计 {exc_w*100:.1f}% 卫星仓)")
    print(f"权重合计 {sum(r['weight'] for r in rows)*100:.1f}%（应≈100%）")


if __name__ == "__main__":
    main()
