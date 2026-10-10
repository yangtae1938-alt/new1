"""ZERO-BASE 마이크로캡 급등 전 포착 레이더 v1.0 (단일 파일)

- 유니버스: NASDAQ / NYSE / NYSE American 보통주 (nasdaqtrader 상장 목록)
- 일봉: Polygon grouped daily 로 전 종목 가격·거래량 수집
- 장중: 같은 시간대(분봉 누적)의 과거 거래량과 비교
- 공시: SEC EDGAR 최근 공시 연동

Streamlit Secrets 에 아래 값이 필요함:
    POLYGON_API_KEY = "..."
    SEC_USER_AGENT  = "이름 email@example.com"
"""
from __future__ import annotations

import io
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

ET = ZoneInfo("America/New_York")
POLY = "https://api.polygon.io"
NASDAQ_DIR = "https://www.nasdaqtrader.com/dynamic/SymDir/"
SEC_FORMS = {
    "8-K", "S-1", "S-3", "424B3", "424B4", "424B5",
    "SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G", "144", "6-K",
}
CATS = {
    "surge": "🚀 이미 급등",
    "setup": "🔥 급등 전 포착",
    "watch": "👀 관찰",
}


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets[name])
    except Exception:
        return os.environ.get(name, default)


KEY = secret("POLYGON_API_KEY")
SEC_UA = secret("SEC_USER_AGENT", "radar-app contact@example.com")


# ───────────────────────── Polygon ─────────────────────────
def pget(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["apiKey"] = KEY
    for _ in range(4):
        r = requests.get(POLY + path, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(13)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Polygon 호출 한도 초과")


@st.cache_data(ttl=3600, show_spinner=False)
def grouped(day: str, free: bool) -> pd.DataFrame:
    j = pget(f"/v2/aggs/grouped/locale/us/market/stocks/{day}", {"adjusted": "true"})
    rows = j.get("results") or []
    if free:
        time.sleep(12.5)  # 무료 플랜: 분당 5회
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)[["T", "o", "h", "l", "c", "v"]]


def load_history(n: int, free: bool, bar) -> pd.DataFrame:
    frames, got, tries = [], 0, 0
    d = datetime.now(ET).date()
    last_err = None
    while got < n and tries < n * 2 + 10:
        tries += 1
        if d.weekday() < 5:
            try:
                df = grouped(d.isoformat(), free)
            except requests.HTTPError as e:
                last_err = e
                df = pd.DataFrame()
            if not df.empty:
                df = df.copy()
                df["date"] = d
                frames.append(df)
                got += 1
                bar.progress(got / n, f"일봉 수집 {got}/{n} ({d})")
        d -= timedelta(days=1)
    if not frames:
        raise RuntimeError(f"일봉 데이터를 못 가져왔어요. API 키/플랜 확인: {last_err}")
    return pd.concat(frames, ignore_index=True)


def intraday_ratio(tkr: str, days_back: int, free: bool):
    """오늘(또는 최근 거래일) 누적 거래량 ÷ 과거 같은 시각까지 누적 거래량 중앙값."""
    now = datetime.now(ET)
    start = (now - timedelta(days=days_back * 2 + 4)).date()
    j = pget(
        f"/v2/aggs/ticker/{tkr}/range/1/minute/{start}/{now.date()}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    if free:
        time.sleep(12.5)
    rows = j.get("results") or []
    if not rows:
        return None
    df = pd.DataFrame(rows)
    ts = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(ET)
    df["date"] = ts.dt.date
    df["tod"] = ts.dt.hour * 60 + ts.dt.minute
    df = df[(df["tod"] >= 570) & (df["tod"] < 960)]  # 정규장 09:30~16:00
    if df.empty:
        return None
    latest = df["date"].max()
    cut = df.loc[df["date"] == latest, "tod"].max()
    cum = df[df["tod"] <= cut].groupby("date")["v"].sum()
    today = cum.get(latest, 0)
    past = cum.drop(latest).tail(days_back)
    if past.empty or past.median() == 0:
        return None
    return float(today / past.median())


# ───────────────────────── 유니버스 ─────────────────────────
def _fetch_txt(name: str) -> pd.DataFrame:
    t = requests.get(NASDAQ_DIR + name, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
    df = pd.read_csv(io.StringIO(t), sep="|", dtype=str)
    return df[~df.iloc[:, 0].str.startswith("File Creation", na=False)]


@st.cache_data(ttl=86400, show_spinner=False)
def load_universe() -> pd.DataFrame:
    a = _fetch_txt("nasdaqlisted.txt")
    a = a[(a["Test Issue"] == "N") & (a["ETF"] == "N")]
    a = pd.DataFrame({"symbol": a["Symbol"], "name": a["Security Name"], "exch": "NASDAQ"})

    b = _fetch_txt("otherlisted.txt")
    b = b[(b["Test Issue"] == "N") & (b["ETF"] == "N") & (b["Exchange"].isin(["N", "A"]))]
    b = pd.DataFrame({
        "symbol": b["ACT Symbol"],
        "name": b["Security Name"],
        "exch": b["Exchange"].map({"N": "NYSE", "A": "NYSE American"}),
    })

    u = pd.concat([a, b], ignore_index=True)
    u = u[u["symbol"].str.fullmatch(r"[A-Z]{1,5}", na=False)]  # 워런트·우선주 등 제외
    bad = r"Warrant|Right|Unit|Preferred|Depositary|Note|Debenture|Trust Preferred"
    u = u[~u["name"].str.contains(bad, case=False, na=False)]
    return u.drop_duplicates("symbol").set_index("symbol")


# ───────────────────────── 분류·스크린 ─────────────────────────
def classify(r) -> str | None:
    if r["chg"] >= 20:
        return CATS["surge"]
    if r["vol_x"] >= 3 and r["chg"] < 10 and r["near_high"] >= 0.5:
        return CATS["setup"]
    if r["vol_x"] >= 2:
        return CATS["watch"]
    return None


def screen(hist: pd.DataFrame, uni: pd.DataFrame, p: dict):
    h = hist.sort_values("date").copy()
    last = h["date"].max()
    prev = h[h["date"] < last]
    base = prev.groupby("T").agg(
        avg_v=("v", "mean"), days=("v", "size"), prev_c=("c", "last")
    )
    t = h[h["date"] == last].set_index("T").join(base, how="inner")
    t["chg"] = (t["c"] / t["prev_c"] - 1) * 100
    t["vol_x"] = t["v"] / t["avg_v"]
    span = (t["h"] - t["l"]).replace(0, float("nan"))
    t["near_high"] = (t["c"] - t["l"]) / span
    t = t[
        t["c"].between(p["pmin"], p["pmax"])
        & ((t["avg_v"] * t["c"]) >= p["min_usd"])
        & (t["days"] >= 5)
    ]
    t = t.join(uni, how="inner")
    if t.empty:
        return last, t
    t["분류"] = t.apply(classify, axis=1)
    t = t.dropna(subset=["분류"]).sort_values("vol_x", ascending=False)
    return last, t


# ───────────────────────── SEC ─────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def cik_map(ua: str) -> dict:
    j = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": ua}, timeout=30,
    ).json()
    return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in j.values()}


def sec_recent(tkr: str, days: int = 7) -> str:
    try:
        cik = cik_map(SEC_UA).get(tkr)
        if not cik:
            return ""
        j = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": SEC_UA}, timeout=30,
        ).json()
        f = j["filings"]["recent"]
        cutoff = (datetime.now(ET) - timedelta(days=days)).date().isoformat()
        out = [
            f"{d} {form}"
            for form, d in zip(f["form"], f["filingDate"])
            if d >= cutoff and form in SEC_FORMS
        ]
        time.sleep(0.15)
        return " / ".join(out[:4])
    except Exception:
        return ""


