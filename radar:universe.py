"""적격 종목 명단: Nasdaq Trader 심볼 디렉터리 (NASDAQ / NYSE / NYSE American)."""
from __future__ import annotations
import io, re
import pandas as pd
import requests

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
HALT_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
OTHER_EXCH = {"A": "NYSE_AMERICAN", "N": "NYSE"}   # P(Arca)·Z(Cboe)는 범위 밖
NON_COMMON = re.compile(
    r"\b(warrants?|rights?|units?|preferred|notes?|debentures?|subordinated|"
    r"depositary shares?.{0,40}preferred|trust|etn|fund)\b", re.I)
HEADERS = {"User-Agent": "microcap-radar/1.0"}

def _read(url: str) -> pd.DataFrame:
    txt = requests.get(url, headers=HEADERS, timeout=30).text
    lines = [l for l in txt.splitlines() if not l.startswith("File Creation Time")]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str).fillna("")

def load_universe(exchanges: list[str]) -> tuple[pd.DataFrame, dict]:
    """반환: (ticker,name,exchange 컬럼 DataFrame, 감사용 카운트 dict)"""
    n = _read(NASDAQ_URL)
    o = _read(OTHER_URL)
    a = pd.DataFrame({"ticker": n["Symbol"], "name": n["Security Name"], "exchange": "NASDAQ",
                      "etf": n["ETF"], "test": n["Test Issue"]})
    b = pd.DataFrame({"ticker": o["ACT Symbol"], "name": o["Security Name"],
                      "exchange": o["Exchange"].map(OTHER_EXCH), "etf": o["ETF"], "test": o["Test Issue"]})
    raw = pd.concat([a, b], ignore_index=True)
    audit = {"raw_rows": len(raw)}
    raw = raw[raw["exchange"].isin(exchanges)]
    audit["in_exchanges"] = len(raw)
    reasons = {
        "etf": raw["etf"].eq("Y"),
        "test_issue": raw["test"].eq("Y"),
        "non_common_name": raw["name"].str.contains(NON_COMMON),
        "bad_symbol": raw["ticker"].str.contains(r"[\$\^ ]") | raw["ticker"].eq(""),
    }
    drop = pd.Series(False, index=raw.index)
    audit["excluded"] = {}
    for k, m in reasons.items():
        audit["excluded"][k] = int((m & ~drop).sum())
        drop |= m
    uni = raw[~drop].drop_duplicates("ticker").reset_index(drop=True)
    audit["unique_eligible"] = len(uni)
    return uni[["ticker", "name", "exchange"]], audit

def fetch_halts() -> set[str]:
    """현재 거래정지 중인 티커(best-effort: 재개 시각이 비어 있는 항목). 실패 시 빈 집합."""
    try:
        xml = requests.get(HALT_URL, headers=HEADERS, timeout=20).text
        out = set()
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
            sym = re.search(r"IssueSymbol>([^<]+)<", item)
            res = re.search(r"ResumptionTradeTime>([^<]*)<", item)
            if sym and (not res or not res.group(1).strip()):
                out.add(sym.group(1).strip())
        return out
    except Exception:
        return set()
