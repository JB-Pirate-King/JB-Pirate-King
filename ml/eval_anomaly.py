"""
이상 신호 MSE 측정 + 피처 분석 스크립트

[분석 1] 탐지율/오탐율 테이블
[분석 2] 피처 간 상관행렬 (Pearson)
[분석 3] 시나리오별 재구성 오차 분해 (피처별 MSE)
[분석 4] Permutation Importance

사용법:
    python eval_anomaly.py              # 전체 실행
    python eval_anomaly.py --corr       # 상관행렬만
    python eval_anomaly.py --recon      # 재구성 오차 분해만
    python eval_anomaly.py --perm       # Permutation Importance만
    python eval_anomaly.py --output result.txt  # 결과 파일 저장

피처 (12개):
    sog, cog, heading, status,
    dt, dist_km,
    cog_hdg_diff, sog_change, cog_hdg_change,
    speed_consistency,
    lat_speed, lon_speed
"""

import argparse
import json
import math
import random
import numpy as np
import onnxruntime as ort
from tqdm import tqdm

# matplotlib — 없으면 저장만, 없어도 텍스트 출력은 됨
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    _korean_fonts = [f.name for f in fm.fontManager.ttflist
                     if any(k in f.name for k in ["Malgun", "NanumGothic", "AppleGothic", "Noto Sans CJK"])]
    if _korean_fonts:
        plt.rcParams["font.family"] = _korean_fonts[0]
    plt.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def rjust(s: str, width: int) -> str:
    display_len = sum(2 if 0xAC00 <= ord(c) <= 0xD7A3 or
                         0x3130 <= ord(c) <= 0x318F else 1 for c in s)
    return " " * max(width - display_len, 0) + s


# ── 설정 ──────────────────────────────────────────────────────────
FEATURES = [
    "sog", "cog", "heading", "status",
    "dt", "dist_km",
    "cog_hdg_diff", "sog_change",
    "cog_hdg_change",
    "speed_consistency",
    "lat_speed", "lon_speed",
]
SEQ_LEN        = 10
MODEL_FILE     = "model.onnx"
SCALER_FILE    = "scaler.json"
THRESHOLD_FILE = "threshold.txt"
DATA_FILE      = "ais_preprocessed.csv"
_KN_TO_DPS     = 1852.0 / 111320.0 / 3600.0   # knot → deg/s


# ── 스케일러 ──────────────────────────────────────────────────────
def load_scaler(path):
    with open(path) as f:
        j = json.load(f)
    return j["min"], j["max"]

def scale_val(val, mn, mx):
    d = mx - mn
    return (val - mn) / d if d != 0 else 0.0

def scale_seq(seq, mins, maxs):
    return [[scale_val(v, mins[i], maxs[i]) for i, v in enumerate(row)]
            for row in seq]


# ── ONNX 추론 ─────────────────────────────────────────────────────
def infer(session, seq_scaled):
    x   = np.array(seq_scaled, dtype=np.float32)[np.newaxis]
    out = session.run(None, {"x": x})[0]
    mse = float(np.mean((out - x) ** 2))
    return mse, x, out

def infer_mse(session, seq_scaled):
    mse, _, _ = infer(session, seq_scaled)
    return mse


# ── 실제 정상 시퀀스 로더 ─────────────────────────────────────────
def load_real_normal_seqs(mins, maxs, n_seqs=3000, max_rows=300000) -> list:
    import csv, os
    if not os.path.exists(DATA_FILE):
        return None

    from collections import defaultdict
    mmsi_rows = defaultdict(list)
    with open(DATA_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if max_rows is not None and i >= max_rows:
                break
            try:
                record = [float(row[feat]) for feat in FEATURES]
                mmsi_rows[row["mmsi"]].append(record)
            except (ValueError, KeyError):
                continue

    dt_idx = FEATURES.index("dt")
    all_seqs = []
    for records in mmsi_rows.values():
        seg, current = [], [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK_DT:
                seg.append(current); current = [rec]
            else:
                current.append(rec)
        seg.append(current)
        for s in seg:
            if len(s) < SEQ_LEN:
                continue
            for i in range(len(s) - SEQ_LEN + 1):
                all_seqs.append(s[i:i + SEQ_LEN])

    if not all_seqs:
        return None

    random.shuffle(all_seqs)
    sampled = all_seqs if n_seqs is None else all_seqs[:n_seqs]
    scaled  = [scale_seq(seq, mins, maxs) for seq in sampled]
    print(f"  실제 정상 시퀀스 로드: {len(scaled):,}개 (전체 풀: {len(all_seqs):,})")
    return scaled


SEQ_BREAK_DT = 3600


# ── 시퀀스 생성 헬퍼 ──────────────────────────────────────────────
def _cog_hdg_diff(cog, hdg):
    if hdg >= 511: return -1.0
    d = abs(cog - hdg)
    return 360 - d if d > 180 else d

def _build_derived(step_list):
    result = []
    prev_sog, prev_chd = step_list[0]["sog"], 0.0
    prev_lat = step_list[0].get("lat", 37.0)
    prev_lon = step_list[0].get("lon", 126.0)
    for i, s in enumerate(step_list):
        sog, cog = s["sog"], s["cog"]
        hdg    = s.get("hdg", 511)
        status = s.get("status", 0)
        dt, dist = s["dt"], s["dist_km"]
        lat    = s.get("lat", prev_lat)
        lon    = s.get("lon", prev_lon)
        chd    = _cog_hdg_diff(cog, hdg)
        sog_ch = abs(sog - prev_sog) if i > 0 else 0.0
        chd_change = abs(chd - prev_chd) if (i > 0 and chd >= 0 and prev_chd >= 0) else 0.0

        if i == 0 or sog < 0.1:
            speed_cons = 1.0
        else:
            expected = sog * dt / 3600.0 * 1.852
            speed_cons = round(dist / (expected + 1e-6), 4)

        if i == 0:
            lat_spd = lon_spd = 0.0
        else:
            lat_spd = round((lat - prev_lat) / (dt + 1e-6), 6)
            lon_spd = round((lon - prev_lon) / (dt + 1e-6), 6)

        result.append([sog, cog, hdg if hdg < 511 else 0., status,
                        dt, dist, chd, sog_ch, chd_change,
                        speed_cons, lat_spd, lon_spd])
        prev_sog = sog
        prev_chd = chd if chd >= 0 else prev_chd
        prev_lat, prev_lon = lat, lon
    return result


# ══════════════════════════════════════════════════════════════════
# 시나리오 함수
# ══════════════════════════════════════════════════════════════════

# ── 기존 ──────────────────────────────────────────────────────────
def make_normal_seq():
    sog, cog = random.uniform(5,15), random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(10,30); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog+random.uniform(-5,5))%360,
                       "status":0,"dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
    return _build_derived(steps)

def make_cog_hdg_mismatch_seq():
    sog, cog, mm = random.uniform(.5,20), random.uniform(0,360), random.uniform(90,180)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(5,60); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int((cog+mm)%360),"status":0,
                       "dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
        cog = (cog+random.uniform(-10,10))%360
    return _build_derived(steps)

def make_anchor_move_seq():
    spd, cog = random.uniform(.2,5), random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(5,60); dist = spd*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":spd,"cog":cog,"hdg":int((cog+random.uniform(60,180))%360),
                       "status":1,"dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
        cog = (cog+random.uniform(-30,30))%360
    return _build_derived(steps)

def make_speed_spike_seq():
    base, spike = random.uniform(2,15), random.uniform(20,50)
    ss, sl = random.randint(0,SEQ_LEN-3), random.randint(1,3)
    cog = random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        sog = spike if ss<=i<ss+sl else base
        dt  = random.uniform(5,60); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog+random.uniform(-5,5))%360,
                       "status":0,"dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
        if random.random()<.2: cog=(cog+random.uniform(-30,30))%360
    return _build_derived(steps)

