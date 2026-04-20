"""
이상 신호 MSE 측정 + 피처 분석 스크립트

[분석 1] 탐지율/오탐율 테이블 (기존)
[분석 2] 피처 간 상관행렬 (Pearson)
[분석 3] 시나리오별 재구성 오차 분해 (피처별 MSE)
[분석 4] Permutation Importance

사용법:
    python eval_anomaly.py              # 전체 실행
    python eval_anomaly.py --corr       # 상관행렬만
    python eval_anomaly.py --recon      # 재구성 오차 분해만
    python eval_anomaly.py --perm       # Permutation Importance만
"""

import argparse
import json
import math
import random
import statistics
import numpy as np
import onnxruntime as ort
from collections import deque

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
    "dt", "dist_km", "expected_dist_km",
    "bearing_cog_diff", "cog_hdg_diff",
    "sog_change", "cog_change",
    "sog_status_ratio",
    "dist_expected_ratio",
    "cog_hdg_change",
    "cog_hdg_std",
]
SEQ_LEN        = 10
MODEL_FILE     = "model.onnx"
SCALER_FILE    = "scaler.json"
THRESHOLD_FILE = "threshold.txt"
DATA_FILE      = "ais_preprocessed.csv"   # 상관행렬용 실제 데이터

STATUS_MAX_SOG = {0:30., 1:1., 2:5., 3:10., 4:10., 5:1., 6:5., 7:15., 8:15.}
DEFAULT_MAX_SOG = 30.0


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
    """MSE + 원본/재구성 배열 반환 — 오차 분해에 사용"""
    x   = np.array(seq_scaled, dtype=np.float32)[np.newaxis]  # (1,SEQ,F)
    out = session.run(None, {"x": x})[0]
    mse = float(np.mean((out - x) ** 2))
    return mse, x, out

def infer_mse(session, seq_scaled):
    mse, _, _ = infer(session, seq_scaled)
    return mse


# ── 실제 정상 시퀀스 로더 ─────────────────────────────────────────
def load_real_normal_seqs(mins, maxs, n_seqs=500, max_rows=200000) -> list:
    """
    ais_preprocessed.csv 에서 슬라이딩 윈도우로 실제 정상 시퀀스를 추출.
    파일이 없으면 None 반환 → 호출부에서 합성 시퀀스로 fallback.
    """
    import csv, os
    if not os.path.exists(DATA_FILE):
        return None

    from collections import defaultdict
    mmsi_rows = defaultdict(list)
    with open(DATA_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            try:
                record = [float(row[feat]) for feat in FEATURES]
                mmsi_rows[row["mmsi"]].append(record)
            except (ValueError, KeyError):
                continue

    # MMSI별 슬라이딩 윈도우로 시퀀스 생성
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

    # 요청 개수만큼 랜덤 샘플
    random.shuffle(all_seqs)
    sampled = all_seqs[:n_seqs]
    scaled  = [scale_seq(seq, mins, maxs) for seq in sampled]
    print(f"  실제 정상 시퀀스 로드: {len(scaled):,}개 (전체 풀: {len(all_seqs):,})")
    return scaled


SEQ_BREAK_DT = 600  # preprocess.py 와 동일


# ── 시퀀스 생성 헬퍼 ──────────────────────────────────────────────
def _sog_status_ratio(sog, status):
    mx = STATUS_MAX_SOG.get(int(status), DEFAULT_MAX_SOG)
    return sog / mx if mx > 0 else 0.0

def _cog_hdg_diff(cog, hdg):
    if hdg >= 511: return -1.0
    d = abs(cog - hdg)
    return 360 - d if d > 180 else d

def _build_derived(step_list):
    window = deque(maxlen=10)
    result = []
    prev_sog, prev_cog, prev_chd = step_list[0]["sog"], step_list[0]["cog"], 0.0
    for i, s in enumerate(step_list):
        sog, cog = s["sog"], s["cog"]
        hdg    = s.get("hdg", 511)
        status = s.get("status", 0)
        dt, dist, exp = s["dt"], s["dist_km"], s["expected_dist"]
        bcog   = s.get("bearing_cog_diff", -1.0)
        chd    = _cog_hdg_diff(cog, hdg)
        sog_ch = abs(sog - prev_sog) if i > 0 else 0.0
        cog_d  = abs(cog - prev_cog) if i > 0 else 0.0
        if cog_d > 180: cog_d = 360 - cog_d
        ssr = _sog_status_ratio(sog, status)
        der = dist / (exp + 1e-6)
        chd_change = abs(chd - prev_chd) if (i > 0 and chd >= 0 and prev_chd >= 0) else 0.0
        window.append(chd if chd >= 0 else 0.0)
        chd_std = statistics.stdev(window) if len(window) >= 2 else 0.0
        result.append([sog, cog, hdg if hdg < 511 else 0., status,
                        dt, dist, exp, bcog, chd,
                        sog_ch, cog_d, ssr, der, chd_change, chd_std])
        prev_sog, prev_cog = sog, cog
        prev_chd = chd if chd >= 0 else prev_chd
    return result


# ── 시나리오 ──────────────────────────────────────────────────────
def make_normal_seq():
    sog, cog = random.uniform(5,15), random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(10,30); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog+random.uniform(-5,5))%360,
                       "status":0,"dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
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
                       "dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
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
                       "status":1,"dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
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
                       "status":0,"dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
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
                       "status":0,"dt":dt,"dist_km":dist,"expected_dist":spd*dt/3600*1.852,
                       "bearing_cog_diff":0.})
        cog = random.uniform(0,360)
    return _build_derived(steps)

