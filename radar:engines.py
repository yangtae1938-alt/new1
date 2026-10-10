"""독립 탐지 엔진 A~D (일봉/장중 거래량 기반). E·F·G·H는 sec.py의 공시 분류가 담당."""
from __future__ import annotations
import pandas as pd

def pre_breakout(df: pd.DataFrame, c: dict) -> dict:
    """A. 완료된 일봉 기준(D-1). 거래량 증가 + 가격 압축 + 저점 방어 + 아직 가격 미반영."""
    w = c["window_days"]
    if len(df) < w + 30: return {"triggered": False, "note": "일봉 이력 부족"}
    recent, prior = df.iloc[-w:], df.iloc[:-w].tail(c["baseline_days"])
    base = prior["volume"].mean()
    if not base or base <= 0: return {"triggered": False, "note": "기준 거래량 0"}
    ratio = recent["volume"].mean() / base
    rng = (df["high"] - df["low"]) / df["close"]
    comp = rng.iloc[-5:].mean() / max(rng.iloc[-25:-5].mean(), 1e-9)
    low_def = bool(recent["low"].iloc[-5:].min() >= recent["low"].iloc[:-5].min() * 0.97)
    gain = df["close"].iloc[-1] / df["close"].iloc[-w - 1] - 1
    trig = ratio >= c["early_ratio"] and comp <= c["compression_max"] and low_def and gain <= c["max_gain"]
    return {"triggered": bool(trig), "tier": "strict" if ratio >= c["strict_ratio"] else "early",
            "volume_ratio_13d": float(ratio), "compression": float(comp), "low_defended": low_def,
            "gain_13d": float(gain), "basis": "완료된 일봉(D-1 또는 EOD)"}

def volume_first(iv: dict | None, prev_close: float | None, c: dict) -> dict:
    """B. 같은 시각 대비 거래량이 먼저 폭증했고 가격은 아직 덜 오른 경우."""
    if not iv or iv.get("rvol_tod") is None: return {"triggered": False, "note": "시간대 보정 거래량 없음"}
    move = float(iv["last_price"] / prev_close - 1) if prev_close and iv.get("last_price") else None
    trig = iv["rvol_tod"] >= c["min_rvol"] and move is not None and move <= c["max_price_move"]
    return {"triggered": bool(trig), "rvol_tod": iv["rvol_tod"], "move_vs_prev_close": move,
            "volume_before_price": iv.get("first_signal_price_move") is not None and (iv["first_signal_price_move"] or 0) <= c["max_price_move"]}

def monster(iv: dict | None, avg20: float, today_vol: float, c: dict) -> dict:
    """C. 10/30/50/100배 티어. 장중엔 같은 시각 배수, 아니면 일 평균 대비 배수."""
    use_tod = bool(iv and iv.get("rvol_tod") is not None)
    val = iv["rvol_tod"] if use_tod else (today_vol / avg20 if avg20 > 0 else 0.0)
    tier = max([t for t in c["tiers"] if val >= t], default=0)
    warn = []
    if avg20 < c["min_avg_volume"]: warn.append(f"20일 평균 {avg20:,.0f}주로 극소 — 분모 착시 가능")
    if iv and iv.get("denominator_small"): warn.append("같은 시각 기대 거래량 하한 적용됨")
    return {"triggered": tier > 0, "tier": tier, "multiple": float(val),
            "basis": "same-time-of-day" if use_tod else "daily-average", "warnings": warn}

def reignition(df: pd.DataFrame, rvol_now: float | None, c: dict) -> dict:
    """D. 1차 급등 → 거래량 수축·조정 → 거래량 재증가."""
    d = df.reset_index(drop=True)
    if len(d) < 30: return {"triggered": False, "note": "일봉 이력 부족"}
    ma = d["volume"].rolling(20).mean().shift(1)
    chg = d["close"] / d["close"].shift(1) - 1
    spike = (d["volume"] >= c["spike_vol_mult"] * ma) & (chg >= c["spike_gain"])
    idx = [i for i in d.index[-c["lookback_days"]:-3] if spike.get(i, False)]
    if not idx: return {"triggered": False, "note": "최근 1차 급등 없음"}
    i = max(idx, key=lambda j: d.loc[j, "volume"])
    after = d.iloc[i + 1:]
    dd = d["close"].iloc[-1] / d.loc[i:, "high"].max() - 1
    contr = after["volume"].iloc[-5:].mean() / d.loc[i, "volume"]
    ok = dd <= -c["min_drawdown"] and contr <= c["contraction_max"] and rvol_now is not None and rvol_now >= c["reignite_rvol"]
    return {"triggered": bool(ok), "first_spike_date": str(d.loc[i, "date"]), "drawdown": float(dd),
            "volume_contraction": float(contr), "reignite_rvol": rvol_now}
