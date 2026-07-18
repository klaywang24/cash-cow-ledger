"""
Derive every L2-L5 signal from the EDGAR annual series.

Two hard rules:
- Missing data returns None; never substitute 0 or an estimate (see config.data_policy).
- Series are magnitude-normalized and split-adjusted before use (the two as-filed XBRL pitfalls).
"""
from __future__ import annotations
import math
from statistics import mean, pstdev
from typing import Optional

# Common split factors (forward splits; reverse splits use the reciprocal)
_SPLIT_FACTORS = [2, 3, 3/2, 4, 5, 6, 7, 8, 10, 15, 20]


def rescale_1000(series: dict[int, float]) -> dict[int, float]:
    """Fix thousand-fold magnitude drift within one series (thousands / millions / units
    mixed, e.g. a company that starts reporting in millions from some year).
    Rescale only when a year deviates from the baseline by approximately an exact 1000^n
    (within tolerance); non-thousand-fold changes from splits or growth (e.g. a 40x
    ten-year increase) are left alone for split_adjust to handle."""
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
            # Only a near-integer (i.e. an almost exact thousand-fold) counts as magnitude drift
            out[y] = v * (1000 ** k) if k != 0 and abs(frac - k) < 0.12 else v
        else:
            out[y] = v
    return out


def split_adjust(series: dict[int, float]) -> dict[int, float]:
    """Restate share-count split discontinuities onto the latest year's basis (shares only).
    Scan adjacent-year ratios newest to oldest; when one is close to a known split factor,
    scale all earlier years accordingly."""
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
            if abs(r - sf) / sf < 0.08:          # forward split: this year ≈ last year × sf
                factor *= sf
                break
            if abs(r - 1 / sf) / (1 / sf) < 0.08:  # reverse split
                factor /= sf
                break
        out[years[i - 1]] = series[years[i - 1]] * factor
    return out


def _last_n_consecutive(series: dict[int, float], n: int) -> list[int]:
    """Return the most recent n CONSECUTIVE fiscal years (truncating at any gap).
    Returns fewer than n if that is all there is."""
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
    """Gross margin: GrossProfit/Revenue preferred, falling back to (Revenue−COGS)/Revenue.
    A year with neither is simply absent."""
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
    """EBIT: OperatingIncomeLoss preferred; falls back to pre-tax income + interest expense.
    Many companies (single-step income statements) never file OperatingIncomeLoss, and the
    fallback recovers them."""
    oi, pretax, intexp = d["operating_income"], d["pretax_income"], d["interest_expense"]
    out = dict(oi)
    for y in pretax:
        if y not in out and y in intexp:
            out[y] = pretax[y] + intexp[y]     # EBIT ≈ pre-tax + interest
    return out


def roic_series(d: dict) -> dict[int, float]:
    """ROIC ≈ NOPAT / invested capital.
    NOPAT = EBIT × (1 − effective tax rate); invested capital = equity + total debt − cash."""
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
        # Effective tax rate (21% when missing or out of range)
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


# ---------------- L2 landmines (True = hit = the name is excluded) ----------------
def flag_accruals(d: dict, ratio_max: float, years_req: int) -> Optional[bool]:
    ni, ocf = d["net_income"], d["ocf"]
    yrs = _last_n_consecutive({y: 1 for y in ni if y in ocf}, years_req)
    if len(yrs) < years_req:
        return None                       # insufficient data to judge
    hits = 0
    for y in yrs:
        if ocf[y] and ocf[y] > 0:
            if ni[y] / ocf[y] > ratio_max:
                hits += 1
        elif ni[y] > 0:                   # profit but no operating cash flow = worst case
            hits += 1
    return hits == len(yrs)               # only a hit in every year of the run counts


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
    """Net share count rising rather than falling over `lookback` years (>2% tolerance)
    signals buybacks swallowed by dilution. Shares are magnitude- and split-normalized first."""
    sh = split_adjust(rescale_1000(d["shares"]))
    yrs = _last_n_consecutive(sh, lookback + 1)
    if len(yrs) < 2:
        return None
    a, b = sh[yrs[0]], sh[yrs[-1]]
    if not a or a <= 0:
        return None
    return (b / a - 1) > 0.02


# ---------------- Bundle every derived signal (for the screener and health probes) ----------------
def derive(d: dict, cfg: dict) -> dict:
    L2, L3 = cfg["L2_landmines"], cfg["L3_quality"]
    fcf = fcf_series(d)
    gm = gross_margin_series(d)
    roic = roic_series(d)
    ebit = ebit_series(d)
    om = {y: ebit[y] / d["revenue"][y] for y in ebit
          if y in d["revenue"] and d["revenue"][y] > 0}   # operating margin (fallback)

    # Staleness anchor: metrics must come from ref_fy-1 or later, where ref_fy is the latest
    # revenue fiscal year; otherwise treat as missing. (Guards against a company that stops
    # reporting gross profit and then passes on an eight-year-old value.)
    ref_fy = max(d["revenue"]) if d["revenue"] else None
    def _recent(y):
        return ref_fy is not None and y is not None and y >= ref_fy - 1

    # L3: consecutive positive-FCF years + coefficient of variation (stale FCF = all missing)
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

    # L3: gross-margin trend (last vs first; "not declining" = last ≥ first × 0.95) + staleness gate
    gm_yrs = _last_n_consecutive(gm, L3["gross_margin_trend_years"])
    gm_trend_ok = (gm[gm_yrs[-1]] >= gm[gm_yrs[0]] * 0.95
                   if len(gm_yrs) >= 2 else None)
    gm_latest = gm[max(gm)] if gm and _recent(max(gm)) else None
    om_latest = om[max(om)] if om and _recent(max(om)) else None

    # L3: M-year average ROIC (must be recent data)
    roic_yrs = _last_n_consecutive(roic, L3["roic_lookback_years"])
    roic_avg = (mean([roic[y] for y in roic_yrs])
                if roic_yrs and _recent(max(roic_yrs)) else None)

    # L3: asset growth ≤ revenue growth (trailing N years)
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
        # L2 landmines
        "L2_accruals": flag_accruals(d, L2["ni_to_ocf_ratio_max"], L2["ni_to_ocf_consecutive_years"]),
        "L2_ar_growth": flag_ar_growth(d, L2["ar_vs_rev_growth_multiple"], L2["ar_vs_rev_consecutive_years"]),
        "L2_share_issuance": flag_net_share_issuance(d, L2["net_share_lookback_years"]),
    }
