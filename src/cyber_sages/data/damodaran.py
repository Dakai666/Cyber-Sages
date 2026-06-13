"""Damodaran（NYU Stern）產業估值 multiples——reference 類別的產業比較錨點（決議 2）。

來源是 Aswath Damodaran 公開的美股產業層級資料（年更、免費、出自大師本人之手，與
persona 哲學同源）。multiples 是**美股口徑**，故僅對 US 個股發；台股套用會誤導，
不發（未來可改用其新興市場資料集）。

資料以 **vendored CSV 快照**形式 commit 進 repo（`datasets/damodaran_us_industries.csv`），
runtime 只讀 stdlib csv——年更資料無需每 run 打網路 / 解析舊式 .xls。

刷新（年更）流程（手動，一次性）——完整可重現指令：

    uv run --with lxml python -c "
    import httpx, pandas as pd, io, re, csv
    h={'User-Agent':'Mozilla/5.0'}
    html = httpx.get('https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html',
                     headers=h, timeout=25, follow_redirects=True).text
    t = max(pd.read_html(io.StringIO(html)), key=lambda d: d.shape[0])
    t.columns = [re.sub(r'\\s+',' ',str(c)).strip() for c in t.iloc[0]]; t = t.iloc[1:]
    def num(x):
        x=re.sub(r'[%,\\$]','',str(x)).strip()
        try: return float(x)
        except: return ''
    rows=[{'industry': re.sub(r'\\s+',' ',str(r['Industry Name'])).strip(),
           'num_firms': num(r['Number of firms']), 'trailing_pe': num(r['Trailing PE']),
           'forward_pe': num(r['Forward PE']),
           'expected_growth_5y_pct': num(r['Expected growth - next 5 years']),
           'peg': num(r['PEG Ratio'])}
          for _,r in t.iterrows() if str(r['Industry Name']).strip().lower() not in ('','nan')]
    with open('src/cyber_sages/data/datasets/damodaran_us_industries.csv','w',newline='',encoding='utf-8') as f:
        f.write('# Damodaran (NYU Stern) US industry valuation multiples — vendored snapshot\\n')
        f.write('# source: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html\\n')
        f.write('# retrieved_date: <YYYY-MM-DD> ; refresh: 年更，重跑本指令\\n')
        f.write('# source typos preserved verbatim (e.g. \\'Heathcare\\' = Damodaran 原始 typo)\\n')
        w=csv.DictWriter(f, fieldnames=['industry','num_firms','trailing_pe','forward_pe','expected_growth_5y_pct','peg'])
        w.writeheader(); [w.writerow(r) for r in rows]
    "

刷新後手動把 `<YYYY-MM-DD>` 改成當日，並 commit。產業名保留 Damodaran 原始拼寫（含 typo），
以維持 source fidelity；對映在 _INDUSTRY_MAP 處理。
"""

from __future__ import annotations

import csv
import logging
import math
import re
from functools import lru_cache
from pathlib import Path

from cyber_sages.data.evidence import Evidence

logger = logging.getLogger(__name__)

_CSV = Path(__file__).parent / "datasets" / "damodaran_us_industries.csv"
_SOURCE = "Damodaran NYU Stern US industry data (annual)"
_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html"

