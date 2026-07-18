"""
构建【第一本：机械指数】的成分与权重。100% 机械，无任何人工确认环节。

权重规则（config.rules）：
  - 按综合得分加权（不用 FCF 绝对额——那会系统性偏向"大而老"的成熟现金牛）
  - 仅在入场时封顶 8%，超出部分按比例分给未封顶者（迭代至收敛）
  - 入场后永不再平衡：权重随价格漂移，让赢家自己膨胀

第二本（集中判断书）不在此计算——它是 data/book2_conviction.csv，纯手工，
由你的判断决定，与本文件的机械逻辑彻底隔离。
"""
from __future__ import annotations
import sys, csv, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
TODAY = dt.date.today().isoformat()


def dedup_dual_class(rows):
    """双重股权去重：同公司只留一只（候选已按得分排序，留高分那只）。"""
    seen, out = set(), []
    for r in rows:
        co = r["entity"].replace(" INC", "").replace(".", "").strip()[:14]
        if co in seen:
            continue
        seen.add(co); out.append(r)
    return out


def cap_and_redistribute(weights: dict, cap: float) -> dict:
    """把超过 cap 的权重削平，超出部分按比例分给未封顶者，迭代至收敛。"""
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
        print(f"⚠️ 合格者仅 {len(rows)} 只，低于下限 {minN}——按规则记录，不补足、不放宽。")
    book1 = rows[:N]                      # 按得分取前 N

    # 得分加权 + 入场封顶
    raw = {r["ticker"]: float(r["score"]) for r in book1}
    tot = sum(raw.values())
    w = cap_and_redistribute({k: v / tot for k, v in raw.items()}, cap)
    for r in book1:
        r["weight"] = w[r["ticker"]]

    write_book1(book1)
    show(book1)
    show_book2()


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
    print(f"\n【第一本 · 机械指数】{TODAY} · N={len(rows)} · 得分加权 · 入场封顶"
          f"{cfg['rules']['entry_weight_cap']*100:.0f}% · 入场后不再平衡")
    print(f"{'#':>2} {'票':7} {'权重':>6} {'得分':>6} {'FCF收益':>7} {'ROIC':>6} {'利润率':>6} {'PE':>5}  公司")
    for i, r in enumerate(rows, 1):
        pe = f"{float(r['pe']):.0f}" if r["pe"] else "--"
        print(f"{i:>2} {r['ticker']:7} {r['weight']*100:5.1f}% {float(r['score']):6.3f} "
              f"{_p(r['fcf_yield']):>7} {_p(r['roic_avg']):>6} {_p(r['gross_margin_latest']):>6} "
              f"{pe:>5}  {r['entity'][:30]}")
    print(f"权重合计 {sum(r['weight'] for r in rows)*100:.1f}%"
          f" · 最大单只 {max(r['weight'] for r in rows)*100:.1f}%"
          f" · 最小 {min(r['weight'] for r in rows)*100:.1f}%")


def show_book2():
    path = ROOT / "data/book2_conviction.csv"
    rows = list(csv.DictReader(l for l in open(path) if not l.startswith("#")))
    print(f"\n【第二本 · 集中判断书】{len(rows)} 只 · 纯手工 · 不参与上面任何计算")
    for r in rows:
        filled = "✅" if r["thesis"] and not r["thesis"].startswith("待填") else "⬜ 论证待你写"
        print(f"  {r['ticker']:8} {r['name'][:24]:26} 权重:{r['weight'] or '待定':6}  {filled}")
    print("  ⚠️ V 与 AXP 共用卡轨命脉，不是两个独立下注——先回答「为什么押卡轨」。")


if __name__ == "__main__":
    main()
