"""
이상 신호 MSE 측정 스크립트

model.onnx + scaler.json + threshold.txt 를 로드하여
정상/이상 시퀀스의 MSE 분포를 비교한다.

사용법:
    python eval_anomaly.py
"""

import json
import math
import random
import numpy as np
import onnxruntime as ort

def rjust(s: str, width: int) -> str:
    """한글 2바이트 폭 고려 우측 정렬"""
    display_len = 0
    for ch in s:
        cp = ord(ch)
        if (0xAC00 <= cp <= 0xD7A3 or  # 한글 완성형
            0x1100 <= cp <= 0x11FF or   # 한글 자모
            0x3130 <= cp <= 0x318F or   # 호환 자모
            0xFF01 <= cp <= 0xFF60):    # 전각
            display_len += 2
        else:
            display_len += 1
    pad = width - display_len
    return " " * max(pad, 0) + s


# ── 설정 (train.py 와 동일) ───────────────────────
FEATURES     = ["sog", "cog", "heading", "status", "dt", "dist_km",
                "expected_dist_km", "bearing_cog_diff", "cog_hdg_diff",
                "sog_change", "cog_change", "status_sog_product", "dist_expected_ratio"]
SEQ_LEN      = 10
MODEL_FILE   = "model.onnx"
SCALER_FILE  = "scaler.json"
THRESHOLD_FILE = "threshold.txt"


# ── 스케일러 로드 ─────────────────────────────────
def load_scaler(path):
    with open(path) as f:
        j = json.load(f)
    return j["min"], j["max"]

def scale(val, mn, mx):
    denom = mx - mn
    return (val - mn) / denom if denom != 0 else 0.0

def scale_seq(seq, mins, maxs):
    return [[scale(v, mins[i], maxs[i]) for i, v in enumerate(row)]
            for row in seq]


# ── ONNX 추론 ─────────────────────────────────────
def infer(session, seq_scaled):
    x = np.array(seq_scaled, dtype=np.float32)[np.newaxis]  # (1, 10, 9)
    output = session.run(None, {"x": x})[0]
    mse = float(np.mean((output - x) ** 2))
    return mse


# ── 시퀀스 생성 헬퍼 ──────────────────────────────
def extra_features(sog, prev_sog, cog, prev_cog, status, dist_km, expected_dist):
    """추가 피처 4개 계산"""
    sog_change = abs(sog - prev_sog)
    cog_diff = abs(cog - prev_cog)
    if cog_diff > 180: cog_diff = 360 - cog_diff
    cog_change = cog_diff
    status_sog_product = status * sog
    dist_expected_ratio = dist_km / (expected_dist + 1e-6)
    return sog_change, cog_change, status_sog_product, dist_expected_ratio


def make_normal_seq():
    """정상 선박: 일정 속도, COG≈HDG"""
    sog = random.uniform(5, 15)
    cog = random.uniform(0, 360)
    seq = []
    lat, lon = 37.0, 126.0
    prev_sog, prev_cog = sog, cog
    for i in range(SEQ_LEN):
        dt = random.uniform(10, 30)
        dist_km = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist_km / 111.0
        lon += math.sin(math.radians(cog)) * dist_km / 111.0
        hdg = int(cog + random.uniform(-5, 5)) % 360
        cog_hdg_diff = abs(cog - hdg)
        if cog_hdg_diff > 180: cog_hdg_diff = 360 - cog_hdg_diff
        expected_dist = sog * dt / 3600 * 1.852
        sc, cc, sp, dr = extra_features(sog, prev_sog, cog, prev_cog, 0, dist_km, expected_dist)
        seq.append([sog, cog, hdg, 0, dt, dist_km, expected_dist, 0.0, cog_hdg_diff, sc, cc, sp, dr])
        prev_sog, prev_cog = sog, cog
    return seq


