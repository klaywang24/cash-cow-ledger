"""派生信号体检：6 只票，看 L2/L3 信号是否符合常识。
预期：AAPL/MSFT/KO 干净通过；MU 应栽在 FCF 连续为正 + CV + 毛利率趋势；
MCD 毛利率缺失应被标 None；AAPL/NVDA 拆股不应误触发净增发。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.edgar import Edgar, extract_all
from src.metrics import derive, rescale_1000, split_adjust

cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml"))
e = Edgar(**{k: cfg["edgar"][k] for k in ("user_agent","rate_limit_per_sec","cache_dir","cache_days")})

def p(v, pct=False):
    if v is None: return "  --  "
    if isinstance(v, bool): return " 命中" if v else " 清白"
    return f"{v*100:5.1f}%" if pct else f"{v:6.2f}"

print(f"{'票':5} {'FCF连正':>7} {'FCF_CV':>7} {'毛利率':>7} {'毛利趋势':>8} "
      f"{'ROIC均':>7} {'资产g':>7} {'收入g':>7} {'ND/EB':>7} "
      f"{'应计':>5} {'应收':>5} {'增发':>5}")
for t in ["AAPL","MSFT","NVDA","MU","KO","MCD"]:
    d = extract_all(e, t)
    m = derive(d, cfg)
    print(f"{t:5} {m['fcf_positive_streak']:>7} {p(m['fcf_cv']):>7} "
          f"{p(m['gross_margin_latest'],1):>7} {'  '+p(m['gross_margin_trend_ok']):>8} "
          f"{p(m['roic_avg'],1):>7} {p(m['asset_cagr'],1):>7} {p(m['rev_cagr'],1):>7} "
          f"{p(m['net_debt_ebitda']):>7} {p(m['L2_accruals']):>5} "
          f"{p(m['L2_ar_growth']):>5} {p(m['L2_share_issuance']):>5}")

# 拆股归一验证
print("\n拆股归一验证（原始 vs 归一后最近5年股本，百万股）：")
for t in ["AAPL","NVDA","MCD"]:
    d = extract_all(e, t)
    raw = d["shares"]; adj = split_adjust(rescale_1000(raw))
    ys = sorted(raw)[-5:]
    print(f"  {t} 原始:", "  ".join(f"{raw[y]/1e6:8.1f}" for y in ys))
    print(f"  {t} 归一:", "  ".join(f"{adj[y]/1e6:8.1f}" for y in ys))
