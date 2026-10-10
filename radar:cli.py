from __future__ import annotations
import argparse, os
from datetime import datetime
from pathlib import Path
from .config import load_config

def _env():
    f = Path(".env")
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

def main(argv=None):
    _env()
    ap = argparse.ArgumentParser(prog="radar", description="ZERO-BASE 미국 마이크로캡 급등 전 포착 레이더")
    ap.add_argument("--config", default="config/radar.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universe", help="적격 종목 명단·제외 사유 카운트만 출력")
    sc = sub.add_parser("scan", help="전체 스캔 실행")
    sc.add_argument("--out", default="reports"); sc.add_argument("--no-sec", action="store_true"); sc.add_argument("--no-news", action="store_true")
    sc.add_argument("--asof", help="ISO 시각(ET 가정). 기본: 지금")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    from .universe import fetch_halts, load_universe
    uni, ua = load_universe(cfg["universe"]["exchanges"])
    if a.cmd == "universe":
        print(ua); return
    from zoneinfo import ZoneInfo
    from .polygon import PolygonClient
    from .report import write_reports
    from .scanner import run_scan
    from .sec import SecClient
    poly = PolygonClient(os.environ.get("POLYGON_API_KEY", ""))
    sec = None if a.no_sec else SecClient(os.environ.get("SEC_USER_AGENT", ""))
    asof = datetime.fromisoformat(a.asof).replace(tzinfo=ZoneInfo("America/New_York")) if a.asof else None
    res = run_scan(cfg, poly, sec, uni, fetch_halts(), ua, asof=asof, use_sec=not a.no_sec, use_news=not a.no_news)
    jp, mp = write_reports(res, a.out)
    print(f"저장: {mp}\n      {jp}")

if __name__ == "__main__":
    main()