# yfinance `industry`（小寫）→ Damodaran 產業名。刻意只對映有把握的常見產業；
# 無把握則不發（寧缺勿給錯產業的 benchmark）。涵蓋面可隨遇到的標的逐步擴充。
_INDUSTRY_MAP: dict[str, str] = {
    "semiconductors": "Semiconductor",
    "semiconductor equipment & materials": "Semiconductor Equip",
    "consumer electronics": "Electronics (Consumer & Office)",
    "computer hardware": "Computers/Peripherals",
    "software—infrastructure": "Software (System & Application)",
    "software—application": "Software (System & Application)",
    "information technology services": "Computer Services",
    "internet content & information": "Software (Internet)",
    "internet retail": "Retail (General)",
    "drug manufacturers—general": "Drugs (Pharmaceutical)",
    "drug manufacturers—specialty & generic": "Drugs (Pharmaceutical)",
    "biotechnology": "Drugs (Biotechnology)",
    "medical devices": "Healthcare Products",
    "auto manufacturers": "Auto & Truck",
    "auto parts": "Auto Parts",
    "banks—diversified": "Bank (Money Center)",
    "banks—regional": "Banks (Regional)",
    "capital markets": "Brokerage & Investment Banking",
    "asset management": "Investments & Asset Management",
    "insurance—life": "Insurance (Life)",
    "insurance—property & casualty": "Insurance (Prop/Cas.)",
    "aerospace & defense": "Aerospace/Defense",
    "restaurants": "Restaurant/Dining",
    "telecom services": "Telecom. Services",
    "beverages—non-alcoholic": "Beverage (Soft)",
    "beverages—wineries & distilleries": "Beverage (Alcoholic)",
    "tobacco": "Tobacco",
    "household & personal products": "Household Products",
    "oil & gas integrated": "Oil/Gas (Integrated)",
    "oil & gas e&p": "Oil/Gas (Production and Exploration)",
    "steel": "Steel",
    "specialty retail": "Retail (Special Lines)",
    "grocery stores": "Retail (Grocery and Food)",
    "entertainment": "Entertainment",
    "utilities—regulated electric": "Utility (General)",
}


@lru_cache(maxsize=1)
def _industries() -> dict[str, dict[str, float]]:
    """讀 vendored CSV（跳過 # 註解行）→ {industry: {欄位: float}}。"""
    out: dict[str, dict[str, float]] = {}
    try:
        with open(_CSV, encoding="utf-8") as f:
            data_lines = [ln for ln in f if not ln.startswith("#")]
        for row in csv.DictReader(data_lines):
            vals: dict[str, float] = {}
            for k, v in row.items():
                if k == "industry":
                    continue
                try:
                    fv = float(v)
                except (ValueError, TypeError):
                    continue
                if not math.isnan(fv):  # 快照對某些產業/欄位無值（寫成 nan），略過不發
                    vals[k] = fv
            out[row["industry"]] = vals
    except OSError as e:
        logger.warning("Damodaran dataset unreadable (%s); industry benchmarks skipped", e)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def map_industry(yf_industry: str | None) -> str | None:
    """yfinance industry → Damodaran 產業名；無對映回 None。"""
    if not yf_industry:
        return None
    return _INDUSTRY_MAP.get(_norm(yf_industry))


def industry_benchmark_evidence(yf_industry: str | None, url: str) -> list[Evidence]:
    """US 個股的產業 + 全市場估值 benchmark（reference 類別，二手年度聚合）。

    產業有對映且快照有資料才發產業欄位；全市場 baseline（Total Market）只要快照有就發，
    讓 US 標的至少有一個估值對照基準。台股不呼叫此函式（US-only）。"""
    table = _industries()
    if not table:
        return []
    out: list[Evidence] = []

    def emit(field, value, unit, note):
        out.append(Evidence(
            category="reference", field=field, value=round(float(value), 2),
            unit=unit, source=_SOURCE, url=_URL, note=note))

    dam = map_industry(yf_industry)
    if dam and dam in table:
        m = table[dam]
        n = int(m.get("num_firms", 0))
        tag = f"{dam}（{n} 家美股同業，年度聚合，二手）"
        if "trailing_pe" in m:
            emit("industry_pe_trailing", m["trailing_pe"], "x", f"產業 trailing P/E：{tag}")
        if "forward_pe" in m:
            emit("industry_pe_forward", m["forward_pe"], "x", f"產業 forward P/E：{tag}")
        if "peg" in m:
            emit("industry_peg", m["peg"], "x", f"產業 PEG：{tag}")
        if "expected_growth_5y_pct" in m:
            emit("industry_growth_5y_pct", m["expected_growth_5y_pct"], "%",
                 f"產業 5 年預估年成長：{tag}（Damodaran 預估，非公司 actual；peg = pe / growth 的分母）")
    elif yf_industry:
        # 有 industry 但對映不到 → 只給市場 baseline；記 debug 供未來擴充 _INDUSTRY_MAP
        logger.debug("Damodaran: unmapped yfinance industry %r → industry benchmark skipped",
                     yf_industry)

    market = table.get("Total Market")
    if market and "trailing_pe" in market:
        emit("market_pe_trailing", market["trailing_pe"], "x",
             "全美股市場 trailing P/E（估值基準，二手年度聚合）")
    return out
