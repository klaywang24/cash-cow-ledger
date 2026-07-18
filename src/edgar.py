"""
SEC EDGAR companyfacts client + annual financial metric extraction.

Design notes (every XBRL pitfall lives here):
- One economic concept may be filed under different us-gaap tags across companies
  and years, so each concept gets a list of candidate tags with ordered fallback.
- Annual values only: form=10-K with fp=FY; flow items (revenue, cash flow) must
  span roughly one year (~365 days), excluding quarterly and overlapping periods.
- A fiscal year may have several entries (original filing plus later restatements);
  the one with the latest `end` date for that year wins.
- Missing means missing: return None, never guess, never fill with zero.
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

    # ---------- low level: rate-limited GET ----------
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

    # ---------- annual series extraction ----------
    @staticmethod
    def annual_series(facts: dict, concepts: list[str], unit: str,
                      flow: bool) -> dict[int, float]:
        """
        Return {fiscal_year: value}. `concepts` is a candidate tag list: earlier tags
        take precedence, later tags are used ONLY to fill years the earlier ones lack.
        A concept's XBRL tag changes when accounting standards change (e.g. ASC 606
        revenue), so taking a single tag silently drops the early history.
        flow=True marks flow items (period must be ~1 year); flow=False marks balances.
        """
        usgaap = facts.get("facts", {}).get("us-gaap", {})
        merged: dict[int, float] = {}          # fy -> val (first writer wins; earlier tags win)
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
                    if not (350 <= days <= 380):   # whole-year periods only
                        continue
                fy = e.get("fy")
                if fy is None:
                    fy = dt.date.fromisoformat(end).year
                # Several entries per fiscal year: keep the latest `end` (newest restatement)
                if fy not in by_year or end > by_year[fy][0]:
                    by_year[fy] = (end, val)
            for fy, (_, val) in by_year.items():
                merged.setdefault(fy, val)     # only fill years earlier tags lack
        return merged


# ---- Candidate tag library: one economic concept -> several possible XBRL tags ----
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
    # Weighted diluted shares: better us-gaap coverage and retroactively split-adjusted,
    # so the "net share change" test is not fooled by splits
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfSharesOutstandingBasic"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
}

FLOW = {"revenue", "gross_profit", "cost_of_revenue", "net_income", "ocf",
        "capex", "operating_income", "interest_expense", "dda", "income_tax",
        "pretax_income", "shares"}   # weighted share count is a duration concept


def extract_all(edgar: "Edgar", ticker: str) -> Optional[dict]:
    """Extract all annual series for one ticker. Returns {concept: {fiscal_year: value}};
    concepts that cannot be resolved come back as {}."""
    facts = edgar.companyfacts(ticker)
    if facts is None:
        return None
    out = {}
    for concept, tags in TAGS.items():
        unit = "shares" if concept == "shares" else "USD"
        out[concept] = edgar.annual_series(facts, tags, unit, flow=(concept in FLOW))
    out["_entity"] = facts.get("entityName", ticker)
    return out