def make_position_jump_seq():
    spd, jd = random.uniform(2,10), random.uniform(5,50)
    ji, cog = random.randint(1,SEQ_LEN-2), random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        dt = random.uniform(5,60)
        dist = jd if i==ji else spd*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":spd,"cog":cog,"hdg":int(cog+random.uniform(-5,5))%360,
                       "status":0,"dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
        cog = random.uniform(0,360)
    return _build_derived(steps)

def make_fn_dt_jump_seq():
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(61,299); sog = random.choice([2.,18.,3.,20.])
        d  = random.uniform(.08,.20)*111.; cog = random.uniform(0,360)
        lat += math.cos(math.radians(cog))*d/111.
        lon += math.sin(math.radians(cog))*d/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog),"status":0,
                       "dt":dt,"dist_km":d,"lat":lat,"lon":lon})
    return _build_derived(steps)

def make_fn_speed_ramp_seq():
    sog, cog = 2., random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(30,55); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog),"status":0,
                       "dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
        sog = 2. if sog >= 29. else min(sog+9.5, 29.)
    return _build_derived(steps)

def make_fn_cog_border_seq():
    sog, cog, mm = random.uniform(3,10), random.uniform(0,360), random.uniform(91,99)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(10,30); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int((cog+mm)%360),"status":0,
                       "dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
        if random.random()<.1: cog=(cog+random.uniform(-10,10))%360
    return _build_derived(steps)

def make_fn_nav_status_seq():
    status = random.choice([2,3,7,8,11,12])
    sog, cog = random.uniform(.5,5), random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(10,30); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog),"status":status,
                       "dt":dt,"dist_km":dist,"lat":lat,"lon":lon})
        if random.random()<.05: cog=(cog+random.uniform(-15,15))%360
    return _build_derived(steps)


# ── D: ML 우회 v1 ─────────────────────────────────────────────────
def make_ml_low_slow_seq():
    """D1 Low&Slow: 모든 규칙 임계값 동시 하회"""
    sog = random.uniform(0.3, 2.0)
    cog = random.uniform(0, 360)
    hdg_offset = random.uniform(50, 95)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(10, 30)
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog, "cog": cog,
                       "hdg": int((cog + hdg_offset) % 360),
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
        sog = max(0.1, sog + random.uniform(-0.15, 0.15))
        cog = (cog + random.uniform(-2, 2)) % 360
        hdg_offset = min(95, max(50, hdg_offset + random.uniform(-2, 2)))
    return _build_derived(steps)

