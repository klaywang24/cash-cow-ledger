"""
The five-layer funnel, L1-L5. Every ticker runs the full funnel and produces one record
carrying the pass/reject reason at each layer.
Rejection is not deletion: the stage and reason are recorded so anyone can audit why a
name did not make it (see config.data_policy).
"""
from __future__ import annotations
from typing import Optional
from src.metrics import derive


def screen_us(d: dict, cfg: dict) -> dict:
    """Run L2 landmines and L3 quality on one US name (EDGAR data `d`). Returns a record.
    L1 is the caller's job (the universe), L4 needs prices, L5 ranking happens on aggregation."""
    m = derive(d, cfg)
    rec = {"ticker": None, "entity": m["entity"], **m,
           "stage": None, "status": None, "reason": None,
           "data_incomplete": False}

    L3 = cfg["L3_quality"]
    L2 = cfg["L2_landmines"]

    # ---- Completeness gate: any missing required field flags the name out of scoring ----
    # Either gross margin or operating margin suffices (payment networks have no COGS,
    # so operating margin is the fallback)
    has_margin = m["gross_margin_latest"] is not None or m["op_margin_latest"] is not None
    need = {"consecutive positive FCF": m["fcf_positive_streak"] is not None,
            "margin": has_margin,
            "ROIC": m["roic_avg"] is not None,
            "asset growth": m["asset_cagr"] is not None,
            "revenue growth": m["rev_cagr"] is not None}
    missing = [k for k, ok in need.items() if not ok]
    if missing:
        rec.update(stage="L3", status="data_incomplete", data_incomplete=True,
                   reason="missing: " + ", ".join(missing))
        return rec

    # ---- L2 landmines (any single hit removes the name) ----
    landmines = []
    if m["L2_accruals"]:
        landmines.append("earnings far exceed operating cash flow (consecutive)")
    if m["L2_ar_growth"]:
        landmines.append("receivables growth > 2x revenue growth (consecutive)")
    if m["L2_share_issuance"]:
        landmines.append("share count rising despite buybacks (swallowed by dilution)")
    nde = m["net_debt_ebitda"]
    if nde is not None and nde > L2["net_debt_to_ebitda_max"]:
        landmines.append(f"net debt/EBITDA={nde:.1f} > {L2['net_debt_to_ebitda_max']}")
    if landmines:
        rec.update(stage="L2", status="rejected", reason="landmine: " + "; ".join(landmines))
        return rec

    # ---- L3 quality inclusion (all must be satisfied) ----
    fails = []
    if m["fcf_positive_streak"] < L3["fcf_positive_years"]:
        fails.append(f"positive FCF only {m['fcf_positive_streak']}y < {L3['fcf_positive_years']}y")
    if m["fcf_cv"] is None or m["fcf_cv"] > L3["fcf_cv_max"]:
        fails.append(f"FCF coeff. of variation {_f(m['fcf_cv'])} > {L3['fcf_cv_max']}")
    if m["gross_margin_latest"] is not None:
        if m["gross_margin_latest"] < L3["gross_margin_min"]:
            fails.append(f"gross margin {m['gross_margin_latest']*100:.0f}% < {L3['gross_margin_min']*100:.0f}%")
        if m["gross_margin_trend_ok"] is False:
            fails.append("10y gross-margin trend declining")
    else:   # no gross margin available -> fall back to operating margin
        if m["op_margin_latest"] < L3["operating_margin_min_fallback"]:
            fails.append(f"operating margin {m['op_margin_latest']*100:.0f}% < {L3['operating_margin_min_fallback']*100:.0f}%")
    if m["roic_avg"] < L3["roic_min"]:
        fails.append(f"avg ROIC {m['roic_avg']*100:.0f}% < {L3['roic_min']*100:.0f}%")
    if m["asset_cagr"] is not None and m["rev_cagr"] is not None \
            and m["asset_cagr"] > m["rev_cagr"] + 0.01:   # asset growth > revenue growth (1% tolerance)
        fails.append(f"asset growth {m['asset_cagr']*100:.0f}% > revenue growth {m['rev_cagr']*100:.0f}%")
    if fails:
        rec.update(stage="L3", status="rejected", reason="quality: " + "; ".join(fails))
        return rec

    rec.update(stage="L3", status="pass_quality", reason="passed landmines + quality")
    return rec


def apply_valuation(rec: dict, pe: Optional[float], fcf_yield: Optional[float],
                    ust10y: float, cfg: dict) -> dict:
    """L4 valuation: FCF yield ≥ Treasury, or P/E ≤ ceiling (whichever passes). Updates rec in place."""
    L4 = cfg["L4_valuation"]
    rec["pe"], rec["fcf_yield"], rec["ust10y"] = pe, fcf_yield, ust10y
    if pe is None and fcf_yield is None:
        rec.update(stage="L4", status="data_incomplete", data_incomplete=True,
                   reason="valuation data unavailable (no price/PE)")
        return rec
    pass_fcfy = fcf_yield is not None and fcf_yield >= ust10y
    pass_pe = pe is not None and 0 < pe <= L4["pe_max"]
    if pass_fcfy or pass_pe:
        why = []
        if pass_fcfy: why.append(f"FCF yield {fcf_yield*100:.1f}% >= UST {ust10y*100:.1f}%")
        if pass_pe: why.append(f"P/E {pe:.0f} <= {L4['pe_max']}")
        rec.update(stage="L4", status="pass_valuation", reason=" / ".join(why))
    else:
        rec.update(stage="L4", status="rejected",
                   reason=f"valuation: FCF yield {_f(fcf_yield,pct=True)} < UST {ust10y*100:.1f}% and P/E {_f(pe)} > {L4['pe_max']}")
    return rec


def composite_score(rec: dict, cfg: dict) -> float:
    """L5 composite score: factor weights applied to within-pool normalized ranks.
    Raw factors are computed here; normalization happens at the ranking stage."""
    return 0.0   # placeholder; actual scoring is normalized across the pool at run time


def _f(v, pct=False):
    if v is None:
        return "--"
    return f"{v*100:.1f}%" if pct else f"{v:.2f}"