def make_fn_dt_jump_seq():
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(61,299); sog = random.choice([2.,18.,3.,20.])
        d  = random.uniform(.08,.20)*111.; cog = random.uniform(0,360)
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog),"status":0,
                       "dt":dt,"dist_km":d,"expected_dist":sog*dt/3600*1.852,"bearing_cog_diff":0.})
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
                       "dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
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
                       "dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
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
                       "dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
        if random.random()<.05: cog=(cog+random.uniform(-15,15))%360
    return _build_derived(steps)

def make_high_sog_status_seq():
    status = random.choice([1,5]); sog, cog = random.uniform(3,15), random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for _ in range(SEQ_LEN):
        dt = random.uniform(10,30); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int(cog+random.uniform(-5,5))%360,
                       "status":status,"dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
    return _build_derived(steps)

def make_oscillating_hdg_seq():
    sog, cog = random.uniform(5,12), random.uniform(0,360)
    lat, lon = 37., 126.
    steps = []
    for i in range(SEQ_LEN):
        dt = random.uniform(10,30); dist = sog*dt/3600*1.852
        lat += math.cos(math.radians(cog))*dist/111.
        lon += math.sin(math.radians(cog))*dist/111.
        steps.append({"sog":sog,"cog":cog,"hdg":int((cog+(90 if i%2==0 else -90))%360),
                       "status":0,"dt":dt,"dist_km":dist,"expected_dist":dist,"bearing_cog_diff":0.})
    return _build_derived(steps)

SCENARIO_MAKERS = [
    ("정상",          make_normal_seq,           False),
    ("COG/HDG불일치", make_cog_hdg_mismatch_seq, True),
    ("정박이동",      make_anchor_move_seq,       True),
    ("속도이상",      make_speed_spike_seq,       True),
    ("위치점프",      make_position_jump_seq,     True),
    ("FN1-dt점프",   make_fn_dt_jump_seq,        True),
    ("FN2-속도단계", make_fn_speed_ramp_seq,     True),
    ("FN3-COG경계", make_fn_cog_border_seq,     True),
    ("FN4-status",  make_fn_nav_status_seq,     True),
    ("고속정박",     make_high_sog_status_seq,   True),
    ("HDG진동",      make_oscillating_hdg_seq,   True),
]