def make_ml_temporal_seq():
    """D2 Temporal Camouflage: 정상 N개 사이 이상 1개 삽입"""
    norm_n = random.randint(5, 10)
    anom_sog = random.uniform(35, 45)
    base_sog = random.uniform(5, 12)
    cog = random.uniform(0, 360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        is_anom = (i % (norm_n + 1) == 0)
        sog = anom_sog if is_anom else base_sog + random.uniform(-0.5, 0.5)
        hdg = int((cog + 160) % 360) if is_anom else int(cog)
        dt = random.uniform(5, 30)
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog, "cog": cog, "hdg": hdg,
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
        cog = (cog + (random.uniform(-30, 30) if is_anom else random.uniform(-5, 5))) % 360
    return _build_derived(steps)

def make_ml_gradual_drift_seq():
    """D3 Gradual Drift: GPS 노이즈 수준 이동 누적"""
    step_deg = random.uniform(0.0003, 0.0006)
    drift_dir = random.uniform(0, 360)
    lat = 37. + random.uniform(-0.1, 0.1)
    lon = 126. + random.uniform(-0.1, 0.1)
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(10, 30)
        sog = random.uniform(0, 0.3)
        cog = random.uniform(0, 360)
        r = math.radians(drift_dir)
        lat += math.cos(r) * step_deg + random.uniform(-step_deg * 0.3, step_deg * 0.3)
        lon += math.sin(r) * step_deg * 1.2 + random.uniform(-step_deg * 0.3, step_deg * 0.3)
        dist = step_deg * 111.
        steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                       "status": 1, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
    return _build_derived(steps)

def make_ml_mimicry_seq():
    """D4 Feature Mimicry: 정상 SOG 프로파일 복사 + 실제 위치 다른 방향 이동"""
    profile = [8.0, 8.2, 8.5, 8.3, 8.1, 7.9, 8.0, 8.2, 8.4, 8.3]
    hidden_sog = random.uniform(10, 20)
    hidden_dir = random.uniform(0, 360)
    cog = random.uniform(0, 360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        report_sog = profile[i % len(profile)]
        dt = random.uniform(10, 30)
        actual_dist = hidden_sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(hidden_dir)) * actual_dist / 111.
        lon += math.sin(math.radians(hidden_dir)) * actual_dist / 111.
        steps.append({"sog": report_sog, "cog": cog, "hdg": int(cog),
                       "status": 0, "dt": dt, "dist_km": actual_dist, "lat": lat, "lon": lon})
        cog = (cog + random.uniform(-3, 3)) % 360
    return _build_derived(steps)


# ── E: ML 우회 v2 (구조적) ───────────────────────────────────────
def make_adv_smooth_seq():
    """E1 Smooth Trajectory: CTRV 운동 유지 → jerk ≈ 0"""
    sog = random.uniform(10, 20)
    cog = random.uniform(0, 360)
    omega = random.uniform(1, 3) * random.choice([-1, 1])
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(5, 20)
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
        cog = (cog + omega * dt) % 360
    return _build_derived(steps)

def make_adv_desync_seq():
    """E2 Fleet Desync: 분산된 타이밍으로 fleet 상관 파괴"""
    base_sog = random.uniform(8, 16)
    spike_sog = random.uniform(30, 42)
    spike_offset = random.uniform(0, 40)
    spike_interval = random.uniform(30, 70)
    cog = random.uniform(0, 360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        t_elapsed = i * 15 + spike_offset
        in_spike = (t_elapsed % spike_interval) < (spike_interval * 0.12)
        sog = spike_sog if in_spike else base_sog + random.uniform(-0.3, 0.3)
        dt = random.uniform(5, 20)
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog, "cog": cog,
                       "hdg": int(cog + random.uniform(-4, 4)) % 360,
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
        if random.random() < 0.08:
            cog = (cog + random.uniform(-8, 8)) % 360
    return _build_derived(steps)

def make_adv_window_edge_seq():
    """E3 Window Edge: window 경계마다 이상 1회로 score 희석"""
    wsize = SEQ_LEN
    norm_sog = random.uniform(8, 14)
    anom_sog = random.uniform(38, 46)
    cog = random.uniform(0, 360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        is_edge = (i % wsize == wsize - 1)
        sog = anom_sog if is_edge else norm_sog + random.uniform(-0.4, 0.4)
        if is_edge:
            cog = (cog + 175 + random.uniform(-3, 3)) % 360
        dt = random.uniform(5, 20)
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
    return _build_derived(steps)

def make_adv_contextual_seq():
    """E4 Contextual Blend: 어선 조업 패턴 위장"""
    _FISH_PHASES = [
        (5.0, 120, 0.0,  0),
        (3.5, 180, 12.0, 7),
        (1.5,  90, 0.0,  7),
        (10.0, 60, 0.0,  0),
        (0.4, 150, 4.0,  7),
    ]
    phase_idx = random.randint(0, len(_FISH_PHASES) - 1)
    phase_elapsed = 0.0
    hidden_sog = random.uniform(2, 4)
    hidden_dir = random.uniform(0, 360)
    lat, lon = 37., 126.
    cog = random.uniform(0, 360)
    steps = []
    for _ in range(SEQ_LEN):
        ph = _FISH_PHASES[phase_idx]
        sog, duration, turn_rate, nav = ph
        dt = random.uniform(10, 30)
        phase_elapsed += dt
        if phase_elapsed >= duration:
            phase_elapsed = 0.0
            phase_idx = (phase_idx + 1) % len(_FISH_PHASES)
        if turn_rate > 0:
            cog = (cog + turn_rate * dt * 0.5) % 360
        actual_dist = hidden_sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(hidden_dir)) * actual_dist / 111.
        lon += math.sin(math.radians(hidden_dir)) * actual_dist / 111.
        steps.append({"sog": sog + random.uniform(-0.3, 0.3), "cog": cog,
                       "hdg": int(cog + random.uniform(-5, 5)) % 360,
                       "status": nav, "dt": dt, "dist_km": actual_dist,
                       "lat": lat, "lon": lon})
    return _build_derived(steps)

def make_adv_shadow_seq():
    """E5 Shadow Vessel: 연안화물 프로파일 + 실제 항로각"""
    _COASTAL_HDGS = [45, 90, 135, 225, 270, 315]
    sog = random.uniform(10, 14)
    base_cog = random.choice(_COASTAL_HDGS) + random.uniform(-10, 10)
    lat = 37. + random.uniform(-0.3, 0.3)
    lon = 126. + random.uniform(-0.3, 0.3)
    target_lat = lat + random.uniform(0.05, 0.15)
    target_lon = lon + random.uniform(0.05, 0.15)
    cog = base_cog
    steps = []
    for _ in range(SEQ_LEN):
        dl = target_lat - lat; dn = target_lon - lon
        want_cog = math.degrees(math.atan2(dn, dl) + 1e-9) % 360
        cog = (0.85 * cog + 0.15 * want_cog) % 360
        dt = random.uniform(10, 30)
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog + random.uniform(-1.5, 1.5), "cog": cog,
                       "hdg": int(cog + random.uniform(-3, 3)) % 360,
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
    return _build_derived(steps)


# ── F: 고급 공격 ──────────────────────────────────────────────────
def make_feat_smoothing_seq():
    """F1 Feature Smoothing: Δ피처 전부 정상 범위 내 클램핑"""
    dsog_max = random.uniform(2.0, 4.0)
    dcog_max = random.uniform(3.0, 7.0)
    dpos_max = 0.01
    sog = random.uniform(5, 12)
    cog = random.uniform(0, 360)
    lat = 37. + random.uniform(-0.05, 0.05)
    lon = 126. + random.uniform(-0.05, 0.05)
    target_lat = lat + random.uniform(0.1, 0.25)
    target_lon = lon + random.uniform(0.1, 0.25)
    steps = []
    for _ in range(SEQ_LEN):
        dl = target_lat - lat; dn = target_lon - lon
        dist_to_target = math.sqrt(dl**2 + dn**2) + 1e-9
        want_cog = math.degrees(math.atan2(dn, dl) + 1e-9) % 360
        want_sog = min(15.0, dist_to_target / (_KN_TO_DPS * 15.0))
        dcog = want_cog - cog
        if dcog > 180: dcog -= 360
        elif dcog < -180: dcog += 360
        dcog = max(-dcog_max, min(dcog_max, dcog))
        cog = (cog + dcog) % 360
        dsog = max(-dsog_max, min(dsog_max, want_sog - sog))
        sog = max(0, sog + dsog)
        dt = random.uniform(10, 25)
        step = min(sog * _KN_TO_DPS * dt, dpos_max)
        lat += math.cos(math.radians(cog)) * step
        lon += math.sin(math.radians(cog)) * step * 1.2
        dist_km = step * 111.
        steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                       "status": 0, "dt": dt, "dist_km": dist_km, "lat": lat, "lon": lon})
    return _build_derived(steps)

