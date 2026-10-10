"""장중 시간대 보정 거래량(Time-of-Day RVOL).

핵심: 당일 '누적' 거래량을 전일 '전체' 거래량과 비교하지 않고,
과거 N거래일의 '같은 시각까지의 누적 거래량'(중앙값)과 비교한다.
 - 5분 버킷 단위 누적 곡선을 날짜별로 만들고 같은 버킷 끝 시각끼리 비교
 - 분봉이 없는 구간은 0으로 채움(저유동 종목은 거래 없는 구간이 많음)
 - 조기 폐장일은 기준선에서 제외
 - 기대 거래량이 너무 작으면 분모 하한 적용 + denominator_small 경고
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import date
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")

def early_close(d: date) -> bool:
    """조기 폐장(13:00) 근사 규칙. 정확한 달력이 필요하면 config로 제외일을 추가하세요."""
    if d.weekday() >= 5: return False
    return (d.month == 7 and d.day == 3) or (d.month == 12 and d.day == 24) or \
           (d.month == 11 and d.weekday() == 4 and 23 <= d.day <= 29)

def bars_frame(raw: list[dict]) -> pd.DataFrame:
    cols = ["date", "minute", "open", "high", "low", "close", "volume"]
    if not raw: return pd.DataFrame(columns=cols)
    df = pd.DataFrame(raw).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    ts = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(ET)
    df["date"] = ts.dt.date
    df["minute"] = ts.dt.hour * 60 + ts.dt.minute
    return df[cols]

def _cum(day: pd.DataFrame, grid: np.ndarray, start: int, bucket: int) -> np.ndarray:
    d = day[(day["minute"] >= start) & (day["minute"] < grid[-1])]
    be = ((d["minute"] - start) // bucket + 1) * bucket + start
    vol = d.groupby(be)["volume"].sum().reindex(grid, fill_value=0)
    return vol.cumsum().to_numpy(float)

def fmt_min(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d} ET"

@dataclass
class IntradayVolume:
    asof: str = ""
    today_cum: float = 0.0
    expected_median: float | None = None       # 과거 같은 시각까지 누적 거래량(중앙값)
    rvol_tod: float | None = None              # 당일 누적 / 같은 시각 과거 누적(중앙값)
    rvol_tod_mean: float | None = None
    baseline_days: int = 0
    prev_day_total: float | None = None
    vs_prev_day_total: float | None = None     # 참고용: 단순 비교(장 초반엔 구조적으로 작게 나옴)
    vs_prev_day_same_time: float | None = None # 전일 같은 시각 누적 대비
    typical_fraction_done: float | None = None # 보통 이 시각까지 하루 거래량의 몇 %가 체결되는지
    projected_full_day: float | None = None
    denominator_small: bool = False
    first_signal_time: str | None = None
    first_signal_price_move: float | None = None
    last_price: float | None = None
    day_high: float | None = None
    notes: list[str] = field(default_factory=list)
    def to_dict(self): return asdict(self)

def compute_intraday_volume(bars: pd.DataFrame, target: date, asof_min: int, cfg: dict) -> IntradayVolume:
    from .config import hhmm
    b, start, end = cfg["bucket_minutes"], hhmm(cfg["session_start"]), hhmm(cfg["session_end"])
    out = IntradayVolume()
    last_end = min(((asof_min - start) // b) * b + start, end)
    if last_end < start + b:
        out.notes.append("세션 시작 직후라 완료된 버킷 없음"); return out
    grid = np.arange(start + b, last_end + 1, b)
    full = np.arange(start + b, end + 1, b)
    out.asof = fmt_min(int(last_end))
    by_day = {d: g for d, g in bars.groupby("date")}
    tdf = by_day.get(target)
    if tdf is None or tdf.empty:
        out.notes.append("당일 분봉 없음"); return out
    today = _cum(tdf, grid, start, b)
    out.today_cum = float(today[-1])
    out.last_price = float(tdf.sort_values("minute")["close"].iloc[-1])
    out.day_high = float(tdf["high"].max())
    prior_days = sorted(d for d in by_day if d < target)
    past = [d for d in prior_days if not early_close(d)][-cfg["baseline_days"]:]
    out.baseline_days = len(past)
    if prior_days:
        pd_ = prior_days[-1]
        pf = _cum(by_day[pd_], full, start, b)
        out.prev_day_total = float(pf[-1])
        out.vs_prev_day_total = float(out.today_cum / pf[-1]) if pf[-1] > 0 else None
        out.vs_prev_day_same_time = float(out.today_cum / pf[len(grid) - 1]) if pf[len(grid) - 1] > 0 else None
    if len(past) < cfg["min_baseline_days"]:
        out.notes.append(f"기준선 거래일 부족({len(past)}일) — 같은 시각 비교 불가"); return out
    mat = np.vstack([_cum(by_day[d], full, start, b) for d in past])      # 일 x 버킷
    k = len(grid)
    exp_med = np.median(mat[:, :k], axis=0)
    exp_mean = mat[:, :k].mean(axis=0)
    floor = cfg["min_expected_shares"]
    rvol = today / np.maximum(exp_med, floor)
    out.expected_median = float(exp_med[-1])
    out.rvol_tod = float(rvol[-1])
    out.rvol_tod_mean = float(today[-1] / max(exp_mean[-1], floor))
    out.denominator_small = bool(exp_med[-1] < floor)
    if out.denominator_small:
        out.notes.append(f"같은 시각 기대 거래량 {exp_med[-1]:.0f}주 < 하한 {floor}주 — 배수 착시 가능")
    tot = mat[:, -1]; ok = tot > 0
    if ok.any():
        frac = float(np.median(mat[ok, k - 1] / tot[ok]))
        out.typical_fraction_done = frac
        if frac >= 0.05:
            out.projected_full_day = float(today[-1] / frac)
    hit = np.where((rvol >= cfg["signal_rvol"]) & (today >= cfg["signal_min_shares"]))[0]
    if len(hit):
        i = int(hit[0]); bstart = int(grid[i] - b)
        out.first_signal_time = fmt_min(int(grid[i]))
        pre = tdf[tdf["minute"] >= bstart].sort_values("minute")
        ref = float(pre["open"].iloc[0]) if len(pre) else None
        if ref and ref > 0 and out.last_price:
            out.first_signal_price_move = out.last_price / ref - 1
    return out
