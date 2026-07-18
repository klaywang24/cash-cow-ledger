"""
半年一次的成分调整（METHODOLOGY §7）。100% 机械，无任何人工确认环节。

核心：**在位成分的份数(units)绝不因调仓被削减**——留任者原样带走自己的份数，
这是「入场后永不再平衡、让赢家漂移」的实现。只有被剔除者才卖出，其释放的
市值用于给新进者建仓；有余额则按比例分给留任者（按比例分配保持留任者之间的
相对权重不变，故不违反「不削赢家」）。

规则：
  - 剔除：名次 > 2N(40) 或不再通过 L2/L3/L4
  - 动量否决：该剔除但价格仍在 200 日均线上方者，延后一个审查期（仅一次）
  - 新进：名次 ≤ N(20)，且价格在 200 日均线上方
  - 新进权重按综合得分比例分配，单只封顶 8%（占调仓后总市值）
"""
from __future__ import annotations
import sys, csv, glob, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEDGER = ROOT / "data/ledger"
CONSTITUENTS = LEDGER / "constituents.csv"
DECISIONS = ROOT / "data/decisions_log.csv"

FIELDS = ["ticker", "entity", "entry_date", "entry_price", "entry_weight",
          "units", "status", "exit_date", "deferred_since"]


# ---------- 工具 ----------
def ma200(ticker: str, closes) -> float | None:
    """200 日均线。数据不足 200 根则返回 None（此时动量闸判为不可用 → 不放行新进）。"""
    s = closes.dropna()
    if len(s) < 200:
        return None
    return float(s.tail(200).mean())


def fetch_history(tickers: list[str]):
    """返回 {ticker: (最新收盘价, 200日均线)}；任一取不到则该票为 (None, None)。"""
    import yfinance as yf
    out = {}
    data = yf.download(tickers, period="18mo", auto_adjust=True,
                       progress=False, group_by="ticker")
    for t in tickers:
        try:
            s = (data[t]["Close"] if len(tickers) > 1 else data["Close"]).dropna()
            out[t] = (float(s.iloc[-1]), ma200(t, s)) if len(s) else (None, None)
        except Exception:
            out[t] = (None, None)
    return out


def load_constituents():
    if not CONSTITUENTS.exists():
        return []
    return list(csv.DictReader(open(CONSTITUENTS)))


def latest_ranking():
    """读最近一次 run_screen 的候选（已按得分降序），返回 [(ticker, entity, score)]。"""
    files = sorted(glob.glob(str(ROOT / "output/candidates_*.csv")))
    if not files:
        return []
    rows = list(csv.DictReader(open(files[-1])))
    # 双重股权去重（与 build_portfolio 同一规则）
    seen, out = set(), []
    for r in rows:
        co = r["entity"].replace(" INC", "").replace(".", "").strip()[:14]
        if co in seen:
            continue
        seen.add(co)
        out.append((r["ticker"], r["entity"], float(r["score"])))
    return out


def cap_and_redistribute(weights: dict, cap: float) -> dict:
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


def log_decision(date, ticker, action, rank, price, reason):
    with open(DECISIONS, "a", newline="") as f:
        csv.writer(f).writerow([date, ticker, action, rank, price, reason])


