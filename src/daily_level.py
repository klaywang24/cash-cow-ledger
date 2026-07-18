"""
每个交易日计算一次指数点位，追加进台账。

核心机制：台账存的是【份数(units)】而不是权重——这才是"入场后永不再平衡"的正确
实现。开账时 units_i = 目标权重_i × 100 / 入场价_i；此后每日
    点位 = Σ(units_i × 当日复权收盘价)
权重随价格自然漂移，赢家自己膨胀，我们一股都不动。

未设 inception_date 时本脚本安全退出——开账前不产生任何记录。
"""
from __future__ import annotations
import sys, csv, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEDGER = ROOT / "data/ledger"
CONSTITUENTS = LEDGER / "constituents.csv"
LEVELS = LEDGER / "index_level.csv"


def load_active():
    """返回 [(ticker, units)]，只含在册成分。"""
    if not CONSTITUENTS.exists():
        return []
    rows = list(csv.DictReader(open(CONSTITUENTS)))
    return [(r["ticker"], float(r["units"])) for r in rows
            if r.get("status", "active") == "active"]


def fetch_closes(tickers):
    """取当日复权收盘价。任一取不到 → 返回 None，当日不落库(绝不用旧价顶替)。"""
    import yfinance as yf
    out = {}
    data = yf.download(tickers, period="5d", auto_adjust=True,
                       progress=False, group_by="ticker")
    for t in tickers:
        try:
            s = data[t]["Close"].dropna() if len(tickers) > 1 else data["Close"].dropna()
            if len(s) == 0:
                return None, f"{t} 无价格"
            out[t] = float(s.iloc[-1])
        except Exception as e:
            return None, f"{t} 取价失败: {e}"
    return out, None


def already_logged(date_str):
    if not LEVELS.exists():
        return False
    return any(r["date"] == date_str for r in csv.DictReader(open(LEVELS)))


def main():
    if not cfg["meta"].get("inception_date"):
        print("尚未开账(config.meta.inception_date 为空)——不产生任何记录，正常退出。")
        return

    active = load_active()
    if not active:
        print("台账无在册成分——退出。")
        return

    today = dt.date.today().isoformat()
    if already_logged(today):
        print(f"{today} 已落库，跳过。")
        return

    closes, err = fetch_closes([t for t, _ in active])
    if closes is None:
        print(f"⚠️ 当日不落库：{err}（宁可缺一天，也不用旧价或估算值顶替）")
        return

    level = sum(units * closes[t] for t, units in active)

    LEDGER.mkdir(parents=True, exist_ok=True)
    new = not LEVELS.exists()
    with open(LEVELS, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "level", "n_constituents"])
        w.writerow([today, round(level, 4), len(active)])
    print(f"{today} 指数点位 {level:.4f}（{len(active)} 只成分）")


if __name__ == "__main__":
    main()
