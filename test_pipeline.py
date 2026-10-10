"""네트워크 없이 run_scan 전체를 가짜 Polygon/SEC 클라이언트로 검증."""
from datetime import date, datetime, timedelta
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo
from radar.config import load_config
from radar.scanner import run_scan
from radar.report import write_reports
from test_intraday import day_bars

TARGET = date(2026, 10, 7)
def tdays(n):
    d, out = TARGET - timedelta(days=1), []
    while len(out) < n:
        if d.weekday() < 5: out.append(d)
        d -= timedelta(days=1)
    return out[::-1]

class FakePoly:
    def grouped_daily(self, d):
        if d.weekday() >= 5 or d > TARGET: return []
        return [{"T": "AAAA", "o": 1, "h": 1.05, "l": 0.95, "c": 1.0, "v": 100000},
                {"T": "BBBB", "o": 2, "h": 2.1, "l": 1.9, "c": 2.0, "v": 50000}]
    def news(self, t, since_iso, limit=20):
        return [{"title": "Aaaa announces agreement", "publisher": "Wire", "published": "2026-10-07T13:00:00Z", "url": "https://example.com/a"}] if t == "AAAA" else []
    def snapshot_all(self):
        return [{"ticker": "AAAA", "day": {"v": 140000, "c": 1.0, "h": 1.0}, "min": {}},
                {"ticker": "BBBB", "day": {"v": 12000, "c": 2.0, "h": 2.0}, "min": {}}]
    def aggs(self, t, mult, span, start, end):
        rows = []
        for d in tdays(25): rows += day_bars(d, 1000)
        rows += [r for r in day_bars(TARGET, 1000, 600 if t == "AAAA" else None, 20 if t == "AAAA" else 1) if r["minute"] < 11 * 60]
        for r in rows:
            r["t"] = int(pd.Timestamp(r["date"]).tz_localize("America/New_York").value // 1e6) + r["minute"] * 60000
        return [{"t": r["t"], "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"], "v": r["volume"]} for r in rows]

def test_end_to_end(tmp_path):
    cfg = load_config("config/radar.yaml")
    cfg["intraday"]["workers"] = 1
    uni = pd.DataFrame({"ticker": ["AAAA", "BBBB"], "name": ["Aaaa Inc", "Bbbb Inc"], "exchange": ["NASDAQ", "NYSE"]})
    asof = datetime(2026, 10, 7, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    res = run_scan(cfg, FakePoly(), None, uni, set(), {"unique_eligible": 2}, asof=asof, use_sec=False, log=lambda *_: None)
    allc = [c for g in res["groups"].values() for c in g]
    a = [c for c in allc if c["ticker"] == "AAAA"][0]
    assert a["engines"]["B"]["triggered"] and a["engines"]["C"]["multiple"] > 3
    assert a["category"] == 2 and "BBBB" not in [c["ticker"] for g in res["groups"].values() for c in g]
    assert a["news"] and "뉴스 존재" in " ".join(a["counter_evidence"])
    jp, mp = write_reports(res, str(tmp_path))
    assert "Aaaa announces agreement" in mp.read_text(encoding="utf-8")
    assert mp.read_text(encoding="utf-8").startswith("# ZERO-BASE")