# ---------- 主流程 ----------
def main():
    R = cfg["rules"]
    N = cfg["L5_count"]["target_holdings"]
    enter_rank, exit_rank = R["buffer_enter_rank"], R["buffer_exit_rank"]
    cap = R["entry_weight_cap"]
    today = dt.date.today()

    if today.month not in R["review_months"]:
        print(f"本月（{today.month} 月）非审查月（{R['review_months']}）—— 成分不动。")
        return
    cur = load_constituents()
    if not cur:
        print("台账尚未开账 —— 调仓不适用。"); return
    if any(r["entry_date"][:7] == today.isoformat()[:7] for r in cur if r["status"] == "active"):
        print("本月已调过仓 —— 调仓是每审查期一次的动作，跳过。"); return

    ranking = latest_ranking()
    if not ranking:
        print("⚠️ 找不到候选名单，请先跑 run_screen —— 中止（不猜、不放宽）。"); sys.exit(1)
    rank_of = {t: i + 1 for i, (t, _, _) in enumerate(ranking)}
    score_of = {t: s for t, _, s in ranking}
    entity_of = {t: e for t, e, _ in ranking}

    active = [r for r in cur if r["status"] == "active"]
    universe = sorted(set([r["ticker"] for r in active] + list(rank_of)))
    px = fetch_history(universe)

    missing = [t for t in [r["ticker"] for r in active] if px.get(t, (None,))[0] is None]
    if missing:
        print(f"⚠️ 在位成分取价失败 {missing} —— 中止调仓（宁可不调，也不用估算价）。")
        sys.exit(1)

    # ---- 1. 决定留任 / 剔除 / 延后 ----
    retain, exits = [], []
    for r in active:
        t = r["ticker"]
        rk = rank_of.get(t)
        price, ma = px[t]
        if rk is not None and rk <= exit_rank:
            r["deferred_since"] = ""          # 回到安全区，清除延后标记
            retain.append(r); continue
        # 触发剔除条件
        why = f"名次{rk}>{exit_rank}" if rk else "不再通过 L2/L3/L4"
        if ma is not None and price > ma and not r.get("deferred_since"):
            r["deferred_since"] = today.isoformat()
            retain.append(r)
            log_decision(today, t, "DEFER", rk or "", round(price, 4),
                         f"{why}，但价格在200日均线上方 → 延后一个审查期（规则自动，非人工裁量）")
        else:
            exits.append((r, price, why))

    # ---- 2. 组合市值与留任者持有市值 ----
    total_value = sum(float(r["units"]) * px[r["ticker"]][0] for r in active)
    freed = sum(float(r["units"]) * p for r, p, _ in exits)

    # ---- 3. 选新进者（名次 ≤ N、动量过闸、且当前未持有）----
    held = {r["ticker"] for r in retain}
    vacancies = max(0, N - len(retain))
    entrants = []
    for t, _, _ in ranking:
        if len(entrants) >= vacancies:
            break
        if t in held or rank_of[t] > enter_rank:
            continue
        price, ma = px.get(t, (None, None))
        if price is None:
            continue
        if ma is None or price <= ma:          # 动量否决：不接下跌趋势 / 数据不足不放行
            log_decision(today, t, "SKIP_MOMENTUM", rank_of[t],
                         round(price, 4) if price else "",
                         "名次达标但价格未站上200日均线（或历史不足200根）→ 本期不买入")
            continue
        entrants.append(t)

    # ---- 4. 分配：新进者按得分比例吃掉 freed，封顶 8% 总市值；余额按比例给留任者 ----
    new_rows = []
    if entrants and freed > 0:
        raw = {t: score_of[t] for t in entrants}
        tot = sum(raw.values())
        target_w = cap_and_redistribute(
            {t: (v / tot) * (freed / total_value) for t, v in raw.items()}, cap)
        for t in entrants:
            alloc = target_w[t] * total_value
            price = px[t][0]
            new_rows.append({
                "ticker": t, "entity": entity_of[t], "entry_date": today.isoformat(),
                "entry_price": round(price, 6), "entry_weight": round(target_w[t], 6),
                "units": round(alloc / price, 8), "status": "active",
                "exit_date": "", "deferred_since": ""})
            log_decision(today, t, "ADD", rank_of[t], round(price, 4),
                         f"名次{rank_of[t]}≤{enter_rank}且站上200日均线，按得分分配 {target_w[t]*100:.2f}%")
        leftover = freed - sum(r["units"] * px[r["ticker"]][0] for r in new_rows)
    else:
        leftover = freed

    # 余额按比例分给留任者（保持相对权重不变 → 不削赢家）
    if leftover > 1e-9 and retain:
        held_val = sum(float(r["units"]) * px[r["ticker"]][0] for r in retain)
        if held_val > 0:
            for r in retain:
                r["units"] = round(float(r["units"]) * (1 + leftover / held_val), 8)

    # ---- 5. 落库 ----
    for r, price, why in exits:
        r["status"] = "removed"; r["exit_date"] = today.isoformat()
        log_decision(today, r["ticker"], "DROP", rank_of.get(r["ticker"], ""),
                     round(price, 4), f"{why}（规则自动，非人工裁量）")

    # cur 已包含全部历史行（active + 早前 removed），且 retain/exits 都是其中对象的
    # 原地引用——上面的状态与份数修改已生效。故直接写 cur + 新进者，每行恰好一次。
    # （曾误写成 removed_before + exits + retain + new_rows，导致本期剔除者被写两遍。）
    out = cur + new_rows
    with open(CONSTITUENTS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    turnover = freed / total_value if total_value else 0
    print(f"调仓完成 {today}：留任 {len(retain)} · 剔除 {len(exits)} · 新进 {len(new_rows)}"
          f" · 单边换手 {turnover*100:.1f}%")
    if turnover > R["turnover_budget_annual"]:
        print(f"⚠️ 换手 {turnover*100:.1f}% 超过年度预算 {R['turnover_budget_annual']*100:.0f}%"
              f" —— 按规则仅记录告警，不调整任何规则。")


if __name__ == "__main__":
    main()