def make_cog_hdg_mismatch_seq():
    """COG/HDG 불일치: 불일치 각도 90~180도 랜덤"""
    sog      = random.uniform(0.5, 20)
    cog      = random.uniform(0, 360)
    mismatch = random.uniform(90, 180)
    offset   = random.uniform(0, 180)
    drift    = random.uniform(0, 10)
    seq = []
    lat, lon = 37.0, 126.0
    prev_sog, prev_cog = sog, cog
    for i in range(SEQ_LEN):
        dt = random.uniform(5, 60)
        dist_km = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist_km / 111.0
        lon += math.sin(math.radians(cog)) * dist_km / 111.0
        hdg = int((cog + mismatch + offset) % 360)
        cog_hdg_diff = abs(cog - hdg)
        if cog_hdg_diff > 180: cog_hdg_diff = 360 - cog_hdg_diff
        expected_dist = sog * dt / 3600 * 1.852
        sc, cc, sp, dr = extra_features(sog, prev_sog, cog, prev_cog, 0, dist_km, expected_dist)
        seq.append([sog, cog, hdg, 0, dt, dist_km, expected_dist, 0.0, cog_hdg_diff, sc, cc, sp, dr])
        prev_sog, prev_cog = sog, cog
        cog = (cog + random.uniform(-drift, drift)) % 360
    return seq


def make_anchor_move_seq():
    """정박 이동: 속도 0.2~5kn, 다양한 방향"""
    speed = random.uniform(0.2, 5.0)
    cog   = random.uniform(0, 360)
    drift = random.uniform(0, 1.0)
    seq = []
    lat, lon = 37.0, 126.0
    prev_sog, prev_cog = speed, cog
    for i in range(SEQ_LEN):
        dt = random.uniform(5, 60)
        dist_km = speed * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist_km / 111.0
        lon += math.sin(math.radians(cog)) * dist_km / 111.0
        hdg = int((cog + random.uniform(60, 180)) % 360)
        cog_hdg_diff = abs(cog - hdg)
        if cog_hdg_diff > 180: cog_hdg_diff = 360 - cog_hdg_diff
        expected_dist = speed * dt / 3600 * 1.852
        sc, cc, sp, dr = extra_features(speed, prev_sog, cog, prev_cog, 1, dist_km, expected_dist)
        seq.append([speed, cog, hdg, 1, dt, dist_km, expected_dist, 0.0, cog_hdg_diff, sc, cc, sp, dr])
        prev_sog, prev_cog = speed, cog
        cog = (cog + random.uniform(-30, 30) * drift) % 360
    return seq


def make_speed_spike_seq():
    """속도 이상: 스파이크 속도 20~50kn, 위치/길이 랜덤"""
    base_speed  = random.uniform(2, 15)
    spike_speed = random.uniform(20, 50)
    spike_start = random.randint(0, SEQ_LEN - 3)
    spike_len   = random.randint(1, SEQ_LEN - spike_start)
    cog = random.uniform(0, 360)
    seq = []
    lat, lon = 37.0, 126.0
    prev_sog, prev_cog = base_speed, cog
    for i in range(SEQ_LEN):
        dt  = random.uniform(5, 60)
        sog = spike_speed if spike_start <= i < spike_start + spike_len else base_speed
        dist_km = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist_km / 111.0
        lon += math.sin(math.radians(cog)) * dist_km / 111.0
        hdg = int(cog + random.uniform(-5, 5)) % 360
        cog_hdg_diff = abs(cog - hdg)
        if cog_hdg_diff > 180: cog_hdg_diff = 360 - cog_hdg_diff
        expected_dist = sog * dt / 3600 * 1.852
        sc, cc, sp, dr = extra_features(sog, prev_sog, cog, prev_cog, 0, dist_km, expected_dist)
        seq.append([sog, cog, hdg, 0, dt, dist_km, expected_dist, 0.0, cog_hdg_diff, sc, cc, sp, dr])
        prev_sog, prev_cog = sog, cog
        if random.random() < 0.2:
            cog = (cog + random.uniform(-30, 30)) % 360
    return seq