def make_intermittent_spoof_seq():
    """F2 Intermittent Spoofing: 정상/이상 교번으로 score 희석"""
    tn = random.uniform(20, 40)
    ta = random.uniform(3, 8)
    anom_sog = random.uniform(40, 50)
    anom_nav = random.choice([4, 5, 6])
    base_sog = random.uniform(5, 12)
    cog = random.uniform(0, 360)
    elapsed = random.uniform(0, tn)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(5, 20)
        t = elapsed % (tn + ta)
        in_attack = t < ta
        sog = anom_sog if in_attack else base_sog + random.uniform(-0.5, 0.5)
        nav = anom_nav if in_attack else 0
        cog = (cog + (random.uniform(-60, 60) if in_attack else random.uniform(-3, 3))) % 360
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                       "status": nav, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
        elapsed += dt
    return _build_derived(steps)

def make_traj_stitch_seq():
    """F3 Trajectory Stitching: 선형 보간으로 C1 연속 궤적"""
    hdg_a = random.uniform(0, 360)
    hdg_b = (hdg_a + random.uniform(120, 240)) % 360
    sog = random.uniform(8, 15)
    stitch_steps = random.randint(3, 5)
    lat = 37. + random.uniform(-0.05, 0.05)
    lon = 126. + random.uniform(-0.05, 0.05)
    cog = hdg_a
    switch_at = random.randint(2, SEQ_LEN - stitch_steps - 1)
    steps = []
    for i in range(SEQ_LEN):
        dt = random.uniform(10, 25)
        dist = sog * dt / 3600 * 1.852
        if i < switch_at:
            cog = hdg_a + random.uniform(-2, 2)
        elif i < switch_at + stitch_steps:
            t = (i - switch_at) / stitch_steps
            dcog = hdg_b - hdg_a
            if dcog > 180: dcog -= 360
            elif dcog < -180: dcog += 360
            cog = (hdg_a + dcog * t + random.uniform(-1, 1)) % 360
        else:
            cog = hdg_b + random.uniform(-2, 2)
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
    return _build_derived(steps)

