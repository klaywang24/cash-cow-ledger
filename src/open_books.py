"""
开账：把当日成分按目标权重换算成【份数(units)】锁进台账，基点 100。

只在 today >= inception_date 且台账尚未建立时执行一次；此后任何一天调用都会
立即退出——开账是不可重复的动作。

份数机制：units_i = 目标权重_i × 100 / 入场价_i。此后点位 = Σ(units_i × 当日价)，
权重随价格自由漂移，我们一股不动（对应 METHODOLOGY §7.3「入场后永不再平衡」）。
"""
from __future__ import annotations
import sys, csv, glob, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEDGER = ROOT / "data/ledger"
CONSTITUENTS = LEDGER / "constituents.csv"
LEVELS = LEDGER / "index_level.csv"


def main():
    inception = cfg["meta"].get("inception_date")
    if not inception:
        print("未设开账日 —— 不动作。"); return
    today = dt.date.today()
    if today < dt.date.fromisoformat(str(inception)):
        print(f"未到开账日（{inception}）—— 不动作。"); return
    if CONSTITUENTS.exists():
        print("台账已开 —— 开账是一次性动作，跳过。"); return

    # 取最近一次 build_portfolio 的成分（与开账同一工作流内生成）
    files = sorted(glob.glob(str(ROOT / "output/book1_index_*.csv")))
    if not files:
        print("⚠️ 找不到成分文件，请先跑 run_screen + build_portfolio。"); sys.exit(1)
    rows = list(csv.DictReader(open(files[-1])))
    if not rows:
        print("⚠️ 成分文件为空，中止。"); sys.exit(1)

    tickers = [r["ticker"] for r in rows]
    import yfinance as yf
    data = yf.download(tickers, period="5d", auto_adjust=True,
                       progress=False, group_by="ticker")
    prices = {}
    for t in tickers:
        try:
            s = data[t]["Close"].dropna()
            prices[t] = float(s.iloc[-1])
        except Exception:
            print(f"⚠️ {t} 取价失败 —— 开账中止（宁可不开，也不用估算价建仓）。")
            sys.exit(1)

    LEDGER.mkdir(parents=True, exist_ok=True)
    opened = today.isoformat()
    with open(CONSTITUENTS, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "entity", "entry_date", "entry_price",
                    "entry_weight", "units", "status", "exit_date"])
        for r in rows:
            wt = float(r["weight"]); px = prices[r["ticker"]]
            w.writerow([r["ticker"], r["entity"], opened, round(px, 6),
                        round(wt, 6), round(wt * 100.0 / px, 8), "active", ""])

    with open(LEVELS, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "level", "n_constituents"])
        w.writerow([opened, "100.0000", len(rows)])

    print(f"✅ 已开账 {opened}：{len(rows)} 只成分，基点 100.0000")
    print("   此后每个交易日自动计算点位；成分只在每年 1 月/7 月首个交易日变动。")


if __name__ == "__main__":
    main()
