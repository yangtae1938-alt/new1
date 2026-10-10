from __future__ import annotations
import json
from pathlib import Path
from .classify import CATS

def _n(x, f="{:,.0f}"): return "추가 확인 필요" if x is None else f.format(x)
def _p(x): return "추가 확인 필요" if x is None else f"{x:+.1%}"

def _news(n):
    if n is None: return "뉴스 미조회"
    if not n: return "48h 내 Polygon 뉴스 없음(IR 사이트 미조회)"
    return "<br>".join(f"[{x['title'][:90]}]({x['url']}) — {x['publisher']} {x['published'][:16]}" for x in n[:5])

def card(c: dict) -> str:
    v, e, s = c["volume"] or {}, c["engines"], c["sec"] or {}
    hit = ", ".join(k for k in "ABCD" if e[k].get("triggered")) or "없음"
    rows = [
        ("기본 정보", f"{c['ticker']} · {c['name']} · {c['exchange']} · {_n(c['market_cap'], '${:,.0f}')} ({c['cap_class']}) · 근거: {c['cap_basis']}"),
        ("가격", f"{_n(c['price'], '{:.4f}')} ({c['price_asof']}) · 당일 {_p(c['day_gain'])} · 장중 고점 {_p(c['intraday_high_gain'])}"),
        ("조기 신호", f"최초 {c['first_signal_time'] or '추가 확인 필요'} · 엔진 {hit} · 최초 신호 이후 {_p(c['first_signal_price_move'])}"),
        ("거래량", f"당일 {_n(c['day_volume'])} · 같은 시각 과거 중앙값 {_n(v.get('expected_median'))} → 시간대 보정 {_n(v.get('rvol_tod'), '{:.1f}x')} "
                  f"· 전일 전체 대비(참고) {_n(v.get('vs_prev_day_total'), '{:.2f}x')} · 전일 같은 시각 대비 {_n(v.get('vs_prev_day_same_time'), '{:.1f}x')} · 20일 {_n(c['avg20'])} · 65일 {_n(c['avg65'])}"),
        ("주식 구조", f"O/S {_n(c['shares_os'])} · Float {c['float']} · 회전율(O/S 기준) {_n(c['float_rotation_os_basis'], '{:.2%}')}"),
        ("공급 위험", f"수준 {s.get('supply_level', '미검증')} · 키워드 {s.get('keyword_hits', '공시에 명시되지 않음/미검증')} · 424B7d={s.get('has_424b_7d')} EFFECT7d={s.get('has_effect_7d')}"),
        ("희석", "실제 발행·잠재 발행·잔여 매도 가능 물량: 추가 확인 필요(원문 검증 필요)"),
        ("뉴스(48h)", _news(c.get("news"))),
        ("촉매", f"SEC 48h: {s.get('recent_48h') or '없음'} · D+7/D+30 이벤트: 추가 확인 필요 · 8-K 항목: {s.get('item_flags') or '없음'}"),
        ("자본 압박", "현금·런웨이·부채: 추가 확인 필요 (계속기업/상장유지 키워드는 반대 증거 참고)"),
        ("최종 분류", f"{CATS.get(c['category'])} — {' / '.join(x for x in c['rationale'] if x)} · 우선검토 점수 {c['score']}"),
        ("반대 증거", "; ".join(c["counter_evidence"]) or "자동 검출된 항목 없음"),
    ]
    return f"#### {c['ticker']}\n| 항목 | 내용 |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"

def write_reports(result: dict, outdir: str) -> tuple[Path, Path]:
    a, g = result["audit"], result["groups"]
    d = Path(outdir); d.mkdir(parents=True, exist_ok=True)
    stamp = a["asof_et"].replace(" ", "_").replace(":", "")
    jp, mp = d / f"scan_{stamp}.json", d / f"scan_{stamp}.md"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = [f"# ZERO-BASE 레이더 결과 — {a['asof_et']}", "", "> 잠정 포착이며 매수 신호·급등 예측이 아닙니다. 점수는 우선 검토 순서일 뿐 확률이 아닙니다.", "",
          "## 감사 로그", "```json", json.dumps(a, ensure_ascii=False, indent=1, default=str), "```", ""]
    for k in (1, 2, 3, 4, 5, 6):
        md.append(f"## {CATS[k]} ({len(g[k])})")
        md += [card(c) for c in g[k]] or ["해당 없음", ""]
    mp.write_text("\n".join(md), encoding="utf-8")
    return jp, mp
