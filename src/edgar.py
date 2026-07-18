"""
SEC EDGAR companyfacts 客户端 + 年度财务指标提取。

设计要点（XBRL 的坑都在这里）：
- 同一经济概念在不同公司/不同年份可能用不同 us-gaap 标签申报，故每个
  概念给一组候选标签，按顺序回退。
- 只取年度值：优先 form=10-K 且 fp=FY；流量项（收入/现金流）要求期间
  约等于一年（~365 天），排除季度/半年重叠段。
- 每个财年可能有多条（原报 + 后续重述），取“该财年 end 日期最新的一条”。
- 缺失就是缺失：取不到返回 None，绝不猜、绝不填 0。
"""
from __future__ import annotations
import json
import time
import pathlib
import datetime as dt
from typing import Optional

import requests

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"


class Edgar:
    def __init__(self, user_agent: str, rate_limit_per_sec: float = 8,
                 cache_dir: str = "data/fundamentals_cache", cache_days: int = 7):
        self.headers = {"User-Agent": user_agent,
                        "Accept-Encoding": "gzip, deflate"}
        self._min_interval = 1.0 / rate_limit_per_sec
        self._last_call = 0.0
        self.cache_dir = pathlib.Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_days = cache_days
        self._ticker_map: Optional[dict] = None

    # ---------- 低层：限流 GET ----------
    def _get(self, url: str) -> requests.Response:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        r = requests.get(url, headers=self.headers, timeout=30)
        self._last_call = time.monotonic()
        r.raise_for_status()
        return r

    # ---------- ticker -> CIK ----------
    def ticker_to_cik(self, ticker: str) -> Optional[str]:
        if self._ticker_map is None:
            cache = self.cache_dir / "company_tickers.json"
            if cache.exists() and self._fresh(cache):
                data = json.loads(cache.read_text())
            else:
                data = self._get(SEC_TICKERS_URL).json()
                cache.write_text(json.dumps(data))
            # {idx: {cik_str, ticker, title}}
            self._ticker_map = {v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                                for v in data.values()}
        return self._ticker_map.get(ticker.upper())

    # ---------- companyfacts ----------
    def companyfacts(self, ticker: str) -> Optional[dict]:
        cik10 = self.ticker_to_cik(ticker)
        if cik10 is None:
            return None
        cache = self.cache_dir / f"CIK{cik10}.json"
        if cache.exists() and self._fresh(cache):
            return json.loads(cache.read_text())
        try:
            data = self._get(COMPANYFACTS_URL.format(cik10=cik10)).json()
        except requests.HTTPError:
            return None
        cache.write_text(json.dumps(data))
        return data

    def _fresh(self, path: pathlib.Path) -> bool:
        age = time.time() - path.stat().st_mtime
        return age < self.cache_days * 86400

    # ---------- 年度序列提取 ----------
    @staticmethod
    def annual_series(facts: dict, concepts: list[str], unit: str,
                      flow: bool) -> dict[int, float]:
        """
        返回 {财年: 值}。concepts 为候选标签列表：靠前的标签优先，靠后的
        标签仅用来【补齐前者缺失的年份】——因为同一概念的 XBRL 标签会随
        会计准则变更（如 ASC 606 收入）而更换，单取一个标签会丢早年历史。
        flow=True 表示流量项（要求期间≈1年）；flow=False 表示时点项（余额）。
        """
        usgaap = facts.get("facts", {}).get("us-gaap", {})
        merged: dict[int, float] = {}          # fy -> val（先到先得，靠前标签赢）
        for tag in concepts:
            node = usgaap.get(tag)
            if not node:
                continue
            entries = node.get("units", {}).get(unit)
            if not entries:
                continue
            by_year: dict[int, tuple] = {}     # fy -> (end_date, val)
            for e in entries:
                if e.get("form") not in ("10-K", "10-K/A"):
                    continue
                if e.get("fp") != "FY":
                    continue
                val, end = e.get("val"), e.get("end")
                if val is None or end is None:
                    continue
                if flow:
                    start = e.get("start")
                    if not start:
                        continue
                    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
                    if not (350 <= days <= 380):   # 只要整年段
                        continue
                fy = e.get("fy")
                if fy is None:
                    fy = dt.date.fromisoformat(end).year
                # 同财年多条：取 end 最新的（= 最近一次重述/申报）
                if fy not in by_year or end > by_year[fy][0]:
                    by_year[fy] = (end, val)
            for fy, (_, val) in by_year.items():
                merged.setdefault(fy, val)     # 只补前面标签没有的年份
        return merged


# ---- 候选标签库：一个经济概念 -> 多个可能的 XBRL 标签 ----
TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold",
                        "CostOfGoodsSold"],
    "net_income": ["NetIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "operating_income": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating",
                         "InterestAndDebtExpense", "InterestIncomeExpenseNet"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "lt_debt_current": ["LongTermDebtCurrent", "DebtCurrent"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "dda": ["DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization"],
    # 用加权稀释股本：us-gaap 覆盖更全，且按拆股回溯调整，做“净股本变化”才不被拆股骗
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfSharesOutstandingBasic"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
}

FLOW = {"revenue", "gross_profit", "cost_of_revenue", "net_income", "ocf",
        "capex", "operating_income", "interest_expense", "dda", "income_tax",
        "pretax_income", "shares"}   # 加权股本是区间(duration)概念


def extract_all(edgar: "Edgar", ticker: str) -> Optional[dict]:
    """提取一只票的全部年度序列。返回 {概念: {财年: 值}}，取不到的概念为 {}。"""
    facts = edgar.companyfacts(ticker)
    if facts is None:
        return None
    out = {}
    for concept, tags in TAGS.items():
        unit = "shares" if concept == "shares" else "USD"
        out[concept] = edgar.annual_series(facts, tags, unit, flow=(concept in FLOW))
    out["_entity"] = facts.get("entityName", ticker)
    return out
