"""
从 EDGAR 年度序列派生 L2-L5 所需的全部信号。

两条铁律：
- 缺数据就返回 None，绝不用 0 或估算值顶替（对应 config.data_policy）。
- 序列在计算前先过量纲归一 + 拆股归一（XBRL as-filed 的两个坑）。
"""
from __future__ import annotations
import math
from statistics import mean, pstdev
from typing import Optional

# 常见拆股比例（正拆 + 反拆用倒数）
_SPLIT_FACTORS = [2, 3, 3/2, 4, 5, 6, 7, 8, 10, 15, 20]


def rescale_1000(series: dict[int, float]) -> dict[int, float]:
    """修同一序列内【千倍量纲错位】（千/百万/全额混用，如 MCD 某年起改按百万报）。
    只在某年偏离基准≈精确 1000^n（容差内）时才拉回；拆股/成长造成的非千倍
    量级变化（如 NVDA 十年 40 倍）一律放过，交给 split_adjust。"""
    vals = [v for v in series.values() if v and v > 0]
    if len(vals) < 2:
        return dict(series)
    logs = sorted(math.log10(v) for v in vals)
    med = logs[len(logs) // 2]
    out = {}
    for y, v in series.items():
        if v and v > 0:
            frac = (med - math.log10(v)) / 3           # 3 = log10(1000)
            k = round(frac)
            # 只有极接近整数（≈精确千倍）才认定是量纲错位
            out[y] = v * (1000 ** k) if k != 0 and abs(frac - k) < 0.12 else v
        else:
            out[y] = v
    return out


def split_adjust(series: dict[int, float]) -> dict[int, float]:
    """把股本序列的拆股跳变还原到最新年份口径（仅用于股本）。
    从新到旧扫相邻年比值，接近某拆股比例就把更早年份整体缩放。"""
    years = sorted(series)
    if len(years) < 2:
        return dict(series)
    out = dict(series)
    factor = 1.0
    for i in range(len(years) - 1, 0, -1):
        cur, prev = series[years[i]], series[years[i - 1]]
        if not (cur and prev and cur > 0 and prev > 0):
            continue
        r = cur / prev
        for sf in _SPLIT_FACTORS:
            if abs(r - sf) / sf < 0.08:          # 正拆：今年股本≈去年×sf
                factor *= sf
                break
            if abs(r - 1 / sf) / (1 / sf) < 0.08:  # 反拆
                factor /= sf
                break
        out[years[i - 1]] = series[years[i - 1]] * factor
    return out


def _last_n_consecutive(series: dict[int, float], n: int) -> list[int]:
    """返回最近 n 个【连续】财年（有断档就截断）。不足 n 返回已有的。"""
    years = sorted(series)
    if not years:
        return []
    run = [years[-1]]
    for y in reversed(years[:-1]):
        if run[-1] - y == 1:
            run.append(y)
        else:
            break
        if len(run) == n:
            break
    return sorted(run)


def fcf_series(d: dict) -> dict[int, float]:
    ocf, capex = d["ocf"], d["capex"]
    return {y: ocf[y] - capex[y] for y in ocf if y in capex}


def gross_margin_series(d: dict) -> dict[int, float]:
    """毛利率：优先 GrossProfit/Rev，回退 (Rev−COGS)/Rev。取不到就没有该年。"""
    rev, gp, cogs = d["revenue"], d["gross_profit"], d["cost_of_revenue"]
    out = {}
    for y, r in rev.items():
        if not r or r <= 0:
            continue
        g = gp.get(y)
        if g is None and y in cogs:
            g = r - cogs[y]
        if g is not None:
            out[y] = g / r
    return out


def ebit_series(d: dict) -> dict[int, float]:
    """营业利润(EBIT)：优先 OperatingIncomeLoss；缺失则用 税前利润+利息费用 回退。
    很多公司(单步式利润表)不报 OperatingIncomeLoss，回退能救回它们。"""
    oi, pretax, intexp = d["operating_income"], d["pretax_income"], d["interest_expense"]
    out = dict(oi)
    for y in pretax:
        if y not in out and y in intexp:
            out[y] = pretax[y] + intexp[y]     # EBIT ≈ 税前 + 利息
    return out


def roic_series(d: dict) -> dict[int, float]:
    """ROIC ≈ NOPAT / 投入资本。
    NOPAT = EBIT × (1 − 有效税率)；投入资本 = 权益 + 总债务 − 现金。"""
    ebit, eq, cash = ebit_series(d), d["equity"], d["cash"]
    ltd, ltdc = d["lt_debt"], d["lt_debt_current"]
    tax, pretax = d["income_tax"], d["pretax_income"]
    out = {}
    for y in ebit:
        if y not in eq:
            continue
        debt = ltd.get(y, 0) + ltdc.get(y, 0)
        invested = eq[y] + debt - cash.get(y, 0)
        if invested <= 0:
            continue
        # 有效税率（缺失或异常则用 21%）
        rate = 0.21
        if y in tax and y in pretax and pretax[y] and pretax[y] > 0:
            r = tax[y] / pretax[y]
            if 0 <= r <= 0.6:
                rate = r
        out[y] = ebit[y] * (1 - rate) / invested
    return out


def net_debt_to_ebitda(d: dict, year: int) -> Optional[float]:
    ebit, dda, cash = ebit_series(d), d["dda"], d["cash"]
    ltd, ltdc = d["lt_debt"], d["lt_debt_current"]
    if year not in ebit or year not in dda:
        return None
    ebitda = ebit[year] + dda[year]
    if ebitda <= 0:
        return None
    net_debt = ltd.get(year, 0) + ltdc.get(year, 0) - cash.get(year, 0)
    return net_debt / ebitda


def cagr(series: dict[int, float], years: list[int]) -> Optional[float]:
    if len(years) < 2:
        return None
    a, b = series.get(years[0]), series.get(years[-1])
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return (b / a) ** (1 / (len(years) - 1)) - 1


# ---------------- L2 防雷（返回 True = 命中地雷 = 剔除） ----------------
def flag_accruals(d: dict, ratio_max: float, years_req: int) -> Optional[bool]:
    ni, ocf = d["net_income"], d["ocf"]
    yrs = _last_n_consecutive({y: 1 for y in ni if y in ocf}, years_req)
    if len(yrs) < years_req:
        return None                       # 数据不足，判不了
    hits = 0
    for y in yrs:
        if ocf[y] and ocf[y] > 0:
            if ni[y] / ocf[y] > ratio_max:
                hits += 1
        elif ni[y] > 0:                   # 有利润却没经营现金流 = 最坏情形
            hits += 1
    return hits == len(yrs)               # 连续每年都命中才算地雷


def flag_ar_growth(d: dict, mult: float, years_req: int) -> Optional[bool]:
    ar, rev = d["receivables"], d["revenue"]
    yrs = _last_n_consecutive({y: 1 for y in ar if y in rev}, years_req + 1)
    if len(yrs) < years_req + 1:
        return None
    hits = 0
    for i in range(1, len(yrs)):
        y0, y1 = yrs[i - 1], yrs[i]
        if not (rev[y0] and ar[y0]) or rev[y0] <= 0 or ar[y0] <= 0:
            continue
        rg = rev[y1] / rev[y0] - 1
        ag = ar[y1] / ar[y0] - 1
        if ag > rg * mult and rg > 0:
            hits += 1
    return hits >= years_req


def flag_net_share_issuance(d: dict, lookback: int) -> Optional[bool]:
    """近 lookback 年净股本不降反升（升幅>2%容差）= 回购被稀释吞掉的信号。
    股本先做量纲+拆股归一。"""
    sh = split_adjust(rescale_1000(d["shares"]))
    yrs = _last_n_consecutive(sh, lookback + 1)
    if len(yrs) < 2:
        return None
    a, b = sh[yrs[0]], sh[yrs[-1]]
    if not a or a <= 0:
        return None
    return (b / a - 1) > 0.02


# ---------------- 打包所有派生信号（供筛选器与体检用） ----------------
def derive(d: dict, cfg: dict) -> dict:
    L2, L3 = cfg["L2_landmines"], cfg["L3_quality"]
    fcf = fcf_series(d)
    gm = gross_margin_series(d)
    roic = roic_series(d)
    ebit = ebit_series(d)
    om = {y: ebit[y] / d["revenue"][y] for y in ebit
          if y in d["revenue"] and d["revenue"][y] > 0}   # 营业利润率(兜底用)

    # 陈旧度锚：以收入最近财年为基准，指标须来自 ref_fy-1 及以后，否则视为缺失。
    # （防 HAL 式：停报毛利后拿 8 年前的旧值蒙混过关。）
    ref_fy = max(d["revenue"]) if d["revenue"] else None
    def _recent(y):
        return ref_fy is not None and y is not None and y >= ref_fy - 1

    # L3: FCF 连续为正年数 + 变异系数（FCF 须为近年数据，否则整体作缺失）
    fcf_years = sorted(fcf)
    if fcf_years and not _recent(fcf_years[-1]):
        streak, fcf_cv = None, None
    else:
        streak = 0
        for y in reversed(fcf_years):
            if fcf[y] > 0:
                streak += 1
            else:
                break
        recent_fcf = [fcf[y] for y in fcf_years[-L3["fcf_positive_years"]:]]
        fcf_cv = (pstdev(recent_fcf) / mean(recent_fcf)
                  if len(recent_fcf) >= 2 and mean(recent_fcf) > 0 else None)

    # L3: 毛利率趋势（首尾比，不下行 = 末 ≥ 首 × 0.95）+ 陈旧度闸
    gm_yrs = _last_n_consecutive(gm, L3["gross_margin_trend_years"])
    gm_trend_ok = (gm[gm_yrs[-1]] >= gm[gm_yrs[0]] * 0.95
                   if len(gm_yrs) >= 2 else None)
    gm_latest = gm[max(gm)] if gm and _recent(max(gm)) else None
    om_latest = om[max(om)] if om and _recent(max(om)) else None

    # L3: ROIC 近 M 年均值（须为近年数据）
    roic_yrs = _last_n_consecutive(roic, L3["roic_lookback_years"])
    roic_avg = (mean([roic[y] for y in roic_yrs])
                if roic_yrs and _recent(max(roic_yrs)) else None)

    # L3: 总资产增速 ≤ 收入增速（近 N 年）
    n = L3["asset_growth_le_revenue_years"]
    a_yrs = _last_n_consecutive(d["assets"], n + 1)
    r_yrs = _last_n_consecutive(d["revenue"], n + 1)
    asset_cagr = cagr(d["assets"], a_yrs) if len(a_yrs) >= 2 else None
    rev_cagr = cagr(d["revenue"], r_yrs) if len(r_yrs) >= 2 else None

    latest = max(fcf) if fcf else (max(d["revenue"]) if d["revenue"] else None)

    return {
        "entity": d.get("_entity"),
        "latest_fy": latest,
        "fcf_latest": fcf.get(latest) if latest else None,
        "fcf_positive_streak": streak,
        "fcf_cv": fcf_cv,
        "gross_margin_latest": gm_latest,
        "op_margin_latest": om_latest,
        "gross_margin_trend_ok": gm_trend_ok,
        "roic_avg": roic_avg,
        "asset_cagr": asset_cagr,
        "rev_cagr": rev_cagr,
        "net_debt_ebitda": net_debt_to_ebitda(d, latest) if latest else None,
        # L2 地雷
        "L2_accruals": flag_accruals(d, L2["ni_to_ocf_ratio_max"], L2["ni_to_ocf_consecutive_years"]),
        "L2_ar_growth": flag_ar_growth(d, L2["ar_vs_rev_growth_multiple"], L2["ar_vs_rev_consecutive_years"]),
        "L2_share_issuance": flag_net_share_issuance(d, L2["net_share_lookback_years"]),
    }