def make_time_skew_seq():
    """F4 Time Skew: 보고 SOG 정상, 실제 Δpos/Δt 불일치"""
    rep_sog = random.uniform(8, 14)
    burst_n = random.randint(3, 5)
    burst_dist_per_step = random.uniform(0.015, 0.025)
    cog = random.uniform(0, 360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        dt = random.uniform(5, 20)
        in_burst = (i % (burst_n * 3)) < burst_n
        if in_burst:
            act_dist = burst_dist_per_step * 111.
            lat += math.cos(math.radians(cog)) * burst_dist_per_step
            lon += math.sin(math.radians(cog)) * burst_dist_per_step * 1.2
        else:
            act_dist = rep_sog * dt * 0.05 / 3600 * 1.852
            lat += math.cos(math.radians(cog)) * act_dist / 111.
            lon += math.sin(math.radians(cog)) * act_dist / 111.
        steps.append({"sog": rep_sog + random.uniform(-0.3, 0.3), "cog": cog,
                       "hdg": int(cog), "status": 0, "dt": dt,
                       "dist_km": act_dist, "lat": lat, "lon": lon})
    return _build_derived(steps)

def make_multi_coord_seq():
    """F5 Multi-Ship Coordination: 개별 정상 + fleet-level 수렴"""
    _COASTAL_HDGS = [45, 90, 135, 225, 270, 315]
    base_cog = random.choice(_COASTAL_HDGS) + random.uniform(-10, 10)
    bias = random.uniform(0.2, 0.4)
    sog = random.uniform(8, 13)
    lat = 37. + random.uniform(-0.1, 0.1)
    lon = 126. + random.uniform(-0.1, 0.1)
    target_lat = lat + random.uniform(0.1, 0.2)
    target_lon = lon + random.uniform(0.1, 0.2)
    cog = base_cog
    steps = []
    for _ in range(SEQ_LEN):
        dl = target_lat - lat; dn = target_lon - lon
        tc = math.degrees(math.atan2(dn, dl) + 1e-9) % 360
        cog = ((1 - bias) * base_cog + bias * tc + random.uniform(-5, 5)) % 360
        dt = random.uniform(10, 25)
        dist = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist / 111.
        lon += math.sin(math.radians(cog)) * dist / 111.
        steps.append({"sog": sog + random.uniform(-0.4, 0.4), "cog": cog,
                       "hdg": int(cog + random.uniform(-4, 4)) % 360,
                       "status": 0, "dt": dt, "dist_km": dist, "lat": lat, "lon": lon})
    return _build_derived(steps)

def make_ais_gap_seq():
    """F6 AIS Gap: 신호 소실 → 위치 도약 재등장"""
    sog = random.uniform(8, 14)
    cog = random.uniform(0, 360)
    gap_at = random.randint(2, SEQ_LEN - 3)
    gap_dt = random.uniform(300, 600)
    jump_dist = random.uniform(0.15, 0.4)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        if i == gap_at:
            ang = random.uniform(0, 360)
            lat += math.cos(math.radians(ang)) * jump_dist
            lon += math.sin(math.radians(ang)) * jump_dist * 1.2
            dist = jump_dist * 111.
            steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                           "status": 0, "dt": gap_dt, "dist_km": dist,
                           "lat": lat, "lon": lon})
        else:
            dt = random.uniform(10, 30)
            dist = sog * dt / 3600 * 1.852
            lat += math.cos(math.radians(cog)) * dist / 111.
            lon += math.sin(math.radians(cog)) * dist / 111.
            steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                           "status": 0, "dt": dt, "dist_km": dist,
                           "lat": lat, "lon": lon})
            cog = (cog + random.uniform(-3, 3)) % 360
    return _build_derived(steps)

def make_lstm_beat_seq():
    """F7 LSTM Beat: Δ피처 1σ 내로 클램핑하며 천천히 이동"""
    sigma_sog = random.uniform(0.3, 0.6)
    sigma_cog = random.uniform(2.0, 4.0)
    sog = random.uniform(6, 12)
    cog = random.uniform(0, 360)
    lat = 37. + random.uniform(-0.05, 0.05)
    lon = 126. + random.uniform(-0.05, 0.05)
    target_lat = lat + random.uniform(0.2, 0.35)
    target_lon = lon + random.uniform(0.2, 0.35)
    steps = []
    for _ in range(SEQ_LEN):
        dl = target_lat - lat; dn = target_lon - lon
        want_cog = math.degrees(math.atan2(dn, dl) + 1e-9) % 360
        dcog = want_cog - cog
        if dcog > 180: dcog -= 360
        elif dcog < -180: dcog += 360
        dcog = max(-sigma_cog, min(sigma_cog, dcog * 0.15))
        cog = (cog + dcog) % 360
        dsog = random.gauss(0, sigma_sog) * 0.3
        sog = max(2.0, min(20.0, sog + dsog))
        dt = random.uniform(10, 25)
        step = min(sog * _KN_TO_DPS * dt, 0.002)
        lat += math.cos(math.radians(cog)) * step
        lon += math.sin(math.radians(cog)) * step * 1.2
        dist_km = step * 111.
        steps.append({"sog": sog, "cog": cog, "hdg": int(cog),
                       "status": 0, "dt": dt, "dist_km": dist_km,
                       "lat": lat, "lon": lon})
    return _build_derived(steps)


# ── SCENARIO_MAKERS ───────────────────────────────────────────────
SCENARIO_MAKERS = [
    # 기존
    ("정상",          make_normal_seq,           False),
    ("COG/HDG불일치", make_cog_hdg_mismatch_seq, True),
    ("정박이동",      make_anchor_move_seq,       True),
    ("속도이상",      make_speed_spike_seq,       True),
    ("위치점프",      make_position_jump_seq,     True),
    ("FN1-dt점프",   make_fn_dt_jump_seq,        True),
    ("FN2-속도단계", make_fn_speed_ramp_seq,     True),
    ("FN3-COG경계", make_fn_cog_border_seq,     True),
    ("FN4-status",  make_fn_nav_status_seq,     True),
    # D: ML 우회 v1
    ("D1-LowSlow",   make_ml_low_slow_seq,       True),
    ("D2-Temporal",  make_ml_temporal_seq,       True),
    ("D3-GradDrift", make_ml_gradual_drift_seq,  True),
    ("D4-Mimicry",   make_ml_mimicry_seq,        True),
    # E: ML 우회 v2
    ("E1-Smooth",    make_adv_smooth_seq,        True),
    ("E2-Desync",    make_adv_desync_seq,        True),
    ("E3-WinEdge",   make_adv_window_edge_seq,   True),
    ("E4-Contextual",make_adv_contextual_seq,    True),
    ("E5-Shadow",    make_adv_shadow_seq,        True),
    # F: 고급 공격
    ("F1-FeatSmooth",make_feat_smoothing_seq,    True),
    ("F2-Intermit",  make_intermittent_spoof_seq,True),
    ("F3-TrajStitch",make_traj_stitch_seq,       True),
    ("F4-TimeSkew",  make_time_skew_seq,         True),
    ("F5-MultiCoord",make_multi_coord_seq,       True),
    ("F6-AISGap",    make_ais_gap_seq,           True),
    ("F7-LSTMBeat",  make_lstm_beat_seq,         True),
]


