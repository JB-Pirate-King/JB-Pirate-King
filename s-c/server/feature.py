"""
AIS 피처 추출 모듈

ml/preprocess.py 및 ml/eval_anomaly.py 와 동일한 12개 피처를 계산.
모델 학습 데이터와의 일관성을 위해 같은 공식을 사용.

피처 순서 (인덱스 고정):
  0  sog               Speed Over Ground (노트)
  1  cog               Course Over Ground (도)
  2  heading           True Heading (도, 511 → 0)
  3  status            Nav Status
  4  dt                이전 신호와의 시간 간격 (초)
  5  dist_km           이전 위치와의 거리 (km, Haversine)
  6  cog_hdg_diff      COG ↔ Heading 차이 (도, heading=511 → 0)
  7  sog_change        SOG 변화량
  8  cog_hdg_change    cog_hdg_diff 변화량
  9  speed_consistency 실제 이동거리 / SOG 기반 예상 거리
  10 lat_speed         위도 변화율 (도/초)
  11 lon_speed         경도 변화율 (도/초)
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

FEATURES: List[str] = [
    "sog", "cog", "heading", "status",
    "dt", "dist_km",
    "cog_hdg_diff", "sog_change",
    "cog_hdg_change",
    "speed_consistency",
    "lat_speed", "lon_speed",
]

N_FEAT  = len(FEATURES)   # 12
SEQ_LEN = 10


# ── 보조 함수 ────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _cog_hdg_diff(cog: float, hdg: int) -> float:
    """COG 와 Heading 의 각도 차이 (0~180). hdg=511 이면 0 반환."""
    if hdg >= 511:
        return 0.0
    d = abs(cog - float(hdg))
    return 360.0 - d if d > 180.0 else d


# ── 핵심 피처 추출 ───────────────────────────────────────────────────

def extract_features(records: List[Dict]) -> List[List[float]]:
    """
    AISTarget 딕셔너리 리스트 → 12-열 피처 행렬.

    records 각 원소는 다음 키를 가짐:
        lat, lon, sog, cog, heading, nav_status, timestamp

    반환: [[feat0..feat11], ...], 길이 = len(records)
    첫 번째 행은 이전 레코드가 없으므로 dt=0, dist_km=0 등 0-초기화.
    """
    result: List[List[float]] = []
    prev_sog = 0.0
    prev_chd = 0.0

    for i, rec in enumerate(records):
        sog    = float(rec.get("sog", 0.0))
        cog    = float(rec.get("cog", 0.0))
        hdg    = int(rec.get("heading", 511))
        status = int(rec.get("nav_status", 15))
        lat    = float(rec.get("lat", 0.0))
        lon    = float(rec.get("lon", 0.0))
        ts     = float(rec.get("timestamp", 0.0))

        if i == 0:
            dt          = 0.0
            dist        = 0.0
            chd         = _cog_hdg_diff(cog, hdg)
            sog_ch      = 0.0
            chd_change  = 0.0
            speed_cons  = 1.0
            lat_spd     = 0.0
            lon_spd     = 0.0
        else:
            prev = records[i - 1]
            prev_ts  = float(prev.get("timestamp", ts))
            prev_lat = float(prev.get("lat", lat))
            prev_lon = float(prev.get("lon", lon))

            dt    = max(0.0, ts - prev_ts)
            dist  = _haversine_km(prev_lat, prev_lon, lat, lon)
            chd   = _cog_hdg_diff(cog, hdg)

            sog_ch = abs(sog - prev_sog)

            prev_chd_val = _cog_hdg_diff(float(prev.get("cog", 0.0)),
                                         int(prev.get("heading", 511)))
            chd_change = abs(chd - prev_chd_val)

            # speed_consistency: 실제 이동 / SOG 예측 이동
            if sog >= 0.1 and dt > 0:
                expected   = sog * dt / 3600.0 * 1.852   # km
                speed_cons = round(dist / (expected + 1e-6), 4)
            else:
                speed_cons = 1.0

            lat_spd = round((lat - prev_lat) / (dt + 1e-6), 6)
            lon_spd = round((lon - prev_lon) / (dt + 1e-6), 6)

        prev_sog = sog
        prev_chd = chd

        # heading 511 → 0 (not available)
        hdg_feat = float(hdg) if hdg < 511 else 0.0

        result.append([
            sog, cog, hdg_feat, float(status),
            dt, dist,
            chd, sog_ch, chd_change,
            speed_cons,
            lat_spd, lon_spd,
        ])

    return result


def build_inference_window(history: List[Dict]) -> Optional[List[List[float]]]:
    """
    히스토리 전체에서 추론용 SEQ_LEN 윈도우 생성.

    history 길이 < SEQ_LEN 이면 None 반환.
    마지막 SEQ_LEN 개 레코드로 피처를 계산하며,
    첫 행의 dt=0 은 학습 데이터(preprocess.py)와 동일한 조건.
    """
    if len(history) < SEQ_LEN:
        return None
    window = history[-SEQ_LEN:]
    return extract_features(window)
