"""Polygon.io 클라이언트: 전 종목 일봉(grouped), 전 종목 snapshot, 종목별 분봉."""
from __future__ import annotations
import json, time
from datetime import date
from pathlib import Path
import requests

class PolygonClient:
    BASE = "https://api.polygon.io"

    def __init__(self, api_key: str, cache_dir: str = "data/cache", min_interval: float = 0.0):
        if not api_key:
            raise ValueError("POLYGON_API_KEY가 필요합니다 (.env 참고)")
        self.key, self.min_interval = api_key, min_interval
        self.cache = Path(cache_dir); self.cache.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def _get(self, url: str, params: dict | None = None) -> dict:
        params = dict(params or {}); params["apiKey"] = self.key
        for attempt in range(5):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0: time.sleep(wait)
            self._last = time.time()
            r = requests.get(url if url.startswith("http") else self.BASE + url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(2 ** attempt * 2); continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Polygon 429 재시도 초과")

    def grouped_daily(self, d: date) -> list[dict]:
        """해당일 전 종목 일봉. 과거일은 디스크에 캐시(빈 결과=휴장일도 캐시)."""
        f = self.cache / f"grouped_{d.isoformat()}.json"
        if f.exists():
            return json.loads(f.read_text())
        data = self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{d.isoformat()}", {"adjusted": "true"})
        res = data.get("results") or []
        if d < date.today():
            f.write_text(json.dumps(res))
        return res

    def snapshot_all(self) -> list[dict]:
        data = self._get("/v2/snapshot/locale/us/markets/stocks/tickers", {"include_otc": "false"})
        return data.get("tickers", [])

    def aggs(self, ticker: str, mult: int, span: str, start: date, end: date) -> list[dict]:
        url = f"/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start.isoformat()}/{end.isoformat()}"
        data = self._get(url, {"adjusted": "true", "sort": "asc", "limit": 50000})
        out = list(data.get("results") or [])
        while data.get("next_url"):
            data = self._get(data["next_url"])
            out += data.get("results") or []
        return out

    def news(self, ticker: str, since_iso: str, limit: int = 20) -> list[dict]:
        """종목별 최근 뉴스(제목·출처·시각·링크만 보관). since_iso: UTC ISO 시각."""
        data = self._get("/v2/reference/news", {"ticker": ticker, "published_utc.gte": since_iso,
                                                "order": "desc", "sort": "published_utc", "limit": limit})
        return [{"title": r.get("title", ""), "publisher": (r.get("publisher") or {}).get("name", ""),
                 "published": r.get("published_utc", ""), "url": r.get("article_url", "")}
                for r in data.get("results", [])]