# ══════════════════════════════════════════════════════════════════
# 분석 1: 탐지율/오탐율 테이블
# ══════════════════════════════════════════════════════════════════
def analysis_detection(session, mins, maxs, threshold, real_seqs=None, N=None):
    print("\n" + "="*60)
    print("  [분석 1] 탐지율 / 오탐율 테이블")
    print("="*60)

    N_ANOM = 500

    all_errors = []
    for name, maker, is_anom in tqdm(SCENARIO_MAKERS, desc="분석1 시나리오", unit="개"):
        if not is_anom and real_seqs is not None:
            seqs = real_seqs if N is None else real_seqs[:N]
            errs = np.array([infer_mse(session, seq)
                             for seq in tqdm(seqs, desc=f"  {name}", leave=False, unit="seq")])
        else:
            n = N_ANOM if N is None else N
            errs = np.array([infer_mse(session, scale_seq(maker(), mins, maxs))
                             for _ in tqdm(range(n), desc=f"  {name}", leave=False, unit="seq")])
        all_errors.append((name, errs))

    ne      = all_errors[0][1]
    N_ne    = len(ne)
    fp_rate = np.sum(ne > threshold) / N_ne * 100
    print(f"\n  임계값: {threshold:.6f}  |  정상 시퀀스: {N_ne:,}개  |  오탐율: {fp_rate:.1f}%\n")

    col_w = 16
    sep   = "─" * (12 + col_w * len(all_errors))
    print(sep)
    print("".rjust(12) + "".join(rjust(n, col_w) for n, _ in all_errors))
    print(sep)
    for label, fn in [
        ("평균 MSE",  lambda e: f"{e.mean():.6f}"),
        ("95th %ile", lambda e: f"{np.percentile(e,95):.6f}"),
        ("탐지율",    lambda e: f"{np.sum(e>threshold)/len(e)*100:.1f}%"),
    ]:
        print(rjust(label,12) + "".join(rjust(fn(e), col_w) for _, e in all_errors))
    print(sep)

    anom = [(n,e) for (n,e),(_,_,ia) in zip(all_errors,SCENARIO_MAKERS) if ia]
    print("\n  [임계값별 탐지율/오탐율]")
    print("  " + "임계값".rjust(12) + "오탐율".rjust(9) +
          "".join(rjust(n,16) for n,_ in anom))
    print("  " + "─"*(12+9+16*len(anom)))
    for pct in [99,98,97,95,90]:
        thr = np.percentile(ne, pct)
        fp  = np.sum(ne>thr)/N_ne*100
        row = rjust(f"{thr:.6f}",12) + rjust(f"{fp:.1f}%",9)
        row += "".join(rjust(f"{np.sum(e>thr)/len(e)*100:.1f}%",16) for _,e in anom)
        print("  "+row)
    print("\n→ threshold.txt 를 위 값 중 적절한 것으로 교체하세요.")
    return all_errors


# ══════════════════════════════════════════════════════════════════
# 분석 2: 피처 간 상관행렬 (Pearson)
# ══════════════════════════════════════════════════════════════════
def analysis_correlation():
    print("\n" + "="*60)
    print("  [분석 2] 피처 간 상관행렬 (Pearson)")
    print("="*60)

    import csv, os
    if not os.path.exists(DATA_FILE):
        print(f"  ⚠ {DATA_FILE} 없음 — 상관행렬 건너뜀")
        return None

    data = {f: [] for f in FEATURES}
    row_count = 0
    with open(DATA_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                for feat in FEATURES:
                    data[feat].append(float(row[feat]))
                row_count += 1
            except (ValueError, KeyError):
                continue

    print(f"  사용 행 수: {row_count:,} (전체)")
    arr  = np.array([data[f] for f in FEATURES], dtype=np.float32)
    corr = np.corrcoef(arr)

    print("\n  |상관| ≥ 0.5 피처 쌍:")
    found = False
    for i in range(len(FEATURES)):
        for j in range(i+1, len(FEATURES)):
            if abs(corr[i,j]) >= 0.5:
                print(f"    {FEATURES[i]:25s} ↔ {FEATURES[j]:25s}  r={corr[i,j]:+.3f}")
                found = True
    if not found:
        print("    없음")

    if HAS_MPL:
        fig, ax = plt.subplots(figsize=(10, 8))
        im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1)
        ax.set_xticks(range(len(FEATURES)))
        ax.set_xticklabels(FEATURES, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(FEATURES)))
        ax.set_yticklabels(FEATURES, fontsize=8)
        for i in range(len(FEATURES)):
            for j in range(len(FEATURES)):
                ax.text(j, i, f"{corr[i,j]:.2f}", ha="center", va="center",
                        fontsize=6, color="white" if abs(corr[i,j])>0.6 else "black")
        plt.colorbar(im, ax=ax)
        ax.set_title("Feature Correlation Matrix (Pearson)")
        plt.tight_layout()
        plt.savefig("feature_correlation.png", dpi=150)
        plt.close()
        print("\n  → feature_correlation.png 저장")

    return corr