# ───────────────────────── UI ─────────────────────────
st.set_page_config(page_title="ZERO-BASE 레이더", layout="wide")
st.title("🎯 ZERO-BASE 급등 전 포착 레이더 v1.0")

if not KEY:
    st.error("Streamlit Secrets에 POLYGON_API_KEY를 넣어주세요.")
    st.stop()

with st.sidebar:
    st.header("설정")
    pmin = st.number_input("최소 가격($)", 0.1, 100.0, 0.5, 0.1)
    pmax = st.number_input("최대 가격($)", 1.0, 500.0, 20.0, 1.0)
    min_usd = st.number_input("평균 거래대금 하한($)", 0, 10_000_000, 200_000, 50_000)
    lookback = st.slider("일봉 비교 기간(거래일)", 5, 30, 15)
    deep = st.slider("정밀 확인할 상위 종목 수", 0, 30, 10)
    free = st.checkbox("Polygon 무료 플랜(분당 5회 대기)", value=True)

if st.button("스캔 시작", type="primary", use_container_width=True):
    p = {"pmin": pmin, "pmax": pmax, "min_usd": min_usd}

    with st.spinner("종목 목록 불러오는 중..."):
        uni = load_universe()

    bar = st.progress(0.0, "일봉 수집 준비 중...")
    try:
        hist = load_history(lookback, free, bar)
    except Exception as e:
        bar.empty()
        st.error(str(e))
        st.stop()
    bar.empty()

    last, res = screen(hist, uni, p)
    st.caption(f"기준일 {last} · 후보 {len(res)}개 · 유니버스 {len(uni)}개")
    if res.empty:
        st.info("조건에 맞는 종목이 없어요.")
        st.stop()

    res = res.copy()
    res["시간대배수"] = None
    res["공시"] = ""

    top = res.head(deep).index.tolist()
    if top:
        prog = st.progress(0.0, "시간대별 거래량·SEC 공시 확인 중...")
        for i, tkr in enumerate(top, 1):
            try:
                x = intraday_ratio(tkr, 10, free)
                res.loc[tkr, "시간대배수"] = round(x, 1) if x else None
            except Exception:
                pass
            res.loc[tkr, "공시"] = sec_recent(tkr)
            prog.progress(i / len(top), f"{tkr} 확인 ({i}/{len(top)})")
        prog.empty()

    view = res.reset_index().rename(columns={
        "index": "종목", "T": "종목", "name": "이름", "exch": "거래소",
        "c": "종가", "chg": "등락%", "vol_x": "거래량배수",
    })
    view["등락%"] = view["등락%"].round(1)
    view["거래량배수"] = view["거래량배수"].round(1)
    st.dataframe(
        view[["종목", "분류", "종가", "등락%", "거래량배수", "시간대배수", "공시", "이름", "거래소"]],
        use_container_width=True, hide_index=True,
    )
    st.caption("참고용 스캐너이며 투자 조언이 아닙니다. 거래량배수 = 기준일 거래량 ÷ 이전 평균, 시간대배수 = 같은 시각까지 누적 거래량 ÷ 과거 중앙값.")
else:
    st.write("왼쪽(≫) 설정을 확인하고 **스캔 시작**을 눌러주세요.")
