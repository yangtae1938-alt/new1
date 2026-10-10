"""3차 최종 분류(①~⑥)와 우선 검토 점수.

점수는 '우선 검토 순서'용이며 급등 확률이 아님:
 score = 40*min(log10(multiple)/2, 1)       # 거래량 배수(1배=0, 100배=만점)
       + 25*(1 - min(max(day_gain,0)/0.30, 1))  # 가격이 덜 올랐을수록 가점
       + 15*[A 충족] + 10*[B 충족] + 10*[D 충족]
       - 20*[공급 high] - 8*[공급 medium] - 10*[분모 착시]
한계: 임계값은 휴리스틱이며 백테스트로 검증되지 않았다.
"""
from __future__ import annotations
import math

CATS = {1: "① 급등 전 PRE 후보", 2: "② 거래량 선행 잠정 후보", 3: "③ 재점화 관찰 후보",
        4: "④ 이미 급등한 종목", 5: "⑤ 공급 위험·제외 종목", 6: "⑥ 데이터 부족·검증 보류"}

def cap_class(cap: float | None, cfg: dict) -> str:
    if cap is None: return "시총 미확인"
    c = cfg["universe"]["cap_classes"]
    return ("나노캡" if cap < c["nano"] else "초소형 마이크로캡" if cap < c["ultra_micro"]
            else "마이크로캡" if cap < c["micro"] else "확장 관찰군")

def classify(c: dict, cfg: dict) -> tuple[int | None, list[str]]:
    k = cfg["classify"]; e = c["engines"]; why = []
    if c["history_days"] < k["min_history_days"] or c.get("price") is None:
        return 6, ["일봉 이력 또는 가격 데이터 부족"]
    hit = {n: bool(e[n].get("triggered")) for n in "ABCD"}
    if not any(hit.values()): return None, []
    if c["sec"] and c["sec"].get("supply_level") == "high":
        return 5, ["424B/EFFECT(7일 내) 또는 공급 관련 키워드 다수 검출 — 수량은 원문 확인 필요"]
    gain, since = c.get("day_gain"), c.get("first_signal_price_move")
    if (gain is not None and gain >= k["surge_day_gain"]) or (since is not None and since >= k["surge_since_signal"]):
        return 4, [f"전일 대비 {gain:+.0%}" if gain is not None else "", f"최초 신호 이후 {since:+.0%}" if since is not None else ""]
    if hit["D"]: return 3, ["1차 급등 후 거래량 수축·조정 뒤 재증가"]
    if hit["A"]: return 1, ["거래량 증가 + 가격 압축 + 저점 방어, 가격 미반영"]
    return 2, [("같은 시각 대비 거래량 선행 증가" if hit["B"] else "이상 거래량") + (" · 48h 뉴스 있음(원문 확인 필요)" if c.get("news") else " · 48h 확인된 뉴스 없음(IR 미조회)")]

def score(c: dict) -> float:
    e = c["engines"]; mult = max(e["C"].get("multiple", 0) or 0, 1.0)
    s = 40 * min(math.log10(mult) / 2, 1)
    s += 25 * (1 - min(max(c.get("day_gain") or 0, 0) / 0.30, 1))
    s += 15 * bool(e["A"].get("triggered")) + 10 * bool(e["B"].get("triggered")) + 10 * bool(e["D"].get("triggered"))
    lvl = (c["sec"] or {}).get("supply_level")
    s -= 20 if lvl == "high" else 8 if lvl == "medium" else 0
    s -= 10 if (c["volume"] or {}).get("denominator_small") else 0
    return round(s, 1)

def counter_evidence(c: dict) -> list[str]:
    out, sec = [], c["sec"] or {}
    out += c["engines"]["C"].get("warnings", [])
    if sec.get("supply_level") in ("high", "medium"): out.append(f"공급 위험 {sec['supply_level']} — 잔여 매도 가능 물량 미확인")
    if "GOING_CONCERN" in sec.get("keyword_hits", {}): out.append("계속기업 불확실성 키워드 검출")
    if any(k.startswith("3.01") for k in sec.get("item_flags", {})) or "LISTING" in sec.get("keyword_hits", {}): out.append("상장유지 관련 통지/키워드 검출")
    if c.get("price") and c["price"] < 1: out.append("주가 $1 미만 — 최소주가 요건·역병합 위험 확인 필요")
    if sec.get("foreign_filer"): out.append("외국계 발행사 — ADS 비율·기초주식 수 확인 필요")
    if not sec: out.append("SEC 공시 미검증")
    if c.get("news") is None: out.append("뉴스 미조회/조회 실패")
    elif c["news"]: out.append("뉴스 존재 — 제목만으로 촉매 실현 가능성을 판단하지 말 것(원문 확인 필요)")
    if c.get("shares_os") is None: out.append("발행주식 수 미확인 — 시총·Float rotation 불확실")
    return out