# ══════════════════════════════════════════════════════════════════
# 분석 3: 시나리오별 재구성 오차 분해 (피처별 MSE)
# ══════════════════════════════════════════════════════════════════
def analysis_reconstruction(session, mins, maxs, real_seqs=None, N=None):
    print("\n" + "="*60)
    print("  [분석 3] 시나리오별 재구성 오차 분해 (피처별 MSE)")
    print("="*60)

    N_ANOM = 500

    scenario_feat_mse = {}
    for name, maker, is_anom in tqdm(SCENARIO_MAKERS, desc="분석3 시나리오", unit="개"):
        feat_mse = np.zeros(len(FEATURES))
        if not is_anom and real_seqs is not None:
            seqs = real_seqs if N is None else real_seqs[:N]
            for seq in tqdm(seqs, desc=f"  {name}", leave=False, unit="seq"):
                _, x, out = infer(session, seq)
                feat_mse += ((out - x) ** 2).mean(axis=(0, 1))
            feat_mse /= len(seqs)
        else:
            n = N_ANOM if N is None else N
            for _ in tqdm(range(n), desc=f"  {name}", leave=False, unit="seq"):
                seq = scale_seq(maker(), mins, maxs)
                _, x, out = infer(session, seq)
                feat_mse += ((out - x) ** 2).mean(axis=(0, 1))
            feat_mse /= n
        scenario_feat_mse[name] = feat_mse

    col_w = 13
    names = [n for n,_,_ in SCENARIO_MAKERS]
    header = "피처".ljust(22) + "".join(rjust(n, col_w) for n in names)
    sep    = "─" * len(header)
    print("\n" + sep + "\n" + header + "\n" + sep)
    for fi, feat in enumerate(FEATURES):
        row = feat.ljust(22)
        for name in names:
            row += rjust(f"{scenario_feat_mse[name][fi]:.4f}", col_w)
        print(row)
    print(sep)

    if HAS_MPL:
        mat   = np.array([scenario_feat_mse[n] for n in names])
        mat_n = mat / (mat.max(axis=1, keepdims=True) + 1e-9)

        fig, axes = plt.subplots(1, 2, figsize=(18, 7))
        for ax, data, title in [
            (axes[0], mat.T,   "Reconstruction Error (Absolute MSE)"),
            (axes[1], mat_n.T, "Reconstruction Error (Relative within scenario)"),
        ]:
            im = ax.imshow(data, cmap="YlOrRd", aspect="auto")
            ax.set_xticks(range(len(names)))
            ax.set_xticklabels(names, rotation=40, ha="right", fontsize=8)
            ax.set_yticks(range(len(FEATURES)))
            ax.set_yticklabels(FEATURES, fontsize=8)
            ax.set_title(title)
            plt.colorbar(im, ax=ax)

        plt.tight_layout()
        plt.savefig("reconstruction_error.png", dpi=150)
        plt.close()
        print("\n  → reconstruction_error.png 저장")

    return scenario_feat_mse


# ══════════════════════════════════════════════════════════════════
# 분석 4: Permutation Importance
# ══════════════════════════════════════════════════════════════════
def analysis_permutation(session, mins, maxs, real_seqs=None, N=3000, repeat=5):
    print("\n" + "="*60)
    print("  [분석 4] Permutation Importance")
    print("="*60)

    if real_seqs is not None:
        seqs_scaled = real_seqs if N is None else real_seqs[:N]
        print(f"  실제 정상 시퀀스 {len(seqs_scaled):,}개 × 반복 {repeat}회")
    else:
        n = 5000 if N is None else N
        seqs_scaled = [scale_seq(make_normal_seq(), mins, maxs) for _ in range(n)]
        print(f"  합성 정상 시퀀스 {len(seqs_scaled):,}개 × 반복 {repeat}회 (CSV 없음)")

    N_ANOM = 500
    anom_scenarios = [(name, maker) for name, maker, is_anom in SCENARIO_MAKERS if is_anom]
    print(f"  이상 시나리오 {len(anom_scenarios)}개 × {N_ANOM:,}개 × 반복 {repeat}회\n")

    arr_normal = np.array(seqs_scaled, dtype=np.float32)
    n_normal   = len(arr_normal)

    anom_arrays = {}
    for name, maker in tqdm(anom_scenarios, desc="이상 시퀀스 생성", unit="시나리오"):
        seqs = [scale_seq(maker(), mins, maxs) for _ in range(N_ANOM)]
        anom_arrays[name] = np.array(seqs, dtype=np.float32)

    def batch_mse(x_arr, desc="추론"):
        return np.mean([infer_mse(session, x_arr[i].tolist())
                        for i in tqdm(range(len(x_arr)), desc=f"  {desc}", leave=False, unit="seq")])

    print("\n  [4-A] 정상 기준 (ΔMSE↑ = 정상 재구성에 중요)")
    baseline_normal = batch_mse(arr_normal, desc="baseline 정상")
    print(f"  baseline MSE (정상): {baseline_normal:.6f}")

    imp_normal = np.zeros(len(FEATURES))
    for fi in tqdm(range(len(FEATURES)), desc="정상 기준 피처", unit="피처"):
        delta_sum = 0.
        for r in range(repeat):
            shuffled = arr_normal.copy()
            shuffled[:, :, fi] = arr_normal[np.random.permutation(n_normal), :, fi]
            delta_sum += batch_mse(shuffled, desc=f"{FEATURES[fi]} r{r+1}") - baseline_normal
        imp_normal[fi] = delta_sum / repeat

    order_n = np.argsort(imp_normal)[::-1]
    print(f"\n  {'순위':>4}  {'피처':<25}  {'ΔMSE(정상)':>12}  상대 중요도")
    print("  " + "─"*68)
    max_n = max(imp_normal[order_n[0]], 1e-9)
    for rank, fi in enumerate(order_n):
        bar = "█" * max(int(imp_normal[fi] / max_n * 20), 0)
        print(f"  {rank+1:>4}  {FEATURES[fi]:<25}  {imp_normal[fi]:>+12.6f}  {bar}")

    print("\n  [4-B] 이상 기준 (ΔMSE↓ = 이상 탐지에 실제로 기여하는 피처)")
    baseline_anom = {}
    for name, arr in tqdm(anom_arrays.items(), desc="baseline 이상", unit="시나리오"):
        baseline_anom[name] = batch_mse(arr, desc=f"baseline {name}")

    imp_anom = np.zeros((len(FEATURES), len(anom_scenarios)))
    for fi in tqdm(range(len(FEATURES)), desc="이상 기준 피처", unit="피처"):
        for si, (name, _) in enumerate(anom_scenarios):
            arr_a = anom_arrays[name]
            n_a   = len(arr_a)
            delta_sum = 0.
            for r in range(repeat):
                shuffled = arr_a.copy()
                shuffled[:, :, fi] = arr_a[np.random.permutation(n_a), :, fi]
                delta_sum += batch_mse(shuffled, desc=f"{FEATURES[fi]}/{name} r{r+1}") - baseline_anom[name]
            imp_anom[fi, si] = delta_sum / repeat

    imp_anom_mean = imp_anom.mean(axis=1)
    order_a = np.argsort(imp_anom_mean)

    print(f"\n  {'순위':>4}  {'피처':<25}  {'평균 ΔMSE':>12}  탐지 기여도")
    print("  " + "─"*68)
    min_a = min(imp_anom_mean[order_a[0]], -1e-9)
    for rank, fi in enumerate(order_a):
        bar = "█" * max(int(imp_anom_mean[fi] / min_a * 20), 0)
        sign = "▼" if imp_anom_mean[fi] < 0 else " "
        print(f"  {rank+1:>4}  {FEATURES[fi]:<25}  {imp_anom_mean[fi]:>+12.6f} {sign} {bar}")

    print(f"\n  [시나리오별 ΔMSE]")
    col_w = 14
    header = "피처".ljust(25) + "".join(rjust(n, col_w) for n, _ in anom_scenarios)
    sep    = "─" * len(header)
    print("  " + sep + "\n  " + header + "\n  " + sep)
    for fi, feat in enumerate(FEATURES):
        row = feat.ljust(25)
        for si in range(len(anom_scenarios)):
            row += rjust(f"{imp_anom[fi, si]:+.4f}", col_w)
        print("  " + row)
    print("  " + sep)

    if HAS_MPL:
        fig, axes = plt.subplots(1, 2, figsize=(16, 6))
        colors_n = ["#e74c3c" if imp_normal[fi] >= 0 else "#3498db" for fi in order_n]
        axes[0].barh([FEATURES[fi] for fi in order_n[::-1]],
                     [imp_normal[fi] for fi in order_n[::-1]], color=colors_n[::-1])
        axes[0].axvline(0, color="black", linewidth=0.8)
        axes[0].set_xlabel("ΔMSE (shuffled - baseline)")
        axes[0].set_title("Permutation Importance\n(정상 기준: 재구성 의존도)")

        colors_a = ["#e74c3c" if imp_anom_mean[fi] >= 0 else "#2ecc71" for fi in order_a[::-1]]
        axes[1].barh([FEATURES[fi] for fi in order_a[::-1]],
                     [imp_anom_mean[fi] for fi in order_a[::-1]], color=colors_a)
        axes[1].axvline(0, color="black", linewidth=0.8)
        axes[1].set_xlabel("ΔMSE (shuffled - baseline)")
        axes[1].set_title("Permutation Importance\n(이상 기준: 탐지 기여도, 음수=기여)")

        plt.tight_layout()
        plt.savefig("permutation_importance.png", dpi=150)
        plt.close()
        print("\n  → permutation_importance.png 저장")

    return imp_normal, imp_anom


