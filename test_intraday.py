from datetime import date, timedelta
import numpy as np, pandas as pd
from radar.config import load_config
from radar.intraday import compute_intraday_volume, early_close

CFG = load_config("config/radar.yaml")["intraday"]

def day_bars(d, base, spike_from=None, spike_mult=1.0, price0=1.0):
    rows = []
    for m in range(4 * 60, 20 * 60, 5):
        w = 3.0 if m < 570 else (6.0 if 570 <= m < 600 else 1.0 if m < 930 else 2.0)  # 장 초반 집중 U자형
        v = base * w * (spike_mult if spike_from is not None and m >= spike_from else 1.0)
        rows.append(dict(date=d, minute=m, open=price0, high=price0, low=price0, close=price0, volume=v if 570 <= m < 960 else v * 0.05))
    return rows

def build(target, spike_from=None, mult=1.0, n=25):
    rows, d, k = [], target - timedelta(days=1), 0
    while k < n:
        if d.weekday() < 5:
            rows += day_bars(d, 1000); k += 1
        d -= timedelta(days=1)
    rows += [r for r in day_bars(target, 1000, spike_from, mult) if r["minute"] < 11 * 60]
    return pd.DataFrame(rows)

TARGET = date(2026, 10, 7)

def test_same_time_vs_prev_day_total():
    bars = build(TARGET, spike_from=600, mult=20)          # 10:00부터 20배
    iv = compute_intraday_volume(bars, TARGET, 11 * 60, CFG)
    assert iv.rvol_tod > 3 and iv.first_signal_time is not None
    assert iv.vs_prev_day_total < iv.rvol_tod             # 전일 전체 대비 단순 비교는 구조적으로 과소평가
    assert iv.baseline_days == 20

def test_normal_day_is_about_one():
    iv = compute_intraday_volume(build(TARGET), TARGET, 11 * 60, CFG)
    assert 0.9 < iv.rvol_tod < 1.1 and iv.first_signal_time is None

def test_early_close_excluded_and_floor():
    assert early_close(date(2025, 11, 28)) and early_close(date(2025, 12, 24)) and not early_close(date(2026, 10, 7))
    bars = build(TARGET, spike_from=600, mult=2)
    bars["volume"] = bars["volume"] * 0.0001                # 극소 거래량 → 분모 착시 경고
    iv = compute_intraday_volume(bars, TARGET, 11 * 60, CFG)
    assert iv.denominator_small
