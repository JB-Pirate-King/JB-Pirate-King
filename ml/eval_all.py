"""
전체 비지도 모델 탐지율 비교 (v3 - 난이도별 + 유형별 분석)
==========================================================
v2 -> v3 개선:
  7) 시간순 분할: --test-files로 학습에 안 쓴 테스트 데이터만 평가
  8) 난이도별 평가: hard(미세)/medium(중간)/easy(큰 변형) 3단계
  9) 공격 유형별 탐지율: 12종 각각의 Det@1%FPR 개별 보고
  10) 종합 표: 난이도 x 모델, 공격유형 x 모델 크로스테이블

v1 -> v2 개선:
  1) 학습/평가 정규화 통일 - 모델의 scaler_{name}.json 적용
  2) 공격 시퀀스를 실제 정상 분포 기반 perturbation으로 생성
  3) 단일 threshold 외에 ROC AUC, PR AUC, F1@best, Det@1%FPR, Det@5%FPR
  4) Bootstrap 95% 신뢰구간 (F1, AUC)
  5) 앙상블은 실제 z-score 결합 점수로 평가
  6) 정상/공격 클래스 불균형 시뮬레이션 옵션

사용:
    python eval_all.py --model-dir D:\\... --data-dir D:\\...
    python eval_all.py --severity all --bootstrap 100
    python eval_all.py --test-files D:\\...\\test_files.json
    python eval_all.py --bootstrap 0  # 빠르게 (CI 비활성)

출력:
    eval_summary.txt   (사람이 읽기 쉬운 표 + 난이도별/유형별)
    eval_metrics.json  (전체 메트릭, 기계 파싱용, by_severity 포함)
    best_ensemble.txt  (최적 앙상블 모델명)
"""
import sys, os
# cp949 콘솔에서 한글/특수문자 출력 오류 방지
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
_ML_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (r"D:\packages", r"C:\pl", _ML_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import csv
import glob
import json
import math
import random
import time
from itertools import combinations
from pathlib import Path

import numpy as np

try:
    import onnxruntime as ort
    HAS_ORT = True
except ImportError:
    HAS_ORT = False
    print("[경고] onnxruntime 없음 -- .pt 모델만 평가합니다")

# PT 직접 추론 (ONNX export 불가 모델용 — 예: TranAD)
try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

# ── 설정 ──────────────────────────────────────────────────────────
FEATURES = ["sog","cog","heading","status","dt","dist_km",
            "cog_hdg_diff","sog_change","cog_hdg_change",
            "speed_consistency","lat_speed","lon_speed"]
SEQ_LEN       = 10
N_FEAT        = len(FEATURES)
SEQ_BREAK_DT  = 600
SEED          = 42
random.seed(SEED)
np.random.seed(SEED)

ALL_MODELS = ["usad","tranad","conv1d","lstm","tcn","anomtrans",
              "dcdetect","iforest","deepsvdd","timesnet","dagmm"]


# ════════════════════════════════════════════════════════════════════
# 스케일러 (학습과 통일)
# ════════════════════════════════════════════════════════════════════

def load_scaler(scaler_path: str) -> dict:
    """모델의 학습 시 스케일러 로드. 없으면 None."""
    if not os.path.exists(scaler_path):
        return None
    try:
        with open(scaler_path, encoding="utf-8") as f:
            d = json.load(f)
        return {
            "features": d.get("features", FEATURES),
            "min": np.array(d["min"], dtype=np.float32),
            "max": np.array(d["max"], dtype=np.float32),
        }
    except Exception as e:
        print(f"  [스케일러 로드 실패] {scaler_path}: {e}")
        return None


def apply_scaler(arr: np.ndarray, scaler: dict) -> np.ndarray:
    """배열 (..., F) → min-max scaled (학습과 동일 방식)."""
    if scaler is None:
        # 폴백: identity (이미 정규화된 데이터로 간주)
        return np.clip(arr, 0.0, 1.0).astype(np.float32)
    mn = scaler["min"]
    mx = scaler["max"]
    denom = np.where(mx - mn < 1e-8, 1.0, mx - mn)
    scaled = (arr - mn) / denom
    return np.clip(scaled, 0.0, 1.0).astype(np.float32)


# ════════════════════════════════════════════════════════════════════
# 정상 시퀀스 로드 (raw — 스케일러는 평가 시점에 적용)
# ════════════════════════════════════════════════════════════════════

def load_normal_raw_sequences(data_dir: str, max_seqs: int = 4000,
                              test_files_json: str = None) -> np.ndarray:
    """전처리 CSV에서 정상 시퀀스(raw, 스케일링 전) 로드.

    각 모델 평가 시 그 모델의 scaler로 정규화하여 사용.

    test_files_json: test_files.json 경로. 지정하면 해당 파일에 기록된
      테스트 파일만 로드 (학습 데이터와 분리 → 데이터 누수 방지).
    """
    all_files = sorted(glob.glob(os.path.join(data_dir, "*_preprocessed.csv")))
    if not all_files:
        print("[주의] 전처리 CSV 없음 -- 합성 정상 시퀀스 사용 (스케일러 없음)")
        rng = np.random.default_rng(SEED)
        X = rng.uniform(0.0, 1.0, size=(max_seqs, SEQ_LEN, N_FEAT)).astype(np.float32)
        return X

    # test_files.json이 있으면 테스트 파일만 필터링
    if test_files_json and os.path.exists(test_files_json):
        try:
            with open(test_files_json, encoding="utf-8") as f:
                tf_info = json.load(f)
            test_basenames = set(tf_info.get("test_files", []))
            if test_basenames:
                files = [fp for fp in all_files
                         if os.path.basename(fp) in test_basenames]
                print(f"  [테스트 분할] 전체 {len(all_files)}개 중 테스트 {len(files)}개 파일만 사용")
                if not files:
                    print(f"  [경고] 테스트 파일 매칭 없음 — 전체 파일 사용")
                    files = all_files
            else:
                files = all_files
        except Exception as e:
            print(f"  [경고] test_files.json 로드 실패: {e} — 전체 파일 사용")
            files = all_files
    else:
        files = all_files

    # 파일 무작위 셔플하여 다양성 확보
    rng = random.Random(SEED)
    rng.shuffle(files)

    # pandas로 빠르게 로드 (없으면 csv 폴백)
    try:
        import pandas as pd
        _has_pandas = True
    except ImportError:
        _has_pandas = False

    seqs = []
    # 1초 단위 dt 컬럼 인덱스 (시퀀스 연속성 체크)
    dt_idx = FEATURES.index("dt")

    max_files = min(len(files), 30)   # 테스트 셋이 작을 수 있으므로 전부 시도
    for fpath in files[:max_files]:
        try:
            if _has_pandas:
                df = pd.read_csv(fpath, usecols=FEATURES, low_memory=False,
                                 on_bad_lines="skip")
                df = df.dropna()
                arr = df.values.astype(np.float32)
            else:
                rows = []
                with open(fpath, encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            rows.append([float(row[col]) for col in FEATURES])
                        except (ValueError, KeyError):
                            continue
                arr = np.array(rows, dtype=np.float32)

            if len(arr) < SEQ_LEN + 1:
                continue

            # 시퀀스 연속성 체크: dt가 SEQ_BREAK_DT(600초) 이상이면 끊기
            for i in range(len(arr) - SEQ_LEN):
                seg = arr[i: i + SEQ_LEN]
                # 시퀀스 내 큰 시간 점프 없음 (raw dt 사용)
                if np.any(seg[1:, dt_idx] >= SEQ_BREAK_DT):
                    continue
                seqs.append(seg)
                if len(seqs) >= max_seqs:
                    break
        except Exception as e:
            print(f"  [정상 로드 스킵] {os.path.basename(fpath)}: {e}")
            continue
        if len(seqs) >= max_seqs:
            break

    if not seqs:
        print("[주의] 정상 시퀀스 추출 실패 -- 합성 데이터")
        rng_np = np.random.default_rng(SEED)
        return rng_np.uniform(0.0, 1.0, size=(max_seqs, SEQ_LEN, N_FEAT)).astype(np.float32)

    rng.shuffle(seqs)
    return np.stack(seqs[:max_seqs]).astype(np.float32)


# ════════════════════════════════════════════════════════════════════
# 공격 시퀀스 생성 (정상 분포 기반)
# ════════════════════════════════════════════════════════════════════

def make_attacks_from_normal(X_normal_scaled: np.ndarray, n_per_type: int,
                              seed: int = SEED, severity: str = "medium") -> tuple:
    """
    실제 정상(스케일드) 시퀀스를 베이스로 perturbation 적용 → 공격 생성.

    severity: "hard" (미세 — 현실적), "medium" (중간), "easy" (큰 변형 — 쉬움)
    """
    rng = np.random.default_rng(seed)
    n_norm = len(X_normal_scaled)
    if n_norm == 0:
        return np.zeros((0, SEQ_LEN, N_FEAT), dtype=np.float32), np.zeros(0, dtype=np.int32)

    # 난이도별 perturbation 범위
    SEVERITY_SCALE = {
        "hard":   {"lo": 0.05, "hi": 0.20, "lo2": 0.03, "hi2": 0.15},  # 미세 — 실제 이상 수준
        "medium": {"lo": 0.20, "hi": 0.45, "lo2": 0.15, "hi2": 0.35},  # 중간
        "easy":   {"lo": 0.45, "hi": 0.85, "lo2": 0.35, "hi2": 0.70},  # 큰 변형
    }
    S = SEVERITY_SCALE.get(severity, SEVERITY_SCALE["medium"])

    seqs, labels, types = [], [], []

    def _base():
        idx = rng.integers(0, n_norm)
        return X_normal_scaled[idx].copy()

    # 공격 1: 속도 스파이크
    for _ in range(n_per_type):
        s = _base()
        t = int(rng.integers(2, SEQ_LEN - 1))
        s[t, 0] = min(1.0, s[t, 0] + rng.uniform(S["lo"], S["hi"]))
        s[t, 7] = min(1.0, s[t, 7] + rng.uniform(S["lo"], S["hi"]))
        seqs.append(s); labels.append(1); types.append("speed_spike")

    # 공격 2: 위치 점프
    for _ in range(n_per_type):
        s = _base()
        t = int(rng.integers(3, SEQ_LEN - 1))
        s[t, 5]  = min(1.0, s[t, 5]  + rng.uniform(S["lo"], S["hi"]))
        s[t, 10] = min(1.0, s[t, 10] + rng.uniform(S["lo2"], S["hi2"]))
        s[t, 11] = min(1.0, s[t, 11] + rng.uniform(S["lo2"], S["hi2"]))
        seqs.append(s); labels.append(1); types.append("position_jump")

    # 공격 3: 방향 불일치 지속
    for _ in range(n_per_type):
        s = _base()
        s[:, 6] = np.minimum(1.0, s[:, 6] + rng.uniform(S["lo2"], S["hi2"], size=SEQ_LEN))
        s[:, 8] = np.minimum(1.0, s[:, 8] + rng.uniform(S["lo2"], S["hi"], size=SEQ_LEN))
        seqs.append(s); labels.append(1); types.append("course_mismatch")

    # 공격 4: 신호 위조
    for _ in range(n_per_type):
        s = _base()
        blend = min(0.8, S["lo"] + 0.2)  # hard=0.25, medium=0.40, easy=0.65
        noise = rng.uniform(0.3, 0.7, size=(SEQ_LEN, N_FEAT)).astype(np.float32)
        s = np.clip((1 - blend) * s + blend * noise, 0.0, 1.0).astype(np.float32)
        seqs.append(s); labels.append(1); types.append("signal_forge")

    # 공격 5: 천천히 표류
    for _ in range(n_per_type):
        s = _base()
        drift_feat = int(rng.choice([0, 5, 6]))
        drift = np.linspace(0, rng.uniform(S["lo2"], S["hi2"]), SEQ_LEN).astype(np.float32)
        s[:, drift_feat] = np.clip(s[:, drift_feat] + drift, 0.0, 1.0)
        seqs.append(s); labels.append(1); types.append("slow_drift")

    # 공격 6: GPS 스푸핑 정적
    for _ in range(n_per_type):
        s = _base()
        s[:, 0]  = np.minimum(1.0, s[:, 0]  + rng.uniform(S["lo2"], S["hi2"], size=SEQ_LEN))
        suppress = max(0.01, 0.10 - S["lo"] * 0.1)  # hard=0.095, medium=0.08, easy=0.055
        s[:, 5]  = s[:, 5]  * rng.uniform(0.0, suppress, size=SEQ_LEN)
        s[:, 10] = s[:, 10] * rng.uniform(0.0, suppress, size=SEQ_LEN)
        s[:, 11] = s[:, 11] * rng.uniform(0.0, suppress, size=SEQ_LEN)
        s[:, 9]  = s[:, 9]  * rng.uniform(0.0, suppress * 2, size=SEQ_LEN)
        seqs.append(s); labels.append(1); types.append("gps_spoofing_static")

    # 공격 7: 불가능한 급선회
    for _ in range(n_per_type):
        s = _base()
        t = int(rng.integers(2, SEQ_LEN - 2))
        s[:, 0] = np.minimum(1.0, s[:, 0] + rng.uniform(S["lo2"], S["hi2"], size=SEQ_LEN))
        s[t, 8] = min(1.0, s[t, 8] + rng.uniform(S["lo"], S["hi"]))
        s[t, 6] = min(1.0, s[t, 6] + rng.uniform(S["lo2"], S["hi2"]))
        seqs.append(s); labels.append(1); types.append("impossible_turn")

    # 공격 8: 암흑 후 재출현
    for _ in range(n_per_type):
        s = _base()
        t = int(rng.integers(2, SEQ_LEN - 2))
        s[t, 4]  = min(1.0, s[t, 4]  + rng.uniform(S["lo"], S["hi"]))
        s[t, 5]  = min(1.0, s[t, 5]  + rng.uniform(S["lo2"], S["hi"]))
        s[t, 10] = min(1.0, s[t, 10] + rng.uniform(S["lo2"], S["hi2"]))
        s[t, 11] = min(1.0, s[t, 11] + rng.uniform(S["lo2"], S["hi2"]))
        seqs.append(s); labels.append(1); types.append("dark_reappear")

    # 공격 9: 속도 불일치
    for _ in range(n_per_type):
        s = _base()
        s[:, 0] = np.minimum(1.0, s[:, 0] + rng.uniform(S["lo2"], S["hi"], size=SEQ_LEN))
        suppress = max(0.01, 0.15 - S["lo"] * 0.1)
        s[:, 5] = s[:, 5] * rng.uniform(0.0, suppress, size=SEQ_LEN)
        s[:, 9] = s[:, 9] * rng.uniform(0.0, suppress * 1.5, size=SEQ_LEN)
        seqs.append(s); labels.append(1); types.append("speed_inconsistency")

    # 공격 10: 배회
    for _ in range(n_per_type):
        s = _base()
        suppress = max(0.01, 0.10 - S["lo"] * 0.05)
        s[:, 5] = s[:, 5] * rng.uniform(0.0, suppress, size=SEQ_LEN)
        s[:, 8] = np.minimum(1.0, s[:, 8] + rng.uniform(S["lo2"], S["hi2"], size=SEQ_LEN))
        s[:, 0] = np.clip(s[:, 0], 0.02, 0.15 + S["lo"])
        seqs.append(s); labels.append(1); types.append("loitering")

    # 공격 11: 방위 센서 동결
    for _ in range(n_per_type):
        s = _base()
        s[:, 2] = s[0, 2]
        drift = np.linspace(0, rng.uniform(S["lo2"], S["hi2"]), SEQ_LEN).astype(np.float32)
        s[:, 6] = np.minimum(1.0, s[:, 6] + drift)
        s[:, 8] = s[:, 8] * max(0.01, 0.10 - S["lo"] * 0.1)
        seqs.append(s); labels.append(1); types.append("heading_freeze")

    # 공격 12: 상태 이상
    for _ in range(n_per_type):
        s = _base()
        anchor_val = float(rng.choice(np.array([1/15.0, 5/15.0])))
        s[:, 3] = anchor_val
        s[:, 0] = np.minimum(1.0, s[:, 0] + rng.uniform(S["lo2"], S["hi2"], size=SEQ_LEN))
        s[:, 5] = np.minimum(1.0, s[:, 5] + rng.uniform(S["lo2"], S["hi2"], size=SEQ_LEN))
        seqs.append(s); labels.append(1); types.append("status_anomaly")

    X = np.stack(seqs).astype(np.float32)
    y = np.array(labels, dtype=np.int32)
    return X, y, types


# ════════════════════════════════════════════════════════════════════
# ONNX 추론
# ════════════════════════════════════════════════════════════════════

def run_onnx(onnx_path: str, X: np.ndarray) -> np.ndarray:
    """ONNX 추론 → MSE 점수 배열."""
    if not HAS_ORT:
        raise RuntimeError("onnxruntime 없음")
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    inp_name = sess.get_inputs()[0].name
    inp_shape = sess.get_inputs()[0].shape
    fixed_batch = isinstance(inp_shape[0], int) and inp_shape[0] == 1
    scores = []
    batch = 1 if fixed_batch else 256
    for i in range(0, len(X), batch):
        xb = X[i:i+batch]
        try:
            out = sess.run(None, {inp_name: xb})[0]
        except Exception:
            out = np.concatenate([
                sess.run(None, {inp_name: X[j:j+1]})[0]
                for j in range(i, min(i+batch, len(X)))
            ])
        mse = ((out - xb) ** 2).mean(axis=(1, 2))
        scores.extend(mse.tolist())
    return np.array(scores, dtype=np.float32)


def run_pt_model(pt_path: str, model_name: str, X: np.ndarray) -> np.ndarray:
    """PT 파일 직접 로드해서 추론 (ONNX export 불가 모델용).
    train_benchmark.py의 모델 클래스를 동적으로 import."""
    if not HAS_TORCH:
        raise RuntimeError("torch 없음")
    try:
        import train_benchmark as tb
        import torch
        # 모델 클래스 & 크기 매핑
        model_map = {
            "tranad":    lambda: tb.TranAD(tb.SEQ_LEN, tb.N_FEAT, d_model=64, nhead=4,
                                           num_encoder_layers=1, num_decoder_layers=1,
                                           dim_feedforward=128),
        }
        if model_name not in model_map:
            raise ValueError(f"PT 추론 미지원 모델: {model_name}")
        model = model_map[model_name]()
        state = torch.load(pt_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()
        # 배치 추론
        X_t = torch.tensor(X, dtype=torch.float32)
        scores = []
        batch = 256
        with torch.no_grad():
            for i in range(0, len(X_t), batch):
                xb = X_t[i:i+batch]
                out = model(xb)
                mse = ((out - xb) ** 2).mean(dim=(1, 2))
                scores.extend(mse.tolist())
        return np.array(scores, dtype=np.float32)
    except Exception as e:
        raise RuntimeError(f"PT 추론 실패 ({model_name}): {e}")


# ════════════════════════════════════════════════════════════════════
# 메트릭 계산
# ════════════════════════════════════════════════════════════════════

def compute_metrics(scores_normal: np.ndarray, scores_attack: np.ndarray,
                    threshold: float, bootstrap: int = 100) -> dict:
    """
    종합 메트릭:
      - tp_rate, fp_rate, f1, precision, recall (저장된 threshold 기준)
      - f1_best_thr, best_threshold (F1 최대화 임계값)
      - roc_auc, pr_auc (임계값 독립)
      - det_at_1pct_fpr, det_at_5pct_fpr (실용 동작점)
      - f1_ci95: Bootstrap 95% 신뢰구간 [lo, hi]
    """
    scores_normal = np.asarray(scores_normal, dtype=np.float32)
    scores_attack = np.asarray(scores_attack, dtype=np.float32)
    n_normal = len(scores_normal)
    n_attack = len(scores_attack)

    # 저장된 threshold 기준 지표
    fp = int((scores_normal > threshold).sum())
    tp = int((scores_attack > threshold).sum())
    fp_rate = fp / max(n_normal, 1) * 100
    tp_rate = tp / max(n_attack, 1) * 100
    precision = tp / max(tp + fp, 1)
    recall    = tp / max(n_attack, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-8)

    # 합쳐서 ROC/PR 계산용
    y = np.concatenate([np.zeros(n_normal), np.ones(n_attack)])
    s = np.concatenate([scores_normal, scores_attack])

    # ROC AUC (수동 계산: 정렬 후 trapezoidal)
    roc_auc = _roc_auc(y, s)
    pr_auc  = _pr_auc(y, s)

    # F1 @ best threshold (정렬된 점수에서 검색)
    f1_best, best_thr = _best_f1_threshold(scores_normal, scores_attack)

    # Detection rate @ specific FPR points
    det_1pct = _detection_at_fpr(scores_normal, scores_attack, target_fpr=0.01)
    det_5pct = _detection_at_fpr(scores_normal, scores_attack, target_fpr=0.05)

    # Bootstrap 95% CI for F1
    ci_lo, ci_hi = (f1, f1)
    if bootstrap > 0 and n_normal > 10 and n_attack > 10:
        ci_lo, ci_hi = _bootstrap_f1_ci(scores_normal, scores_attack, threshold,
                                          n_iter=bootstrap)

    return {
        "tp_rate": tp_rate,
        "fp_rate": fp_rate,
        "f1": f1,
        "precision": precision * 100,
        "recall": recall * 100,
        "threshold": threshold,
        "f1_best_thr": f1_best,
        "best_threshold": best_thr,
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "det_at_1pct_fpr": det_1pct * 100,   # %
        "det_at_5pct_fpr": det_5pct * 100,
        "f1_ci95_lo": ci_lo,
        "f1_ci95_hi": ci_hi,
        "n_normal": n_normal,
        "n_attack": n_attack,
    }


def _roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Mann-Whitney U 통계량으로 AUC 계산 (sklearn 없이)."""
    if len(y_true) == 0 or (y_true.sum() == 0) or (y_true.sum() == len(y_true)):
        return 0.5
    # 점수 정렬 + 순위 매기기
    order = np.argsort(y_score, kind="mergesort")
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, len(y_score) + 1)
    # 동점 처리 (평균 순위)
    unique_vals, inverse = np.unique(y_score, return_inverse=True)
    if len(unique_vals) != len(y_score):
        avg_ranks = np.zeros(len(unique_vals))
        for i, v in enumerate(unique_vals):
            mask = (y_score == v)
            avg_ranks[i] = ranks[mask].mean()
        ranks = avg_ranks[inverse]
    n_pos = float(y_true.sum())
    n_neg = float(len(y_true) - n_pos)
    rank_sum_pos = ranks[y_true == 1].sum()
    auc = (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return float(max(0.0, min(1.0, auc)))


def _pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Average precision (PR AUC) 계산."""
    if y_true.sum() == 0:
        return 0.0
    order = np.argsort(-y_score, kind="mergesort")
    y_sorted = y_true[order]
    tp_cum = np.cumsum(y_sorted)
    fp_cum = np.cumsum(1 - y_sorted)
    precision = tp_cum / np.maximum(tp_cum + fp_cum, 1)
    recall    = tp_cum / max(y_true.sum(), 1)
    # AP = sum_k (R_k - R_{k-1}) * P_k
    recall_diff = np.diff(np.concatenate([[0.0], recall]))
    ap = float(np.sum(recall_diff * precision))
    return max(0.0, min(1.0, ap))


def _best_f1_threshold(s_norm: np.ndarray, s_atk: np.ndarray) -> tuple:
    """F1 최대화하는 임계값 탐색."""
    if len(s_norm) == 0 or len(s_atk) == 0:
        return 0.0, 0.0
    candidates = np.unique(np.concatenate([s_norm, s_atk]))
    # 너무 많으면 다운샘플
    if len(candidates) > 500:
        candidates = np.linspace(candidates.min(), candidates.max(), 500)

    best_f1, best_thr = 0.0, float(candidates[0])
    n_atk = float(len(s_atk))
    for thr in candidates:
        tp = float((s_atk  > thr).sum())
        fp = float((s_norm > thr).sum())
        if tp + fp == 0:
            continue
        precision = tp / (tp + fp)
        recall    = tp / n_atk
        if precision + recall == 0:
            continue
        f1 = 2 * precision * recall / (precision + recall)
        if f1 > best_f1:
            best_f1, best_thr = f1, float(thr)
    return best_f1, best_thr


def _detection_at_fpr(s_norm: np.ndarray, s_atk: np.ndarray,
                       target_fpr: float) -> float:
    """정상 점수에서 target_fpr 분위수를 임계값으로 잡고 → 공격 탐지율."""
    if len(s_norm) == 0 or len(s_atk) == 0:
        return 0.0
    # FPR=target → normal 점수의 (1 - target_fpr) 분위수가 임계값
    thr = float(np.quantile(s_norm, 1.0 - target_fpr))
    det = float((s_atk > thr).sum()) / len(s_atk)
    return det


def _bootstrap_f1_ci(s_norm: np.ndarray, s_atk: np.ndarray, threshold: float,
                     n_iter: int = 100, alpha: float = 0.05) -> tuple:
    """F1의 Bootstrap 95% 신뢰구간."""
    rng = np.random.default_rng(SEED)
    n_n, n_a = len(s_norm), len(s_atk)
    f1s = []
    for _ in range(n_iter):
        idx_n = rng.integers(0, n_n, size=n_n)
        idx_a = rng.integers(0, n_a, size=n_a)
        sn, sa = s_norm[idx_n], s_atk[idx_a]
        fp = (sn > threshold).sum()
        tp = (sa > threshold).sum()
        if tp + fp == 0:
            f1s.append(0.0); continue
        precision = tp / (tp + fp)
        recall    = tp / max(n_a, 1)
        if precision + recall == 0:
            f1s.append(0.0); continue
        f1s.append(2 * precision * recall / (precision + recall))
    f1s = np.array(f1s, dtype=np.float64)
    lo = float(np.percentile(f1s, alpha/2 * 100))
    hi = float(np.percentile(f1s, (1 - alpha/2) * 100))
    return lo, hi


# ════════════════════════════════════════════════════════════════════
# 단일 모델 평가
# ════════════════════════════════════════════════════════════════════

def evaluate_model(name: str, model_dir: str, X_normal_raw: np.ndarray,
                   bootstrap: int = 100, attacks_per_type: int = 200,
                   severity: str = "medium") -> dict:
    """단일 모델 평가. raw 정상 시퀀스를 받아서 모델별 scaler 적용.

    severity: "hard", "medium", "easy" — 공격 난이도
    반환값에 per-type 탐지율 포함.
    """
    onnx_path   = os.path.join(model_dir, f"model_{name}.onnx")
    pt_path     = os.path.join(model_dir, f"model_{name}.pt")
    thr_path    = os.path.join(model_dir, f"threshold_{name}.txt")
    scaler_path = os.path.join(model_dir, f"scaler_{name}.json")

    if not (os.path.exists(onnx_path) or os.path.exists(pt_path)):
        return None

    # 스케일러 (학습 시와 동일하게 적용 — CORE FIX)
    scaler = load_scaler(scaler_path)
    if scaler is None:
        print(f"  [{name}] 스케일러 없음 — identity 사용 (정확도 ↓)")

    # 정상 데이터를 모델 스케일러로 정규화
    X_normal = apply_scaler(X_normal_raw, scaler)

    # 공격 데이터: 스케일링된 정상에서 perturbation (난이도 적용)
    X_attack, y_attack, attack_types = make_attacks_from_normal(
        X_normal, attacks_per_type, severity=severity)

    # 임계값 로드
    threshold = 0.01
    if os.path.exists(thr_path):
        try:
            with open(thr_path) as f:
                threshold = float(f.read().strip())
        except Exception:
            pass

    try:
        if os.path.exists(onnx_path) and HAS_ORT:
            s_norm = run_onnx(onnx_path, X_normal)
            s_atk  = run_onnx(onnx_path, X_attack)
        elif os.path.exists(pt_path) and HAS_TORCH:
            # PT 직접 추론 (ONNX export 불가 모델 — 예: TranAD)
            print(f" [PT직접추론]", end="", flush=True)
            s_norm = run_pt_model(pt_path, name, X_normal)
            s_atk  = run_pt_model(pt_path, name, X_attack)
        else:
            print(f"  [{name}] ONNX/PT 모두 없음 — 스킵")
            return None
    except Exception as e:
        print(f"  [{name}] 추론 오류: {e}")
        return None

    metrics = compute_metrics(s_norm, s_atk, threshold, bootstrap=bootstrap)
    metrics["name"] = name
    metrics["severity"] = severity
    metrics["scaler_loaded"] = scaler is not None

    # ── 공격 유형별 탐지율 (Det@1%FPR 기준 threshold) ──
    thr_1pct = float(np.quantile(s_norm, 0.99))  # FPR=1% threshold
    type_det = {}
    attack_types_arr = np.array(attack_types)
    unique_types = sorted(set(attack_types))
    for atype in unique_types:
        mask = (attack_types_arr == atype)
        s_this = s_atk[mask]
        if len(s_this) == 0:
            continue
        det_rate = float((s_this > thr_1pct).sum()) / len(s_this)
        type_det[atype] = round(det_rate * 100, 1)
    metrics["per_type_det"] = type_det

    # 앙상블용 점수 (정규화하여 결합 시 사용)
    metrics["_scores_normal"] = s_norm
    metrics["_scores_attack"] = s_atk
    return metrics


# ════════════════════════════════════════════════════════════════════
# 앙상블 (z-score 점수 결합 → 실제 평가)
# ════════════════════════════════════════════════════════════════════

def _zscore(x: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """ref의 mean/std로 표준화."""
    mu, sd = float(np.mean(ref)), float(np.std(ref))
    if sd < 1e-12:
        return np.zeros_like(x, dtype=np.float32)
    return ((x - mu) / sd).astype(np.float32)


def evaluate_ensemble(combo_results: list, bootstrap: int = 100) -> dict:
    """
    여러 모델 점수를 z-score 정규화 후 평균 → 앙상블 점수.
    표준 threshold = ensemble_normal의 (1 - 0.05) 분위수 (5% FPR 기준)
    """
    if not combo_results:
        return None

    # 각 모델 점수 z-score (정상 점수 기준)
    norm_z = []
    atk_z  = []
    for m in combo_results:
        sn = m["_scores_normal"]
        sa = m["_scores_attack"]
        norm_z.append(_zscore(sn, sn))   # 자기 자신 기준
        atk_z .append(_zscore(sa, sn))   # 정상 기준으로 attack 정규화

    norm_z = np.stack(norm_z).mean(axis=0)   # (n_normal,)
    atk_z  = np.stack(atk_z ).mean(axis=0)   # (n_attack,)

    # 표준 운영 임계값: 5% FPR
    threshold = float(np.quantile(norm_z, 0.95))
    return compute_metrics(norm_z, atk_z, threshold, bootstrap=bootstrap)


def find_best_ensemble(results: list, max_k: int = 5, bootstrap: int = 50) -> tuple:
    """실제 z-score 결합 점수로 앙상블 평가 -> 최적 조합.

    모든 모델 조합을 전수 탐색 (최대 max_k개 조합).
    정렬 기준: Det@1%FPR (오탐율 1% 운영점 탐지율).
    """
    valid = [r for r in results if r]
    if not valid:
        return [], 0.0, [], {}

    # 전수 탐색: 모든 모델 포함 (상위 N개 제한 없음)
    all_evaluated = []
    total_combos = 0

    # 단일 모델도 포함
    for r in valid:
        all_evaluated.append({
            "names":            [r["name"]],
            "f1_best_thr":      r["f1_best_thr"],
            "roc_auc":          r["roc_auc"],
            "det_at_1pct_fpr":  r["det_at_1pct_fpr"],
            "det_at_5pct_fpr":  r["det_at_5pct_fpr"],
            "pr_auc":           r.get("pr_auc", 0),
        })
        total_combos += 1

    for k in range(2, min(max_k + 1, len(valid) + 1)):
        for combo in combinations(valid, k):
            m = evaluate_ensemble(list(combo), bootstrap=bootstrap)
            if m is None:
                continue
            names = [c["name"] for c in combo]
            all_evaluated.append({
                "names":            names,
                "f1_best_thr":      m["f1_best_thr"],
                "roc_auc":          m["roc_auc"],
                "det_at_1pct_fpr":  m["det_at_1pct_fpr"],
                "det_at_5pct_fpr":  m["det_at_5pct_fpr"],
                "pr_auc":           m.get("pr_auc", 0),
            })
            total_combos += 1

    print(f"  총 {total_combos}개 조합 평가 완료")

    # Det@1%FPR 기준 정렬 (오탐율 1% 운영점 → 실전 가장 중요)
    by_det1 = sorted(all_evaluated, key=lambda x: -x["det_at_1pct_fpr"])
    # F1best 기준 정렬 (참고용)
    by_f1   = sorted(all_evaluated, key=lambda x: -x["f1_best_thr"])

    best = by_det1[0]
    best_combo   = best["names"]
    best_det1    = best["det_at_1pct_fpr"]
    best_metrics = best

    return best_combo, best_det1, by_det1[:10], best_metrics, by_f1[:10]


# ════════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════════

def _print_per_type_table(all_severity_results: dict, model_names: list):
    """공격 유형 × 모델 × 난이도별 탐지율 표 출력."""
    # 모든 공격 유형 수집
    all_types = set()
    for sev_data in all_severity_results.values():
        for r in sev_data:
            all_types.update(r.get("per_type_det", {}).keys())
    all_types = sorted(all_types)
    if not all_types:
        return ""

    lines = []
    lines.append("\n" + "=" * 100)
    lines.append("  공격 유형별 탐지율 (Det@1%FPR)")
    lines.append("=" * 100)

    for sev in ["hard", "medium", "easy"]:
        if sev not in all_severity_results:
            continue
        results = all_severity_results[sev]
        sev_label = {"hard": "Hard (미세)", "medium": "Medium (중간)", "easy": "Easy (큰 변형)"}
        lines.append(f"\n  ── {sev_label.get(sev, sev)} ──")

        # 헤더
        hdr = f"  {'유형':<22}"
        for r in results:
            hdr += f" {r['name']:>8}"
        lines.append(hdr)
        lines.append("  " + "-" * (22 + 9 * len(results)))

        # 각 공격 유형
        for atype in all_types:
            row = f"  {atype:<22}"
            for r in results:
                det = r.get("per_type_det", {}).get(atype, -1)
                if det >= 0:
                    row += f" {det:>7.1f}%"
                else:
                    row += f"     {'—':>3}"
            lines.append(row)

        # 평균 행
        row = f"  {'평균':<22}"
        for r in results:
            dets = [v for v in r.get("per_type_det", {}).values() if v >= 0]
            avg = sum(dets) / max(len(dets), 1)
            row += f" {avg:>7.1f}%"
        lines.append(row)

    out = "\n".join(lines) + "\n"
    print(out)
    return out


def main():
    parser = argparse.ArgumentParser(description="비지도 모델 종합 평가 (v3 - 난이도별 + 유형별)")
    parser.add_argument("--model-dir", default=r"D:\JB-Pirate-King-ML-Results")
    parser.add_argument("--data-dir",  default=r"D:\JB-Pirate-King-AIS\preprocessed")
    parser.add_argument("--test-files", default=None,
                        help="test_files.json 경로 (학습에서 제외된 테스트 데이터만 평가)")
    parser.add_argument("--n-normal",  type=int, default=4000,
                        help="평가용 정상 시퀀스 수 (기본 4000, 정확도 ↑)")
    parser.add_argument("--attacks-per-type", type=int, default=200,
                        help="공격 유형별 시퀀스 수 (12 유형 × N)")
    parser.add_argument("--bootstrap", type=int, default=100,
                        help="Bootstrap CI 반복 수 (0=비활성)")
    parser.add_argument("--severity", default="all",
                        choices=["hard", "medium", "easy", "all"],
                        help="공격 난이도 (all=3단계 전부)")
    args = parser.parse_args()

    # --test-files 자동 탐색 (명시 안 했으면 model-dir에서 찾기)
    test_files_json = args.test_files
    if test_files_json is None:
        auto_path = os.path.join(args.model_dir, "test_files.json")
        if os.path.exists(auto_path):
            test_files_json = auto_path
            print(f"[자동] 테스트 파일 목록 발견: {auto_path}")

    severities = ["hard", "medium", "easy"] if args.severity == "all" else [args.severity]

    print("\n" + "=" * 72)
    print("  JB-Pirate-King  비지도 모델 종합 평가 (v3)")
    print("=" * 72)
    print(f"  모델 디렉터리:    {args.model_dir}")
    print(f"  데이터 디렉터리:  {args.data_dir}")
    print(f"  테스트 분할:      {test_files_json or '없음 (전체 사용)'}")
    print(f"  정상 시퀀스:      {args.n_normal}개")
    print(f"  공격 시나리오:    12종 × {args.attacks_per_type}개 × {len(severities)} 난이도")
    print(f"  난이도:           {', '.join(severities)}")
    print(f"  Bootstrap CI:     {args.bootstrap}회")
    print()

    # 정상 데이터 raw 로드 (스케일링은 모델별로)
    t0 = time.time()
    print("[데이터] raw 정상 시퀀스 로드 중...")
    X_normal_raw = load_normal_raw_sequences(args.data_dir, args.n_normal,
                                              test_files_json=test_files_json)
    print(f"  정상: {len(X_normal_raw)}개  ({time.time()-t0:.1f}s)")

    # 사용 가능한 모델 자동 탐지
    available = [m for m in ALL_MODELS
                 if os.path.exists(os.path.join(args.model_dir, f"model_{m}.onnx"))
                 or os.path.exists(os.path.join(args.model_dir, f"model_{m}.pt"))]
    print(f"\n[평가] {len(available)}개 모델: {available}")

    # ── 난이도별 평가 루프 ──
    all_severity_results = {}   # {severity: [results...]}
    primary_results = None       # medium 난이도 결과 (앙상블/요약에 사용)

    for sev in severities:
        sev_label = {"hard": "Hard(미세)", "medium": "Medium(중간)", "easy": "Easy(큰변형)"}
        print(f"\n{'─'*60}")
        print(f"[난이도: {sev_label.get(sev, sev)}]")
        print(f"{'─'*60}")

        results = []
        for name in available:
            print(f"  {name}...", end="", flush=True)
            r = evaluate_model(name, args.model_dir, X_normal_raw,
                                bootstrap=args.bootstrap,
                                attacks_per_type=args.attacks_per_type,
                                severity=sev)
            if r:
                results.append(r)
                print(f"  F1={r['f1']:.3f} (best={r['f1_best_thr']:.3f}) "
                      f"AUC={r['roc_auc']:.3f}  Det@1%FPR={r['det_at_1pct_fpr']:.1f}%")
            else:
                print("  스킵")

        if results:
            results_sorted = sorted(results, key=lambda x: -x["f1_best_thr"])
            all_severity_results[sev] = results_sorted
            if sev == "medium":
                primary_results = results_sorted
            elif primary_results is None:
                primary_results = results_sorted

    if not primary_results:
        print("\n[결과 없음] 평가할 모델이 없습니다.")
        return

    # medium이 없으면 첫 번째 난이도 결과 사용
    if primary_results is None:
        primary_results = list(all_severity_results.values())[0]

    # ── 난이도별 종합 표 ──
    print("\n" + "=" * 100)
    print("  난이도별 종합 결과 (Det@1%FPR)")
    print("=" * 100)
    hdr = f"  {'모델':<12}"
    for sev in severities:
        hdr += f" {sev:>10}"
    hdr += f"  {'F1best':>8} {'AUC':>7} {'CI95':>16}"
    print(hdr)
    print("  " + "-" * (12 + 11 * len(severities) + 35))

    # 모델별 각 난이도 Det@1%FPR
    model_names = [r["name"] for r in primary_results]
    for mname in model_names:
        row = f"  {mname:<12}"
        for sev in severities:
            sev_results = all_severity_results.get(sev, [])
            mr = next((r for r in sev_results if r["name"] == mname), None)
            if mr:
                row += f" {mr['det_at_1pct_fpr']:>9.1f}%"
            else:
                row += f"       {'—':>3}"
        # F1best와 AUC는 medium (또는 primary)에서
        pr = next((r for r in primary_results if r["name"] == mname), None)
        if pr:
            ci_str = f"[{pr['f1_ci95_lo']:.2f}, {pr['f1_ci95_hi']:.2f}]"
            row += f"  {pr['f1_best_thr']:>8.3f} {pr['roc_auc']:>7.3f} {ci_str:>16}"
        print(row)
    print("=" * 100)

    # ── 공격 유형별 탐지율 표 ──
    type_table_str = _print_per_type_table(all_severity_results, model_names)

    # ── 기존 medium 요약표 (호환) ──
    print("\n" + "=" * 90)
    print(f"  {'모델':<10} {'F1':>7} {'F1best':>8} {'AUC':>7} "
          f"{'PR-AUC':>7} {'Det@1%':>8} {'Det@5%':>8} {'CI95':>16}")
    print("  " + "-" * 88)
    for r in primary_results:
        ci_str = f"[{r['f1_ci95_lo']:.2f}, {r['f1_ci95_hi']:.2f}]"
        print(f"  {r['name']:<10} {r['f1']:>7.3f} {r['f1_best_thr']:>8.3f} "
              f"{r['roc_auc']:>7.3f} {r['pr_auc']:>7.3f} "
              f"{r['det_at_1pct_fpr']:>7.1f}% {r['det_at_5pct_fpr']:>7.1f}% "
              f"{ci_str:>16}")
    print("=" * 90)
    print("\n  - F1       = 저장된 threshold 기준")
    print("  - F1best   = 최적 threshold 탐색 (이론적 상한)")
    print("  - AUC/PR   = 임계값 독립 ranking 품질")
    print("  - Det@N%FPR= 운영 FPR=N% 동작점에서 탐지율")
    print("  - CI95     = F1 Bootstrap 95% 신뢰구간")

    # ── 앙상블 전수 탐색 (primary 난이도 기준) ──
    print("\n[앙상블] 전수 조합 탐색 (모든 모델, 1~5개 조합)...")
    ens_result = find_best_ensemble(primary_results, max_k=5,
                                    bootstrap=max(20, args.bootstrap // 5))
    if ens_result and len(ens_result) == 5:
        best_combo, best_det1, top_by_det1, best_metrics, top_by_f1 = ens_result
        print(f"\n  === Det@1%FPR 기준 최적 조합 (오탐율 1% 운영점) ===")
        print(f"  최적: {' + '.join(best_combo)}")
        if isinstance(best_metrics, dict):
            print(f"  Det@1%FPR: {best_metrics.get('det_at_1pct_fpr', 0):.1f}%")
            print(f"  F1(best):  {best_metrics.get('f1_best_thr', 0):.3f}")
            print(f"  ROC AUC:   {best_metrics.get('roc_auc', 0):.3f}")

        print(f"\n  상위 10 (Det@1%FPR 순):")
        for rank, t in enumerate(top_by_det1[:10], 1):
            combo_str = ' + '.join(t['names'])
            print(f"    {rank:>2}. {combo_str:<45} Det@1%={t['det_at_1pct_fpr']:>5.1f}%  "
                  f"F1={t['f1_best_thr']:.3f}  AUC={t['roc_auc']:.3f}")

        print(f"\n  상위 10 (F1best 순):")
        for rank, t in enumerate(top_by_f1[:10], 1):
            combo_str = ' + '.join(t['names'])
            print(f"    {rank:>2}. {combo_str:<45} F1={t['f1_best_thr']:.3f}  "
                  f"Det@1%={t['det_at_1pct_fpr']:>5.1f}%  AUC={t['roc_auc']:.3f}")

        top_evaluated = top_by_det1
    else:
        best_combo  = [primary_results[0]["name"]]
        best_det1   = primary_results[0]["det_at_1pct_fpr"]
        top_evaluated = []
        top_by_det1 = []
        top_by_f1   = []
        best_metrics = {}

    # ── 결과 파일 저장 ──
    out_path = os.path.join(args.model_dir, "eval_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("=" * 72 + "\n")
        f.write("JB-Pirate-King 비지도 모델 종합 평가 (v3)\n")
        f.write("=" * 72 + "\n")
        f.write(f"정상 {len(X_normal_raw)}개 | 공격 12종 × {args.attacks_per_type}\n")
        f.write(f"테스트 분할: {test_files_json or '없음'}\n")
        f.write(f"난이도: {', '.join(severities)}\n")
        f.write(f"Bootstrap CI: {args.bootstrap}회\n\n")

        # 기존 형식 호환 (legacy parser용)
        f.write(f"{'모델':<12} {'탐지율':>8} {'오탐율':>8} {'F1':>8}\n")
        f.write("-" * 50 + "\n")
        for r in primary_results:
            f.write(f"{r['name']:<12} {r['tp_rate']:>7.1f}% {r['fp_rate']:>7.1f}% {r['f1']:>8.3f}\n")
        f.write("\n")

        # 확장 통계
        f.write(f"{'모델':<10} {'F1':>7} {'F1best':>8} {'AUC':>7} "
                f"{'PR-AUC':>7} {'Det@1%FPR':>10} {'Det@5%FPR':>10} {'CI95':>16}\n")
        f.write("-" * 80 + "\n")
        for r in primary_results:
            ci_str = f"[{r['f1_ci95_lo']:.2f}, {r['f1_ci95_hi']:.2f}]"
            f.write(f"{r['name']:<10} {r['f1']:>7.3f} {r['f1_best_thr']:>8.3f} "
                    f"{r['roc_auc']:>7.3f} {r['pr_auc']:>7.3f} "
                    f"{r['det_at_1pct_fpr']:>9.1f}% {r['det_at_5pct_fpr']:>9.1f}% "
                    f"{ci_str:>16}\n")

        # 난이도별 표
        f.write(f"\n{'='*72}\n난이도별 Det@1%FPR\n{'='*72}\n")
        hdr = f"{'모델':<12}"
        for sev in severities:
            hdr += f" {sev:>10}"
        f.write(hdr + "\n" + "-" * (12 + 11 * len(severities)) + "\n")
        for mname in model_names:
            row = f"{mname:<12}"
            for sev in severities:
                sev_results = all_severity_results.get(sev, [])
                mr = next((r for r in sev_results if r["name"] == mname), None)
                if mr:
                    row += f" {mr['det_at_1pct_fpr']:>9.1f}%"
                else:
                    row += f"       {'—':>3}"
            f.write(row + "\n")

        # 공격 유형별 표
        if type_table_str:
            f.write(type_table_str)

        f.write(f"\n최적 조합 (Det@1%FPR 기준): {' + '.join(best_combo)}\n")
        if isinstance(best_metrics, dict) and "det_at_1pct_fpr" in best_metrics:
            f.write(f"앙상블 Det@1%FPR: {best_metrics['det_at_1pct_fpr']:.1f}%\n")
            f.write(f"앙상블 F1(best):  {best_metrics.get('f1_best_thr', 0):.3f}\n")
            f.write(f"앙상블 ROC AUC:   {best_metrics.get('roc_auc', 0):.3f}\n")

        if top_evaluated:
            f.write("\n상위 앙상블 후보 (Det@1%FPR 순):\n")
            for rank, t in enumerate(top_evaluated[:10], 1):
                f.write(f"  {rank:>2}. {' + '.join(t['names']):<45}  "
                        f"Det@1%={t['det_at_1pct_fpr']:>5.1f}%  "
                        f"F1={t['f1_best_thr']:.3f}  AUC={t['roc_auc']:.3f}\n")

        if top_by_f1:
            f.write("\n상위 앙상블 후보 (F1best 순):\n")
            for rank, t in enumerate(top_by_f1[:10], 1):
                f.write(f"  {rank:>2}. {' + '.join(t['names']):<45}  "
                        f"F1={t['f1_best_thr']:.3f}  "
                        f"Det@1%={t['det_at_1pct_fpr']:>5.1f}%  AUC={t['roc_auc']:.3f}\n")

    print(f"\n결과 저장: {out_path}")

    # JSON 메트릭 (기계 파싱)
    metrics_path = os.path.join(args.model_dir, "eval_metrics.json")
    # 난이도별 결과 포함
    severity_json = {}
    for sev, sev_results in all_severity_results.items():
        severity_json[sev] = [
            {k: v for k, v in r.items() if not k.startswith("_")}
            for r in sev_results
        ]
    json_data = {
        "version": "v3",
        "model_dir": args.model_dir,
        "data_dir":  args.data_dir,
        "test_files": test_files_json,
        "n_normal":  len(X_normal_raw),
        "n_attack_per_severity":  12 * args.attacks_per_type,
        "severities": severities,
        "bootstrap": args.bootstrap,
        "models": [
            {k: v for k, v in r.items() if not k.startswith("_")}
            for r in primary_results
        ],
        "by_severity": severity_json,
        "best_ensemble": best_combo,
        "best_ensemble_metrics": best_metrics if isinstance(best_metrics, dict) else {},
        "top_ensembles": top_evaluated[:5] if top_evaluated else [],
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"JSON 메트릭: {metrics_path}")

    # 최적 앙상블 이름 저장 (Det@1%FPR 기준)
    # 형식: "lstm + dcdetect" — orchestrator가 +로 파싱
    best_path = os.path.join(args.model_dir, "best_ensemble.txt")
    with open(best_path, "w", encoding="utf-8") as f:
        f.write(" + ".join(best_combo) + "\n")
    print(f"최적 앙상블 (Det@1%FPR 기준): {best_path}")
    print(f"\n최적 조합: {' + '.join(best_combo)}")


if __name__ == "__main__":
    main()
