"""Derived-signal probe over six tickers: do the L2/L3 signals match common sense?
Expected: AAPL/MSFT/KO pass cleanly; MU should fail on consecutive positive FCF + CV +
gross-margin trend; MCD's missing gross margin should be flagged None; AAPL/NVDA splits
must not falsely trigger the net-issuance landmine."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.edgar import Edgar, extract_all
from src.metrics import derive, rescale_1000, split_adjust

cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml"))
e = Edgar(**{k: cfg["edgar"][k] for k in ("user_agent","rate_limit_per_sec","cache_dir","cache_days")})

def p(v, pct=False):
    if v is None: return "  --  "
    if isinstance(v, bool): return "   HIT" if v else " clean"
    return f"{v*100:5.1f}%" if pct else f"{v:6.2f}"

print(f"{'tkr':5} {'FCFyrs':>7} {'FCF_CV':>7} {'grossM':>7} {'mgnTrnd':>8} "
      f"{'ROICavg':>7} {'assetG':>7} {'revG':>7} {'ND/EB':>7} "
      f"{'accr':>5} {'AR':>5} {'issue':>5}")
for t in ["AAPL","MSFT","NVDA","MU","KO","MCD"]:
    d = extract_all(e, t)
    m = derive(d, cfg)
    print(f"{t:5} {m['fcf_positive_streak']:>7} {p(m['fcf_cv']):>7} "
          f"{p(m['gross_margin_latest'],1):>7} {'  '+p(m['gross_margin_trend_ok']):>8} "
          f"{p(m['roic_avg'],1):>7} {p(m['asset_cagr'],1):>7} {p(m['rev_cagr'],1):>7} "
          f"{p(m['net_debt_ebitda']):>7} {p(m['L2_accruals']):>5} "
          f"{p(m['L2_ar_growth']):>5} {p(m['L2_share_issuance']):>5}")

# Split-normalization verification
print("\nSplit-normalization check (raw vs normalized share count, last 5 years, millions):")
for t in ["AAPL","NVDA","MCD"]:
    d = extract_all(e, t)
    raw = d["shares"]; adj = split_adjust(rescale_1000(raw))
    ys = sorted(raw)[-5:]
    print(f"  {t} raw :", "  ".join(f"{raw[y]/1e6:8.1f}" for y in ys))
    print(f"  {t} norm:", "  ".join(f"{adj[y]/1e6:8.1f}" for y in ys))