# ══════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════
def main():
    import sys, os

    parser = argparse.ArgumentParser()
    parser.add_argument("--corr",   action="store_true")
    parser.add_argument("--recon",  action="store_true")
    parser.add_argument("--perm",   action="store_true")
    parser.add_argument("--output", type=str, default="eval_result.txt",
                        help="텍스트 결과 저장 파일명 (기본: eval_result.txt)")
    args = parser.parse_args()
    run_all = not any([args.corr, args.recon, args.perm])

    mins, maxs = load_scaler(SCALER_FILE)
    with open(THRESHOLD_FILE) as f:
        threshold = float(f.read())
    session = ort.InferenceSession(MODEL_FILE, providers=["CPUExecutionProvider"])

    # ── stdout → 터미널 + 파일 동시 출력 ────────────────────────
    class Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data); s.flush()
        def flush(self):
            for s in self.streams: s.flush()

    out_file = open(args.output, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, out_file)

    try:
        if not HAS_MPL:
            print("  ⚠ matplotlib 없음 — 그래프 저장 건너뜀 (pip install matplotlib)")

        print("\n실제 정상 시퀀스 로드 중...")
        real_seqs_all = load_real_normal_seqs(mins, maxs, n_seqs=None)
        if real_seqs_all is None:
            print(f"  ⚠ {DATA_FILE} 없음 — 합성 정상 시퀀스 사용")
        real_seqs = real_seqs_all[:5000] if real_seqs_all and len(real_seqs_all) > 5000 else real_seqs_all

        if run_all:
            analysis_detection(session, mins, maxs, threshold, real_seqs=real_seqs_all)
            analysis_correlation()
            analysis_reconstruction(session, mins, maxs, real_seqs=real_seqs)
            analysis_permutation(session, mins, maxs, real_seqs=real_seqs, repeat=10)
        else:
            if args.corr:  analysis_correlation()
            if args.recon: analysis_reconstruction(session, mins, maxs, real_seqs=real_seqs)
            if args.perm:  analysis_permutation(session, mins, maxs, real_seqs=real_seqs, repeat=10)

        print("\n완료!")
        if HAS_MPL:
            saved = [f for f in ["feature_correlation.png",
                                  "reconstruction_error.png",
                                  "permutation_importance.png"] if os.path.exists(f)]
            if saved:
                print("저장된 그래프:", ", ".join(saved))
        print(f"\n→ 텍스트 결과 저장: {args.output}")

    finally:
        sys.stdout = sys.__stdout__
        out_file.close()


if __name__ == "__main__":
    main()