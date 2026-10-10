"""SEC EDGAR 클라이언트 + 공시 분류. 원문(EDGAR)을 우선하며 모든 수치는 '검출'이지 '검증'이 아니다."""
from __future__ import annotations
import re, threading, time
from datetime import date, datetime, timedelta, timezone
import requests

FOREIGN_FORMS = {"6-K", "20-F", "F-1", "F-3", "F-1/A", "F-3/A", "424B1", "FWP"}
REG_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A", "S-3", "S-3/A", "F-3", "F-3/A"}
KEYWORDS = {
    "ATM": r"at[- ]the[- ]market|equity distribution agreement|sales agreement",
    "ELOC": r"equity line of credit|standby equity|equity purchase agreement",
    "PIPE": r"private placement|securities purchase agreement",
    "RESALE": r"resale|selling (stock|share)holders?",
    "WARRANT": r"pre-funded warrants?|warrants? to purchase",
    "CONVERT": r"convertible (promissory )?(notes?|debentures?|preferred)",
    "REVERSE_SPLIT": r"reverse (stock |share )?split",
    "GOING_CONCERN": r"going concern|substantial doubt",
    "LISTING": r"minimum bid price|stockholders.? equity requirement|delisting",
}
ITEM_FLAGS = {"1.01": "중요계약", "2.02": "실적", "3.01": "상장유지 통지", "3.02": "비등록 주식 발행",
              "5.03": "정관 변경(병합 가능)", "5.07": "주주투표 결과", "8.01": "기타 이벤트"}
SCAN_FORMS = ("8-K", "6-K", "424B", "S-1", "F-1", "S-3", "F-3")

def norm(t: str) -> str:
    return t.upper().replace("-", ".")

class SecClient:
    def __init__(self, user_agent: str, rps: float = 8.0):
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT에 연락 가능한 이메일을 포함해야 합니다 (SEC 정책)")
        self.h = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self.gap, self._last, self._lock = 1.0 / rps, 0.0, threading.Lock()

    def _get(self, url: str, **kw) -> requests.Response:
        with self._lock:
            w = self.gap - (time.time() - self._last)
            if w > 0: time.sleep(w)
            self._last = time.time()
        r = requests.get(url, headers=self.h, timeout=40, **kw)
        r.raise_for_status()
        return r

    def ticker_map(self) -> dict[str, tuple[int, str]]:
        d = self._get("https://www.sec.gov/files/company_tickers.json").json()
        return {norm(v["ticker"]): (int(v["cik_str"]), v["title"]) for v in d.values()}

    def bulk_shares_outstanding(self, today: date | None = None) -> dict[int, tuple[float, str]]:
        """frames API로 전 상장사 커버페이지 주식 수를 한 번에 조회 → {cik: (shares, end_date)}. 시총 '추정'용."""
        today = today or date.today()
        out: dict[int, tuple[float, str]] = {}
        y, q = today.year, (today.month - 1) // 3 + 1
        for _ in range(3):                      # 최근 3개 분기(오래된 것부터 덮어써 최신값 우선)
            q -= 1
            if q == 0: y, q = y - 1, 4
        periods = []
        yy, qq = y, q
        for _ in range(3):
            periods.append(f"CY{yy}Q{qq}I"); qq += 1
            if qq == 5: yy, qq = yy + 1, 1
        for p in periods:
            try:
                data = self._get(f"https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/{p}.json").json()["data"]
            except Exception:
                continue
            for row in data:
                out[int(row["cik"])] = (float(row["val"]), row["end"])
        return out

    def recent_filings(self, cik: int, since: date) -> list[dict]:
        j = self._get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
        r = j["filings"]["recent"]
        out = []
        for i in range(len(r["form"])):
            fd = date.fromisoformat(r["filingDate"][i])
            if fd < since: continue
            acc = r["accessionNumber"][i]
            out.append({"form": r["form"][i], "filed": r["filingDate"][i], "accepted": r["acceptanceDateTime"][i],
                        "items": r.get("items", [""] * len(r["form"]))[i], "accession": acc,
                        "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{r['primaryDocument'][i]}"})
        return out

    def doc_text(self, url: str, max_bytes: int = 400_000) -> str:
        r = self._get(url, stream=True)
        raw = next(r.iter_content(max_bytes), b"")
        return re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "ignore"))

def classify_filings(filings: list[dict], now: datetime, cfg: dict, client: SecClient | None = None) -> dict:
    """공시 목록 → 뉴스(48h)·공급 위험·자본압박·이벤트 플래그. 수량은 추출하지 않는다(원문 확인 필요)."""
    nowd = now.date()
    h48 = [f for f in filings if (now.replace(tzinfo=None) - datetime.fromisoformat(f["accepted"].replace("Z", "")).replace(tzinfo=None)).total_seconds()
           <= cfg["news_lookback_hours"] * 3600]
    hs = timedelta(days=cfg["high_supply_days"])
    recent7 = [f for f in filings if nowd - date.fromisoformat(f["filed"]) <= hs]
    has_424b_7d = any(f["form"].startswith("424B") for f in recent7)
    has_effect_7d = any(f["form"] == "EFFECT" for f in recent7)
    reg_30d = [f for f in filings if f["form"] in REG_FORMS]
    item_flags = {}
    for f in filings:
        for it in (f["items"] or "").split(","):
            if it in ITEM_FLAGS:
                item_flags.setdefault(it, []).append(f["filed"])
    kw_hits: dict[str, list[str]] = {}
    if client:
        docs = [f for f in filings if f["form"].startswith(SCAN_FORMS)][: cfg["keyword_scan_max_docs"]]
        for f in docs:
            try:
                txt = client.doc_text(f["url"])
            except Exception:
                continue
            for k, pat in KEYWORDS.items():
                if re.search(pat, txt, re.I):
                    kw_hits.setdefault(k, []).append(f"{f['form']} {f['filed']}")
    supply_kw = {k for k in kw_hits if k in {"ATM", "ELOC", "PIPE", "RESALE", "CONVERT"}}
    if has_424b_7d or has_effect_7d or len(supply_kw) >= 2:
        level = "high"
    elif reg_30d or "3.02" in item_flags or supply_kw:
        level = "medium"
    else:
        level = "low"
    return {
        "recent_48h": [(f["form"], f["filed"], f["url"]) for f in h48],
        "has_424b_7d": has_424b_7d, "has_effect_7d": has_effect_7d,
        "registration_30d": [(f["form"], f["filed"]) for f in reg_30d],
        "item_flags": {f"{k} {ITEM_FLAGS[k]}": v for k, v in item_flags.items()},
        "keyword_hits": kw_hits, "supply_level": level,
        "foreign_filer": any(f["form"] in FOREIGN_FORMS for f in filings),
        "note": "키워드·양식 검출 결과이며 발행 수량·잔여 물량·매도 완료 여부는 원문 확인 필요(미확인)",
    }
