"""数据体检：拉几只票，打印关键年度序列，肉眼确认 EDGAR 提取靠谱。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.edgar import Edgar, extract_all

cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml"))
e = Edgar(**{k: cfg["edgar"][k] for k in
             ("user_agent", "rate_limit_per_sec", "cache_dir", "cache_days")})

TICKERS = ["AAPL", "MSFT", "NVDA", "MU", "KO", "MCD"]

def fmt(series, keys):
    return "  ".join(f"{k}:{series.get(k, float('nan')):.2f}" if k in series else f"{k}:--"
                     for k in keys)

for t in TICKERS:
    d = extract_all(e, t)
    if d is None:
        print(f"\n{t}: NO DATA (CIK/companyfacts 取不到)"); continue
    print(f"\n===== {t}  ({d['_entity']}) =====")
    years = sorted(set(d["ocf"]) | set(d["capex"]) | set(d["revenue"]))[-11:]
    # FCF = OCF - CapEx
    fcf = {y: d["ocf"][y] - d["capex"][y] for y in years if y in d["ocf"] and y in d["capex"]}
    # 毛利率：优先 GrossProfit/Rev，回退 (Rev-COGS)/Rev
    gm = {}
    for y in years:
        rev = d["revenue"].get(y)
        gp = d["gross_profit"].get(y)
        if gp is None and y in d["cost_of_revenue"] and rev:
            gp = rev - d["cost_of_revenue"][y]
        if rev and gp is not None:
            gm[y] = gp / rev
    print("  年份     :", "  ".join(str(y) for y in years))
    print("  收入(B)  :", "  ".join(f"{d['revenue'][y]/1e9:6.1f}" if y in d['revenue'] else "   -- " for y in years))
    print("  FCF(B)   :", "  ".join(f"{fcf[y]/1e9:6.1f}" if y in fcf else "   -- " for y in years))
    print("  毛利率   :", "  ".join(f"{gm[y]*100:5.1f}%" if y in gm else "   -- " for y in years))
    print("  净利(B)  :", "  ".join(f"{d['net_income'][y]/1e9:6.1f}" if y in d['net_income'] else "   -- " for y in years))
    print("  总资产(B):", "  ".join(f"{d['assets'][y]/1e9:6.0f}" if y in d['assets'] else "   -- " for y in years))
    print("  股本(M)  :", "  ".join(f"{d['shares'][y]/1e6:6.0f}" if y in d['shares'] else "   -- " for y in years))
    # 缺失体检
    missing = [k for k in ("revenue","ocf","capex","net_income","assets","equity") if not d[k]]
    print("  缺失概念 :", missing if missing else "无")