# ══════════════════════════════════════════════════════════════════
# 분석 1: 탐지율/오탐율 테이블
# ══════════════════════════════════════════════════════════════════
def analysis_detection(session, mins, maxs, threshold, real_seqs=None, N=500):
    print("\n" + "="*60)
    print("  [분석 1] 탐지율 / 오탐율 테이블")
    print("="*60)

    all_errors = []
    for name, maker, is_anom in SCENARIO_MAKERS:
        if not is_anom and real_seqs is not None:
            # 정상: 실제 데이터 사용
            errs = np.array([infer_mse(session, seq) for seq in real_seqs[:N]])
        else:
            errs = np.array([infer_mse(session, scale_seq(maker(), mins, maxs))
                             for _ in range(N)])
        all_errors.append((name, errs))

    ne      = all_errors[0][1]
    fp_rate = np.sum(ne > threshold) / N * 100
    print(f"\n  임계값: {threshold:.6f}  |  오탐율: {fp_rate:.1f}%\n")

    col_w = 16
    sep   = "─" * (12 + col_w * len(all_errors))
    print(sep)
    print("".rjust(12) + "".join(rjust(n, col_w) for n, _ in all_errors))
    print(sep)
    for label, fn in [
        ("평균 MSE",  lambda e: f"{e.mean():.6f}"),
        ("95th %ile", lambda e: f"{np.percentile(e,95):.6f}"),
        ("탐지율",    lambda e: f"{np.sum(e>threshold)/N*100:.1f}%"),
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
        fp  = np.sum(ne>thr)/N*100
        row = rjust(f"{thr:.6f}",12) + rjust(f"{fp:.1f}%",9)
        row += "".join(rjust(f"{np.sum(e>thr)/N*100:.1f}%",16) for _,e in anom)
        print("  "+row)
    print("\n→ threshold.txt 를 위 값 중 적절한 것으로 교체하세요.")
    return all_errors


# ══════════════════════════════════════════════════════════════════
# 분석 2: 피처 간 상관행렬 (Pearson)
# ══════════════════════════════════════════════════════════════════
def analysis_correlation(max_rows=50000):
    print("\n" + "="*60)
    print("  [분석 2] 피처 간 상관행렬 (Pearson)")
    print("="*60)

    import csv, os
    if not os.path.exists(DATA_FILE):
        print(f"  ⚠ {DATA_FILE} 없음 — 상관행렬 건너뜀")
        return None

    data = {f: [] for f in FEATURES}
    with open(DATA_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= max_rows: break
            try:
                for feat in FEATURES:
                    data[feat].append(float(row[feat]))
            except (ValueError, KeyError):
                continue

    n = len(data[FEATURES[0]])
    print(f"  사용 행 수: {n:,}")

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
def analysis_reconstruction(session, mins, maxs, real_seqs=None, N=300):
    print("\n" + "="*60)
    print("  [분석 3] 시나리오별 재구성 오차 분해 (피처별 MSE)")
    print("="*60)

    scenario_feat_mse = {}
    for name, maker, is_anom in SCENARIO_MAKERS:
        feat_mse = np.zeros(len(FEATURES))
        if not is_anom and real_seqs is not None:
            seqs = real_seqs[:N]
            for seq in seqs:
                _, x, out = infer(session, seq)
                feat_mse += ((out - x) ** 2).mean(axis=(0, 1))
            feat_mse /= len(seqs)
        else:
            for _ in range(N):
                seq = scale_seq(maker(), mins, maxs)
                _, x, out = infer(session, seq)
                feat_mse += ((out - x) ** 2).mean(axis=(0, 1))
            feat_mse /= N
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
        mat   = np.array([scenario_feat_mse[n] for n in names])   # (S, F)
        mat_n = mat / (mat.max(axis=1, keepdims=True) + 1e-9)      # 시나리오 내 상대값

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
def analysis_permutation(session, mins, maxs, real_seqs=None, N=300, repeat=5):
    print("\n" + "="*60)
    print("  [분석 4] Permutation Importance")
    print("="*60)

    if real_seqs is not None:
        seqs_scaled = real_seqs[:N]
        print(f"  실제 정상 시퀀스 {len(seqs_scaled)}개 × 반복 {repeat}회\n")
    else:
        seqs_scaled = [scale_seq(make_normal_seq(), mins, maxs) for _ in range(N)]
        print(f"  합성 정상 시퀀스 {N}개 × 반복 {repeat}회 (CSV 없음)\n")

    arr = np.array(seqs_scaled, dtype=np.float32)  # (N, SEQ_LEN, F)

    def batch_mse(x_arr):
        return np.mean([infer_mse(session, x_arr[i].tolist()) for i in range(len(x_arr))])

    baseline = batch_mse(arr)
    print(f"  baseline MSE (정상): {baseline:.6f}")

    importance = np.zeros(len(FEATURES))
    for fi in range(len(FEATURES)):
        delta_sum = 0.
        for _ in range(repeat):
            shuffled = arr.copy()
            shuffled[:, :, fi] = arr[np.random.permutation(N), :, fi]
            delta_sum += batch_mse(shuffled) - baseline
        importance[fi] = delta_sum / repeat

    order = np.argsort(importance)[::-1]
    print(f"\n  {'순위':>4}  {'피처':<25}  {'ΔMSE':>10}  상대 중요도")
    print("  " + "─"*65)
    max_imp = max(importance[order[0]], 1e-9)
    for rank, fi in enumerate(order):
        bar = "█" * max(int(importance[fi] / max_imp * 25), 0)
        print(f"  {rank+1:>4}  {FEATURES[fi]:<25}  {importance[fi]:>+10.6f}  {bar}")

    if HAS_MPL:
        fig, ax = plt.subplots(figsize=(9, 6))
        colors = ["#e74c3c" if importance[fi] >= 0 else "#3498db" for fi in order]
        ax.barh([FEATURES[fi] for fi in order[::-1]],
                [importance[fi] for fi in order[::-1]],
                color=colors[::-1])
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_xlabel("ΔMSE (shuffled - baseline)")
        ax.set_title("Permutation Importance (normal sequences)")
        plt.tight_layout()
        plt.savefig("permutation_importance.png", dpi=150)
        plt.close()
        print("\n  → permutation_importance.png 저장")

    return importance


# ══════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corr",  action="store_true")
    parser.add_argument("--recon", action="store_true")
    parser.add_argument("--perm",  action="store_true")
    args = parser.parse_args()
    run_all = not any([args.corr, args.recon, args.perm])

    mins, maxs = load_scaler(SCALER_FILE)
    with open(THRESHOLD_FILE) as f:
        threshold = float(f.read())
    session = ort.InferenceSession(MODEL_FILE, providers=["CPUExecutionProvider"])

    if not HAS_MPL:
        print("  ⚠ matplotlib 없음 — 그래프 저장 건너뜀 (pip install matplotlib)")

    # 실제 정상 시퀀스 한 번만 로드 (없으면 None → 합성으로 fallback)
    print("\n실제 정상 시퀀스 로드 중...")
    real_seqs = load_real_normal_seqs(mins, maxs, n_seqs=500)
    if real_seqs is None:
        print(f"  ⚠ {DATA_FILE} 없음 — 합성 정상 시퀀스 사용")

    if run_all:
        analysis_detection(session, mins, maxs, threshold, real_seqs=real_seqs)
        analysis_correlation()
        analysis_reconstruction(session, mins, maxs, real_seqs=real_seqs)
        analysis_permutation(session, mins, maxs, real_seqs=real_seqs)
    else:
        if args.corr:  analysis_correlation()
        if args.recon: analysis_reconstruction(session, mins, maxs, real_seqs=real_seqs)
        if args.perm:  analysis_permutation(session, mins, maxs, real_seqs=real_seqs)

    print("\n완료!")
    if HAS_MPL:
        import os
        saved = [f for f in ["feature_correlation.png",
                              "reconstruction_error.png",
                              "permutation_importance.png"] if os.path.exists(f)]
        if saved:
            print("저장된 그래프:", ", ".join(saved))


if __name__ == "__main__":
    main()