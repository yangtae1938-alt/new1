"""Streamlit 웹 화면. Streamlit Cloud의 'Main file path'는 이 파일(app.py)."""
import os, sys, json, tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
from radar.classify import CATS                      # noqa: E402
from radar.config import load_config                 # noqa: E402
from radar.polygon import PolygonClient              # noqa: E402
from radar.report import card, write_reports         # noqa: E402
from radar.scanner import run_scan                   # noqa: E402
from radar.sec import SecClient                      # noqa: E402
from radar.universe import fetch_halts, load_universe  # noqa: E402

st.set_page_config(page_title="ZERO-BASE 레이더", layout="wide")

def secret(key: str, default: str = "") -> str:
    try:
        return str(st.secrets[key])
    except Exception:
        return os.environ.get(key, default)

# 선택: APP_PASSWORD를 Secrets에 넣으면 비밀번호를 아는 사람만 스캔 가능(내 API 키 보호)
pw = secret("APP_PASSWORD")
if pw and st.text_input("비밀번호", type="password") != pw:
    st.info("비밀번호를 입력하세요."); st.stop()

st.title("ZERO-BASE 미국 마이크로캡 급등 전 포착 레이더")
st.caption("잠정 포착이며 매수 신호·급등 예측이 아닙니다. 점수는 우선 검토 순서일 뿐 확률이 아닙니다. (투자 조언 아님)")

cfg = load_config(ROOT / "config" / "radar.yaml")
with st.sidebar:
    st.header("스캔 설정")
    when = st.radio("기준 시각", ["지금", "직접 지정(ET)"])
    asof = None
    if when != "지금":
        d = st.date_input("날짜"); t = st.time_input("시각(ET)")
        asof = datetime.combine(d, t).replace(tzinfo=ZoneInfo("America/New_York"))
    n2 = st.slider("분봉 정밀 검증 종목 수", 30, 500, cfg["stage1"]["max_stage2"], step=10)
    use_sec = st.checkbox("SEC 공시 검증", True)
    use_news = st.checkbox("뉴스(48h) 조회", True)

if st.button("스캔 실행", type="primary"):
    cfg["stage1"]["max_stage2"] = n2
    try:
        poly = PolygonClient(secret("POLYGON_API_KEY"), cache_dir=str(ROOT / "data" / "cache"))
        sec = SecClient(secret("SEC_USER_AGENT")) if use_sec else None
    except ValueError as e:
        st.error(f"설정 오류: {e}  (Secrets에 POLYGON_API_KEY, SEC_USER_AGENT 입력)"); st.stop()
    with st.status("스캔 중… (처음 실행은 수 분 걸릴 수 있어요)", expanded=True) as status:
        uni, ua = load_universe(cfg["universe"]["exchanges"])
        status.write(f"적격 종목 {ua['unique_eligible']:,}개")
        res = run_scan(cfg, poly, sec, uni, fetch_halts(), ua, asof=asof, use_sec=use_sec,
                       use_news=use_news, log=status.write)
        status.update(label="스캔 완료", state="complete")
    st.session_state["res"] = res

res = st.session_state.get("res")
if not res:
    st.info("왼쪽에서 설정을 확인하고 '스캔 실행'을 누르세요."); st.stop()

a, g = res["audit"], res["groups"]
cols = st.columns(6)
for col, k in zip(cols, CATS):
    col.metric(CATS[k], len(g[k]))
st.caption(f"{a['asof_et']} · {a['mode']} · {a['scan_type']}")

tabs = st.tabs([CATS[k] for k in CATS] + ["감사 로그"])
for tab, k in zip(tabs, CATS):
    with tab:
        rows = g[k]
        if not rows:
            st.write("해당 없음"); continue
        st.dataframe(pd.DataFrame([{
            "티커": c["ticker"], "회사": c["name"], "거래소": c["exchange"], "가격": c["price"],
            "당일": c["day_gain"], "시간대 보정 배수": (c["volume"] or {}).get("rvol_tod"),
            "최초 신호": c["first_signal_time"], "공급 위험": (c["sec"] or {}).get("supply_level", "미검증"),
            "뉴스(48h)": None if c.get("news") is None else len(c["news"]), "점수": c["score"]} for c in rows]),
            use_container_width=True, hide_index=True)
        for c in rows:
            with st.expander(f"{c['ticker']} · {c['name']} · 점수 {c['score']}"):
                st.markdown(card(c), unsafe_allow_html=True)
with tabs[-1]:
    st.json(a)

with tempfile.TemporaryDirectory() as td:
    jp, mp = write_reports(res, td)
    st.download_button("Markdown 보고서 받기", mp.read_text(encoding="utf-8"), file_name=mp.name)
    st.download_button("JSON 받기", jp.read_text(encoding="utf-8"), file_name=jp.name)