def make_position_jump_seq():
    """위치 점프: 점프 거리 5~50km, 발생 위치 랜덤"""
    base_speed = random.uniform(2, 10)
    jump_dist  = random.uniform(5, 50)
    jump_idx   = random.randint(1, SEQ_LEN - 2)
    cog = random.uniform(0, 360)
    seq = []
    lat, lon = 37.0, 126.0
    prev_sog, prev_cog = base_speed, cog
    for i in range(SEQ_LEN):
        dt = random.uniform(5, 60)
        if i == jump_idx:
            dist_km = jump_dist
            sog     = base_speed
        else:
            sog     = base_speed
            dist_km = sog * dt / 3600 * 1.852
        lat += math.cos(math.radians(cog)) * dist_km / 111.0
        lon += math.sin(math.radians(cog)) * dist_km / 111.0
        hdg = int(cog + random.uniform(-5, 5)) % 360
        cog_hdg_diff = abs(cog - hdg)
        if cog_hdg_diff > 180: cog_hdg_diff = 360 - cog_hdg_diff
        expected_dist = sog * dt / 3600 * 1.852
        sc, cc, sp, dr = extra_features(sog, prev_sog, cog, prev_cog, 0, dist_km, expected_dist)
        seq.append([sog, cog, hdg, 0, dt, dist_km, expected_dist, 0.0, cog_hdg_diff, sc, cc, sp, dr])
        prev_sog, prev_cog = sog, cog
        cog = random.uniform(0, 360)
    return seq


# ── 메인 ─────────────────────────────────────────


def main():
    mins, maxs = load_scaler(SCALER_FILE)
    with open(THRESHOLD_FILE) as f:
        threshold = float(f.read())

    session = ort.InferenceSession(MODEL_FILE,
        providers=["CPUExecutionProvider"])

    N = 500
    normal_errors  = []
    anomaly_errors = []

    for _ in range(N):
        seq = make_normal_seq()
        normal_errors.append(infer(session, scale_seq(seq, mins, maxs)))

    scenarios = [
        ("COG/HDG 불일치", [make_cog_hdg_mismatch_seq() for _ in range(N)]),
        ("정박 이동",       [make_anchor_move_seq()       for _ in range(N)]),
        ("속도 이상",       [make_speed_spike_seq()       for _ in range(N)]),
        ("위치 점프",       [make_position_jump_seq()     for _ in range(N)]),
    ]

    ne = np.array(normal_errors)
    fp_rate = np.sum(ne > threshold) / N * 100

    print(f"\n  현재 임계값: {threshold:.6f}  |  오탐율: {fp_rate:.1f}%\n")
    col_w = 18

    all_errors = [("정상", ne, False)]
    for name, seqs in scenarios:
        errs = np.array([infer(session, scale_seq(s, mins, maxs)) for s in seqs])
        all_errors.append((name, errs, True))

    total_w = 12 + col_w * len(all_errors)
    sep = "─" * total_w

    # 헤더
    header = "" .rjust(12)
    for name, _, _ in all_errors:
        header += rjust(name, col_w)
    print(sep)
    print(header)
    print(sep)

    # 데이터 행
    stat_rows = [
        ("평균 MSE",   lambda e: f"{e.mean():.6f}"),
        ("95th %ile",  lambda e: f"{np.percentile(e,95):.6f}"),
        ("탐지율",     lambda e: f"{np.sum(e > threshold)/N*100:.1f}%"),
    ]
    for label, fn in stat_rows:
        line = rjust(label, 12)
        for _, errs, _ in all_errors:
            line += rjust(fn(errs), col_w)
        print(line)
    print(sep)

    # 권장 임계값 테이블
    print("\n  [임계값별 탐지율/오탐율]")
    th_col = 12
    fp_col = 9
    det_col = 18
    anom_names = [(name, errs) for name, errs, is_anom in all_errors if is_anom]

    hdr2 = "임계값".rjust(th_col) + "오탐율".rjust(fp_col)
    for name, _ in anom_names:
        hdr2 += rjust(name, det_col)
    print("  " + hdr2)
    print("  " + "─" * (th_col + fp_col + det_col * len(anom_names)))

    for pct in [99, 98, 97, 95, 90]:
        thr = np.percentile(ne, pct)
        fp  = np.sum(ne > thr) / N * 100
        row = rjust(f"{thr:.6f}", th_col) + rjust(f"{fp:.1f}%", fp_col)
        for _, errs in anom_names:
            det = np.sum(errs > thr) / N * 100
            row += rjust(f"{det:.1f}%", det_col)
        print("  " + row)
    print("\n→ threshold.txt 를 위 값 중 적절한 것으로 교체하세요.")


if __name__ == "__main__":
    main()