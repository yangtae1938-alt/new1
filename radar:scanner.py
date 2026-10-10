"""전체 스캔 오케스트레이션: 1단계(전 종목 일봉+snapshot) → 2단계(후보 분봉, 같은 시각 비교) → 3단계(SEC 검증)."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import numpy as np
import pandas as pd
from .classify import CATS, cap_class, classify, counter_evidence, score
from .config import hhmm
from .engines import monster, pre_breakout, reignition, volume_first
from .intraday import ET, bars_frame, compute_intraday_volume
from .sec import classify_filings, norm

def trading_history(poly, end: date, n: int = 90) -> pd.DataFrame:
    rows, d, got, tried = [], end, 0, 0
    while got < n and tried < 170:
        if d.weekday() < 5:
            res = poly.grouped_daily(d)
            if res:
                rows += [(r["T"], d, r["o"], r["h"], r["l"], r["c"], r["v"]) for r in res]; got += 1
        d -= timedelta(days=1); tried += 1
    return pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "volume"])

def resolve_session(poly, asof: datetime, icfg: dict) -> tuple[date, bool, int]:
    now = asof.astimezone(ET); mins = now.hour * 60 + now.minute
    wk = now.weekday() < 5
    if wk and hhmm(icfg["session_start"]) <= mins < hhmm(icfg["session_end"]):
        return now.date(), True, mins
    d = now.date() if (wk and mins >= hhmm(icfg["session_end"])) else now.date() - timedelta(days=1)
    for _ in range(10):
        if d.weekday() < 5 and poly.grouped_daily(d): break
        d -= timedelta(days=1)
    return d, False, hhmm(icfg["session_end"])

def today_table(poly, target: date, live: bool, hist: pd.DataFrame) -> pd.DataFrame:
    if live:
        rows = []
        for s in poly.snapshot_all():
            day, mn = s.get("day") or {}, s.get("min") or {}
            vol = max(day.get("v") or 0, mn.get("av") or 0)
            px = (s.get("lastTrade") or {}).get("p") or day.get("c") or mn.get("c")
            rows.append((s["ticker"], px, vol, day.get("h")))
        return pd.DataFrame(rows, columns=["ticker", "price", "day_vol", "day_high"])
    t = hist[hist["date"] == target]
    return pd.DataFrame({"ticker": t["ticker"], "price": t["close"], "day_vol": t["volume"], "day_high": t["high"]})

def run_scan(cfg, poly, sec, uni: pd.DataFrame, halts: set, uni_audit: dict, asof: datetime | None = None,
             use_sec: bool = True, use_news: bool = True, log=print) -> dict:
    asof = asof or datetime.now(ET)
    icfg, ecfg, scfg = cfg["intraday"], cfg["engines"], cfg["sec"]
    target, live, asof_min = resolve_session(poly, asof, icfg)
    mode = "LIVE(장중)" if live else "EOD(장 마감 후/휴장)"
    log(f"[1/3] 기준일 {target} · {mode}")
    uni = uni[~uni["ticker"].isin(halts)]
    names = uni.set_index("ticker")[["name", "exchange"]].to_dict("index")
    hist = trading_history(poly, target - timedelta(days=1) if live else target)
    hist = hist[hist["ticker"].isin(names)].sort_values(["ticker", "date"])
    prior = hist[hist["date"] < target]
    avg20 = prior.groupby("ticker").tail(20).groupby("ticker")["volume"].mean()
    avg65 = prior.groupby("ticker").tail(65).groupby("ticker")["volume"].mean()
    prev_close = prior.groupby("ticker")["close"].last()
    nhist = hist.groupby("ticker").size()
    today = today_table(poly, target, live, hist)
    today = today[today["ticker"].isin(names)].set_index("ticker")
    cikmap, shares = {}, {}
    if use_sec:
        cikmap = sec.ticker_map(); bulk = sec.bulk_shares_outstanding()
        for t in today.index:
            m = cikmap.get(norm(t))
            if m and m[0] in bulk: shares[t] = bulk[m[0]]
    t1 = today.join(avg20.rename("avg20")).join(avg65.rename("avg65")).join(prev_close.rename("prev_close"))
    t1["shares_os"] = pd.Series({k: v[0] for k, v in shares.items()})
    t1["mcap"] = t1["price"] * t1["shares_os"]
    t1["ratio1"] = t1["day_vol"] / np.maximum(t1["avg20"].fillna(0), 1)
    u = cfg["universe"]
    ok = (t1["price"] >= u["min_price"]) & (t1["avg20"].notna()) & (t1["mcap"].isna() | (t1["mcap"] <= u["stage1_max_cap"]))
    t1 = t1[ok]
    by = {t: g.reset_index(drop=True) for t, g in hist.groupby("ticker")}
    a_hits = [t for t in t1.index if t in by and pre_breakout(by[t], ecfg["pre_breakout"]).get("triggered")]
    vol_cands = t1[t1["day_vol"] >= cfg["stage1"]["min_day_volume"]].nlargest(cfg["stage1"]["max_stage2"], "ratio1").index.tolist()
    cands = list(dict.fromkeys(vol_cands + a_hits))
    cands = sorted(cands, key=lambda t: -t1.loc[t, "ratio1"])[: cfg["stage1"]["max_stage2"]]
    log(f"[2/3] 1단계 통과 {len(t1)} → 분봉 조회 {len(cands)}종목 (A엔진 일봉 포착 {len(a_hits)})")

    start = target - timedelta(days=int(icfg["baseline_days"] * 1.6) + 10)
    def fetch(t):
        try:
            return t, compute_intraday_volume(bars_frame(poly.aggs(t, 5, "minute", start, target)), target, asof_min, icfg)
        except Exception as ex:
            return t, str(ex)
    with ThreadPoolExecutor(icfg["workers"]) as ex:
        ivs = dict(ex.map(fetch, cands))

    now = datetime.now()
    out, errors = [], []
    for t in cands:
        iv = ivs[t]
        if isinstance(iv, str): errors.append((t, iv)); iv = None
        ivd = iv.to_dict() if iv else None
        r = t1.loc[t]; df = by.get(t)
        px = (ivd or {}).get("last_price") or r["price"]
        pc = r["prev_close"]
        rv_now = (ivd or {}).get("rvol_tod")
        if rv_now is None and r["avg20"] > 0: rv_now = r["day_vol"] / r["avg20"]
        eng = {"A": pre_breakout(df, ecfg["pre_breakout"]), "B": volume_first(ivd, pc, ecfg["volume_first"]),
               "C": monster(ivd, float(r["avg20"]), float(r["day_vol"]), ecfg["monster"]),
               "D": reignition(df, rv_now, ecfg["reignition"])}
        cap = None if pd.isna(r["mcap"]) else float(r["mcap"])
        cls = cap_class(cap, cfg)
        if cls == "확장 관찰군" and eng["C"]["tier"] < u["extended_min_tier"]: continue
        hi = (ivd or {}).get("day_high") or r["day_high"]
        out.append({"ticker": t, **names[t], "price": float(px) if px else None, "price_asof": (ivd or {}).get("asof") or "일봉 종가",
                    "market_cap": cap, "cap_class": cls,
                    "cap_basis": f"가격 {px} × SEC 커버페이지 주식 수({shares[t][1]} 기준)" if t in shares else "발행주식 수 미확인",
                    "shares_os": float(r["shares_os"]) if pd.notna(r["shares_os"]) else None, "float": "추가 확인 필요(Float는 별도 데이터 필요)",
                    "float_rotation_os_basis": float(r["day_vol"] / r["shares_os"]) if pd.notna(r["shares_os"]) else None,
                    "day_gain": float(px / pc - 1) if px and pc else None,
                    "intraday_high_gain": float(hi / pc - 1) if hi and pc else None,
                    "first_signal_time": (ivd or {}).get("first_signal_time"),
                    "first_signal_price_move": (ivd or {}).get("first_signal_price_move"),
                    "day_volume": float(r["day_vol"]), "avg20": float(r["avg20"]), "avg65": float(r["avg65"]),
                    "volume": ivd, "engines": eng, "history_days": int(nhist.get(t, 0)), "sec": None})
    triggered = [c for c in out if any(c["engines"][k].get("triggered") for k in "ABCD")]
    log(f"[3/3] 엔진 포착 {len(triggered)}종목 → SEC 검증" if use_sec else "[3/3] SEC 검증 생략(--no-sec)")
    if use_sec:
        since = (date.today() - timedelta(days=scfg["supply_lookback_days"]))
        def sec_job(c):
            m = cikmap.get(norm(c["ticker"]))
            if not m: return c["ticker"], None
            try:
                f = sec.recent_filings(m[0], since)
                return c["ticker"], classify_filings(f, now, scfg, sec)
            except Exception as ex:
                return c["ticker"], {"error": str(ex), "supply_level": "unknown"}
        with ThreadPoolExecutor(4) as ex:
            res = dict(ex.map(sec_job, triggered))
        for c in triggered: c["sec"] = res.get(c["ticker"])
    for c in out: c["news"] = None
    if use_news:
        since_iso = (datetime.now(timezone.utc) - timedelta(hours=scfg["news_lookback_hours"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        def news_job(c):
            try: return c["ticker"], poly.news(c["ticker"], since_iso)
            except Exception: return c["ticker"], None
        with ThreadPoolExecutor(icfg["workers"]) as ex:
            nres = dict(ex.map(news_job, triggered))
        for c in triggered: c["news"] = nres.get(c["ticker"])
    for c in out:
        c["category"], c["rationale"] = classify(c, cfg)
        c["score"] = score(c); c["counter_evidence"] = counter_evidence(c)
    groups = {k: sorted([c for c in out if c["category"] == k], key=lambda c: -c["score"]) for k in CATS}
    audit = {
        "exchanges": cfg["universe"]["exchanges"], "asof_et": asof.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
        "target_date": str(target), "mode": mode, "universe": uni_audit, "halted_excluded": len(halts),
        "market_data_tickers": int(len(today)), "stage1_passed": int(len(t1)),
        "stage2_intraday_checked": len(cands), "intraday_errors": errors[:20],
        "sec_verified": sum(1 for c in triggered if c["sec"] and "error" not in c["sec"]),
        "sec_unverified": sum(1 for c in triggered if not c["sec"] or "error" in c["sec"]),
        "engine_hits": {k: sum(1 for c in out if c["engines"][k].get("triggered")) for k in "ABCD"},
        "category_counts": {CATS[k]: len(v) for k, v in groups.items()},
        "supply_excluded": [(c["ticker"], c["rationale"]) for c in groups[5]],
        "scan_type": "표본 스캔(1단계 전 종목 → 상위 후보만 2·3단계)" ,
        "sources": {"일봉/snapshot/분봉": "Polygon.io", "공시/주식 수": "SEC EDGAR", "뉴스(48h)": "Polygon 종목 뉴스", "종목 명단": "Nasdaq Trader", "거래정지": "Nasdaq Trader RSS(best-effort)"},
        "caveats": ["Float는 SEC·Polygon 무료 데이터로 확인 불가 → 추가 확인 필요", "공시 분류는 양식·키워드 검출이며 수량 검증 아님",
                    "시총은 SEC 커버페이지 주식 수 기반 추정", "기업 IR 사이트는 미조회. 무호재 = SEC·Polygon 뉴스 48h 내 확인된 호재 없음을 뜻하며 뉴스가 없다는 주장이 아님"],
    }
    return {"audit": audit, "groups": groups}
