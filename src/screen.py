"""
五层筛选漏斗 L1-L5。每只票走完漏斗，产出一条带【每层通过/淘汰原因】的记录。
淘汰不是丢弃：记录 stage 与 reason，让人能复核为什么某只没进来（对应 data_policy）。
"""
from __future__ import annotations
from typing import Optional
from src.metrics import derive


def screen_us(d: dict, cfg: dict) -> dict:
    """对一只美股（EDGAR 数据 d）跑 L2-L3 质量与防雷。返回 record。
    L1 由调用方（宇宙）负责，L4 估值需价格另算，L5 排名在汇总时做。"""
    m = derive(d, cfg)
    rec = {"ticker": None, "entity": m["entity"], **m,
           "stage": None, "status": None, "reason": None,
           "data_incomplete": False}

    L3 = cfg["L3_quality"]
    L2 = cfg["L2_landmines"]

    # ---- 数据完整性闸：必需字段缺失 → 标记，不参与打分 ----
    need = {"FCF连续为正": m["fcf_positive_streak"] is not None,
            "毛利率": m["gross_margin_latest"] is not None,
            "ROIC": m["roic_avg"] is not None,
            "资产增速": m["asset_cagr"] is not None,
            "收入增速": m["rev_cagr"] is not None}
    missing = [k for k, ok in need.items() if not ok]
    if missing:
        rec.update(stage="L3", status="data_incomplete", data_incomplete=True,
                   reason="缺失: " + ",".join(missing))
        return rec

    # ---- L2 防雷（任一命中即剔除）----
    landmines = []
    if m["L2_accruals"]:
        landmines.append("利润远超经营现金流(连续)")
    if m["L2_ar_growth"]:
        landmines.append("应收增速>收入×2(连续)")
    if m["L2_share_issuance"]:
        landmines.append("净股本不降反升(回购被稀释吞掉)")
    nde = m["net_debt_ebitda"]
    if nde is not None and nde > L2["net_debt_to_ebitda_max"]:
        landmines.append(f"净负债/EBITDA={nde:.1f}>{L2['net_debt_to_ebitda_max']}")
    if landmines:
        rec.update(stage="L2", status="rejected", reason="防雷: " + "; ".join(landmines))
        return rec

    # ---- L3 质量入选（须全部满足）----
    fails = []
    if m["fcf_positive_streak"] < L3["fcf_positive_years"]:
        fails.append(f"FCF连续为正{m['fcf_positive_streak']}<{L3['fcf_positive_years']}年")
    if m["fcf_cv"] is None or m["fcf_cv"] > L3["fcf_cv_max"]:
        fails.append(f"FCF变异系数{_f(m['fcf_cv'])}>{L3['fcf_cv_max']}")
    if m["gross_margin_latest"] < L3["gross_margin_min"]:
        fails.append(f"毛利率{m['gross_margin_latest']*100:.0f}%<{L3['gross_margin_min']*100:.0f}%")
    if m["gross_margin_trend_ok"] is False:
        fails.append("毛利率10年趋势下行")
    if m["roic_avg"] < L3["roic_min"]:
        fails.append(f"ROIC均值{m['roic_avg']*100:.0f}%<{L3['roic_min']*100:.0f}%")
    if m["asset_cagr"] is not None and m["rev_cagr"] is not None \
            and m["asset_cagr"] > m["rev_cagr"] + 0.01:   # 资产增速>收入增速(1%容差)
        fails.append(f"资产增速{m['asset_cagr']*100:.0f}%>收入增速{m['rev_cagr']*100:.0f}%")
    if fails:
        rec.update(stage="L3", status="rejected", reason="质量: " + "; ".join(fails))
        return rec

    rec.update(stage="L3", status="pass_quality", reason="通过防雷+质量")
    return rec


def apply_valuation(rec: dict, pe: Optional[float], fcf_yield: Optional[float],
                    ust10y: float, cfg: dict) -> dict:
    """L4 估值：FCF收益率≥美债 或 PE≤上限（取宽松者）。就地更新 rec。"""
    L4 = cfg["L4_valuation"]
    rec["pe"], rec["fcf_yield"], rec["ust10y"] = pe, fcf_yield, ust10y
    if pe is None and fcf_yield is None:
        rec.update(stage="L4", status="data_incomplete", data_incomplete=True,
                   reason="估值数据缺失(价格/PE取不到)")
        return rec
    pass_fcfy = fcf_yield is not None and fcf_yield >= ust10y
    pass_pe = pe is not None and 0 < pe <= L4["pe_max"]
    if pass_fcfy or pass_pe:
        why = []
        if pass_fcfy: why.append(f"FCF收益率{fcf_yield*100:.1f}%≥美债{ust10y*100:.1f}%")
        if pass_pe: why.append(f"PE {pe:.0f}≤{L4['pe_max']}")
        rec.update(stage="L4", status="pass_valuation", reason=" / ".join(why))
    else:
        rec.update(stage="L4", status="rejected",
                   reason=f"估值: FCF收益率{_f(fcf_yield,pct=True)}<美债{ust10y*100:.1f}% 且 PE {_f(pe)}>{L4['pe_max']}")
    return rec


def composite_score(rec: dict, cfg: dict) -> float:
    """L5 综合得分：各因子分位加权（在候选池内相对排名，汇总时归一）。
    这里先算原始因子值，归一化在 rank 阶段做。"""
    return 0.0   # 占位；实际打分在 run 阶段跨候选池归一后计算


def _f(v, pct=False):
    if v is None:
        return "--"
    return f"{v*100:.1f}%" if pct else f"{v:.2f}"
