"""
AIS 선박 이상 탐지 벤치마크 학습 스크립트

정상 AIS 시퀀스로 비지도 학습 후 재구성 오차(MSE) 기반으로 이상 판정.
모든 모델은 동일한 ONNX 인터페이스로 export되어 eval_anomaly.py 및
OpenCPN 플러그인과 호환된다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지원 모델 (9종)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[시계열 기반]

  usad      USAD — KDD 2020
            Encoder 1개 + Decoder 2개(G1, G2)의 adversarial 구조.
            G1은 G2를 속이도록, G2는 G1의 재구성을 간파하도록 학습.
            이상 시퀀스는 두 decoder 간 재구성 불일치가 커진다.
            입력: (B, T*F) flatten → latent → unflatten

  tranad    TranAD — VLDB 2022
            Transformer Encoder + Decoder 2개(D1, D2).
            D1이 1차 재구성, D2가 D1 출력을 조건으로 2차 재구성(self-conditioning).
            학습 스케줄: epoch 진행에 따라 두 loss의 가중치를 동적으로 조정.
            window=10 기본값을 논문에서 사용 → 본 프로젝트 seq=10과 일치.

  conv1d    Conv1D Autoencoder
            Conv1d(kernel=3) × 2 인코더 + ConvTranspose1d × 2 디코더.
            same padding으로 시퀀스 길이를 유지.
            지역 패턴(급격한 방향/속도 변화) 탐지에 강하고 ONNX 변환이 안정적.

  lstm      LSTM Autoencoder
            Encoder LSTM → hidden state → Decoder LSTM (step-by-step).
            각 스텝에서 이전 출력을 다음 입력으로 사용(autoregressive).
            seq=10 환경에서는 장기 의존성 이점이 제한적.

  tcn       TCN Autoencoder — Bai et al., 2018
            Dilated Causal Conv 블록을 스택하여 다양한 receptive field 확보.
            dilation=[1,2,4]로 최대 7타임스텝까지 커버.
            Conv1D AE보다 시간 패턴 포착력이 강하면서도 경량.

  anomtrans Anomaly Transformer — NeurIPS 2022
            핵심: Association Discrepancy.
            - Series Association: 학습된 self-attention 분포
            - Prior Association: 가우시안 커널 기반 고정 분포
            두 분포의 KL 발산을 극대화하면서 재구성 오차를 최소화.
            이상 시퀀스는 두 분포의 불일치가 커져 MSE가 높아진다.

  dcdetect  DCdetector — KDD 2023
            이중 어텐션 구조:
            - Channel-wise Attention: 12개 피처 간 상관관계 학습
            - Patch-wise Attention: seq를 patch(=2)로 분할 후 패치 간 관계 학습
            두 관점의 표현을 결합하여 재구성.
            정교한 위장 공격(E5-Shadow, F1-FeatSmooth 등)에 강점.

[비시계열 기반 — sklearn 필터링 + Dense AE]

  iforest   IsolationForest-guided AE — Liu et al., 2008
            1단계: IsolationForest로 이상 샘플 탐지 (contamination=5%).
            2단계: 정상으로 판별된 90% 샘플만으로 FlattenAE 학습.
            입력을 (T×F) flatten하여 피처 값의 절대적 분포 기반 이상 탐지.
            scikit-learn 필요: pip install scikit-learn

  ocsvm     One-Class SVM-guided AE — Schölkopf et al., 2001
            RBF 커널 OCSVM으로 정상 경계 추정 후 필터링.
            IForest보다 고차원 피처 공간에서의 경계를 정밀하게 설정.
            학습 시간이 길어질 수 있음 (대규모 데이터에서 O(n²) 복잡도).
            scikit-learn 필요: pip install scikit-learn

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
출력 파일 (모델별 분리)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  model_{name}.onnx      ONNX 모델 (input="x", shape=(1, SEQ_LEN, N_FEAT))
  scaler_{name}.json     Min-Max 스케일러 파라미터 (feature별 min/max)
  threshold_{name}.txt   이상 판정 임계값 (정상 학습 데이터의 95 퍼센타일 MSE)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용법
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  python train_benchmark.py --model dcdetect
  python train_benchmark.py --model tranad --epochs 50 --lr 0.0005
  python train_benchmark.py --model all              # 전체 9개 순차 학습
  python train_benchmark.py --model iforest          # scikit-learn 필요

하이퍼파라미터 수정: 파일 내 DEFAULTS 딕셔너리 직접 수정
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", write_through=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", write_through=True)
# D드라이브 패키지 우선, 구 경로 하위 호환
for _p in (r"D:\packages", r"C:\pl"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import os
import shutil
import csv
import glob
import json
import math
import random
import time
import warnings
from collections import defaultdict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset, random_split
from tqdm import tqdm


def notify(msg: str, title: str = "JB-Pirate-King | 학습"):
    """Discord 웹훅 알림 (실패 무시)"""
    try:
        import subprocess
        _notify_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify.py")
        subprocess.Popen(
            [sys.executable, _notify_py, msg, title],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


def get_best_device() -> torch.device:
    """CUDA(NVIDIA) → DirectML 외장GPU 우선 → CPU 순으로 최적 디바이스 선택"""
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        print(f"[GPU] CUDA -- {name}")
        return torch.device("cuda")
    try:
        import torch_directml
        n = torch_directml.device_count()
        # 외장 GPU 우선: 내장(iGPU)은 이름에 "(TM) Graphics" 패턴
        best_idx = 0
        for i in range(n):
            name = torch_directml.device_name(i)
            print(f"  DML [{i}] {name}")
            if "Graphics" not in name or "RX" in name or "RTX" in name or "GTX" in name:
                best_idx = i   # 외장 GPU 발견 시 우선 선택
        chosen_name = torch_directml.device_name(best_idx)
        print(f"[GPU] DirectML [{best_idx}] {chosen_name}  ← 선택")
        return torch_directml.device(best_idx)
    except ImportError:
        pass
    print("[CPU] GPU 미감지, CPU로 실행")
    return torch.device("cpu")

# ── 공통 설정 ─────────────────────────────────────────────────────
FEATURES = [
    "sog", "cog", "heading", "status",
    "dt", "dist_km",
    "cog_hdg_diff", "sog_change",
    "cog_hdg_change",
    "speed_consistency",
    "lat_speed", "lon_speed",
]
SEQ_LEN    = 10
N_FEAT     = len(FEATURES)   # 12
SEED       = 42

random.seed(SEED)
torch.manual_seed(SEED)

# GPU 가속 설정
if torch.cuda.is_available():
    torch.backends.cudnn.benchmark = True   # Conv 자동 최적화 커널 선택

INPUT_FILE           = "."   # 기본: 현재 폴더의 *_preprocessed.csv 파일 전체 사용
SCALER_FILE          = "scaler.json"       # 모델별 실행 시 덮어씀
THRESHOLD_FILE       = "threshold.txt"    # 모델별 실행 시 덮어씀
SEQ_BREAK_DT         = 600
SAMPLE_MMSI          = 6000    # 32GB RAM 활용 -- 6000 MMSI (약 20GB 예상)
MAX_RECS_PER_MMSI    = 1500    # MMSI당 최대 레코드 수
VAL_RATIO            = 0.1
TEST_RATIO           = 0.1   # 시간순 마지막 10% 파일 → 평가 전용 (학습 시 미사용)
THRESHOLD_PERCENTILE = 95


# ══════════════════════════════════════════════════════════════════
# 데이터 파이프라인 (train.py 와 동일)
# ══════════════════════════════════════════════════════════════════

class MinMaxScaler:
    def __init__(self):
        self.min_ = None
        self.max_ = None

    def fit(self, data: list):
        n = len(data[0])
        self.min_ = [min(row[i] for row in data) for i in range(n)]
        self.max_ = [max(row[i] for row in data) for i in range(n)]

    def transform(self, data: list) -> list:
        result = []
        for row in data:
            scaled = []
            for i, val in enumerate(row):
                denom = self.max_[i] - self.min_[i]
                s = (val - self.min_[i]) / denom if denom != 0 else 0.0
                scaled.append(max(0.0, min(1.0, s)))
            result.append(scaled)
        return result

    def fit_transform(self, data: list) -> list:
        self.fit(data)
        return self.transform(data)


def _iter_csv_files(input_path: str):
    """단일 파일, 디렉터리, glob 패턴을 통일된 파일 목록으로 변환"""
    if os.path.isdir(input_path):
        files = sorted(glob.glob(os.path.join(input_path, "*_preprocessed.csv")))
        if not files:
            files = sorted(glob.glob(os.path.join(input_path, "*.csv")))
    else:
        files = sorted(glob.glob(input_path)) if "*" in input_path else [input_path]
    return [f for f in files if os.path.isfile(f)]


CACHE_FILE      = r"D:\JB-Pirate-King-ML-Results\train_data_cache.pt"
CACHE_META_FILE = r"D:\JB-Pirate-King-ML-Results\train_data_cache_meta.json"
OUTPUT_DIR      = r"D:\JB-Pirate-King-ML-Results"   # 모델/스케일러/임계값 저장

# ── 메모리 가드 (v3 OOM 사망 재발 방지) ──────────────────────────────
RAM_GUARD_GB     = 28.0   # 28GB 초과 시 경고/중단 (총 32GB의 87.5%)
RAM_WARN_GB      = 24.0   # 24GB 초과 시 경고만

def _check_ram(ctx: str = ""):
    """현재 프로세스 RAM 사용량 체크. 한도 초과면 경고/예외."""
    try:
        import psutil
        proc = psutil.Process()
        rss_gb = proc.memory_info().rss / (1024**3)
        sys_avail_gb = psutil.virtual_memory().available / (1024**3)
        if rss_gb > RAM_GUARD_GB or sys_avail_gb < 2.0:
            msg = (f"[RAM 가드] {ctx} 프로세스 {rss_gb:.1f}GB / "
                   f"시스템 여유 {sys_avail_gb:.1f}GB → 중단")
            print(msg, flush=True)
            try:
                from notify import send
                send(msg, "JB | RAM 가드 발동")
            except Exception:
                pass
            raise MemoryError(msg)
        elif rss_gb > RAM_WARN_GB:
            print(f"[RAM 경고] {ctx} 프로세스 {rss_gb:.1f}GB / "
                  f"여유 {sys_avail_gb:.1f}GB (한도 {RAM_GUARD_GB}GB)", flush=True)
        return rss_gb, sys_avail_gb
    except ImportError:
        return None, None
    except MemoryError:
        raise
    except Exception:
        return None, None

def load_and_prepare(input_path: str, scaler_path: str = "scaler.json"):
    """
    CSV 로드 → 시퀀스 생성 → 스케일러 fit → Tensor 반환
    캐시(train_data_cache.pt)가 있으면 로딩 생략 (수십 분 → 수 초)

    두 번 패스 스트리밍으로 대용량 데이터 처리:
      1패스: 고유 MMSI 수집 (mmsi 컬럼만 읽음)
      2패스: 샘플링된 MMSI 레코드만 읽음
    → 117GB 파일도 최소 RAM으로 처리 가능
    """
    files = _iter_csv_files(input_path)
    if not files:
        raise FileNotFoundError(f"CSV 파일 없음: {input_path}")

    # ── 시간순 분할: 마지막 TEST_RATIO 비율의 파일은 학습에서 제외 ──
    # 파일명이 ais-YYYY-MM-DD 형식이므로 sort()하면 시간순 정렬됨
    files.sort()
    n_test = max(1, int(len(files) * TEST_RATIO))
    test_files = files[-n_test:]
    train_val_files = files[:-n_test]
    # 테스트 파일 목록 저장 → eval_all.py가 이 파일만 사용
    test_list_path = os.path.join(OUTPUT_DIR, "test_files.json")
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(test_list_path, "w", encoding="utf-8") as f:
            json.dump({"test_files": [os.path.basename(p) for p in test_files],
                        "train_val_files": len(train_val_files),
                        "total_files": len(files),
                        "test_ratio": TEST_RATIO}, f, indent=2, ensure_ascii=False)
        print(f"[분할] 전체 {len(files)}개 → 학습+검증 {len(train_val_files)}개 / 테스트 {n_test}개 (시간순)")
        print(f"  테스트 파일 목록 저장: {test_list_path}")
        print(f"  테스트 기간: {os.path.basename(test_files[0])} ~ {os.path.basename(test_files[-1])}")
    except Exception as e:
        print(f"  [경고] 테스트 파일 목록 저장 실패: {e}")
    files = train_val_files   # 이후 로딩은 train+val 파일만

    # ── 캐시 히트 (시간순 분할 후에 체크) ──────────────────────────────
    # 캐시 유효성: 캐시 메타에 저장된 파일 해시가 현재 train_val 파일과 일치해야 함
    import hashlib
    file_sig = hashlib.md5("|".join(os.path.basename(f) for f in files).encode()).hexdigest()

    cache_valid = False
    if os.path.exists(CACHE_FILE) and os.path.getsize(CACHE_FILE) > 0:
        try:
            if os.path.exists(CACHE_META_FILE):
                with open(CACHE_META_FILE, encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("file_signature") == file_sig:
                    cache_valid = True
                else:
                    print(f"[캐시 무효] 파일 목록 변경됨 (기존 캐시는 테스트 데이터 포함 가능)")
            else:
                print(f"[캐시 무효] 메타 파일 없음 (기존 캐시는 시간순 분할 이전 생성)")
        except Exception:
            pass

    if cache_valid:
        print(f"[캐시] {CACHE_FILE} 에서 로드 중...")
        tensor = torch.load(CACHE_FILE, weights_only=True)
        print(f"  캐시 로드 완료: {tensor.shape}")
        return tensor
    elif os.path.exists(CACHE_FILE):
        print(f"[캐시 삭제] 무효 캐시 제거 중... ({os.path.getsize(CACHE_FILE)/1024/1024:.0f} MB)")
        os.remove(CACHE_FILE)
        if os.path.exists(CACHE_META_FILE):
            os.remove(CACHE_META_FILE)

    print(f"[데이터] {len(files)}개 파일 로드 중... (완료 후 캐시 저장)")

    # pandas + pyarrow 사용 (pyarrow: include_columns으로 실제 컬럼만 읽어 10-20배 빠름)
    try:
        import pandas as pd
        _HAS_PANDAS = True
    except ImportError:
        _HAS_PANDAS = False
        print("  [경고] pandas 없음 -- csv.DictReader 사용 (느림)")

    try:
        import pyarrow.csv as _pa_csv
        import pyarrow.compute as _pa_compute
        import pyarrow as _pa
        _HAS_PYARROW = True
        print("  [최적화] pyarrow 활성화 -- 컬럼 선별 읽기로 I/O 대폭 절약")
    except ImportError:
        _HAS_PYARROW = False

    # ── 1패스: 전체 MMSI 수집 (mmsi 컬럼만 읽음) ──────────────────
    t1_start = time.time()
    all_mmsi = set()
    if _HAS_PYARROW:
        # pyarrow: include_columns으로 mmsi 컬럼만 실제 읽음 (pandas의 10-20배 빠름)
        _pa_read_opts = _pa_csv.ReadOptions(block_size=64 * 1024 * 1024)
        for idx, fpath in enumerate(files):
            try:
                tbl = _pa_csv.read_csv(
                    fpath,
                    read_options=_pa_read_opts,
                    convert_options=_pa_csv.ConvertOptions(include_columns=["mmsi"],
                                                           auto_dict_encode=False),
                )
                col = tbl.column("mmsi").cast(_pa.large_string())
                all_mmsi.update(col.drop_null().to_pylist())
            except Exception as e:
                print(f"  [1패스 스킵] {os.path.basename(fpath)}: {e}")
                continue
            if (idx + 1) % max(1, len(files) // 10) == 0:
                pct = (idx + 1) / len(files) * 100
                el  = time.time() - t1_start
                eta = el / (idx + 1) * (len(files) - idx - 1)
                rss_gb, avail_gb = _check_ram(f"1패스 {pct:.0f}%")
                ram_str = f" RAM={rss_gb:.1f}GB" if rss_gb else ""
                print(f"  1패스 {pct:.0f}% ({idx+1}/{len(files)}) "
                      f"경과 {el/60:.1f}min ETA {eta/60:.1f}min{ram_str}")
    elif _HAS_PANDAS:
        for idx, fpath in enumerate(files):
            try:
                df = pd.read_csv(fpath, usecols=["mmsi"], dtype={"mmsi": str},
                                 low_memory=False, on_bad_lines="skip")
                all_mmsi.update(df["mmsi"].dropna().unique())
            except Exception as e:
                print(f"  [1패스 스킵] {os.path.basename(fpath)}: {e}")
                continue
            if (idx + 1) % max(1, len(files) // 10) == 0:
                pct = (idx + 1) / len(files) * 100
                el  = time.time() - t1_start
                eta = el / (idx + 1) * (len(files) - idx - 1)
                rss_gb, avail_gb = _check_ram(f"1패스 {pct:.0f}%")
                ram_str = f" RAM={rss_gb:.1f}GB" if rss_gb else ""
                print(f"  1패스 {pct:.0f}% ({idx+1}/{len(files)}) "
                      f"경과 {el/60:.1f}min ETA {eta/60:.1f}min{ram_str}")
    else:
        for fpath in files:
            with open(fpath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    m = row.get("mmsi", "")
                    if m:
                        all_mmsi.add(m)
    t1_elapsed = (time.time() - t1_start) / 60
    print(f"  전체 고유 MMSI: {len(all_mmsi):,}  (1패스 {t1_elapsed:.1f}min)")

    # MMSI 샘플링
    if SAMPLE_MMSI and len(all_mmsi) > SAMPLE_MMSI:
        sampled_mmsi = set(random.sample(sorted(all_mmsi), SAMPLE_MMSI))
        print(f"  샘플링: {len(sampled_mmsi):,} MMSI 선택")
    else:
        sampled_mmsi = all_mmsi

    # ── 2패스: 샘플 MMSI 레코드만 읽기 (mmsi + FEATURES 컬럼만) ──
    t2_start = time.time()
    mmsi_data = defaultdict(list)
    cols_needed = ["mmsi"] + FEATURES
    _sampled_pa = _pa.array(list(sampled_mmsi)) if _HAS_PYARROW else None
    if _HAS_PYARROW:
        for idx, fpath in enumerate(files):
            try:
                tbl = _pa_csv.read_csv(
                    fpath,
                    read_options=_pa_read_opts,
                    convert_options=_pa_csv.ConvertOptions(
                        include_columns=cols_needed,
                        auto_dict_encode=False,
                        column_types={"mmsi": _pa.large_string()},
                    ),
                )
                mask = _pa_compute.is_in(tbl.column("mmsi"), value_set=_sampled_pa)
                df = tbl.filter(mask).to_pandas()
                df["mmsi"] = df["mmsi"].astype(str)
                df = df[df["mmsi"].isin(sampled_mmsi)]
                # MMSI별 그룹화하여 레코드 추가
                for mmsi_val, group in df.groupby("mmsi", sort=False):
                    if MAX_RECS_PER_MMSI and len(mmsi_data[mmsi_val]) >= MAX_RECS_PER_MMSI:
                        continue
                    remaining = MAX_RECS_PER_MMSI - len(mmsi_data[mmsi_val]) if MAX_RECS_PER_MMSI else len(group)
                    take = group[FEATURES].values[:remaining]
                    # NaN/Inf 행 제거
                    valid = ~np.isnan(take).any(axis=1) & ~np.isinf(take).any(axis=1)
                    take = take[valid]
                    mmsi_data[mmsi_val].extend(take.tolist())
            except Exception as e:
                print(f"  [2패스 스킵] {os.path.basename(fpath)}: {e}")
                continue
            if (idx + 1) % max(1, len(files) // 10) == 0:
                pct = (idx + 1) / len(files) * 100
                el  = time.time() - t2_start
                eta = el / (idx + 1) * (len(files) - idx - 1)
                rss_gb, avail_gb = _check_ram(f"2패스 {pct:.0f}%")
                ram_str = f" RAM={rss_gb:.1f}GB" if rss_gb else ""
                print(f"  2패스 {pct:.0f}% ({idx+1}/{len(files)}) "
                      f"경과 {el/60:.1f}min ETA {eta/60:.1f}min{ram_str}")
    elif _HAS_PANDAS:
        for idx, fpath in enumerate(files):
            try:
                df = pd.read_csv(fpath, usecols=cols_needed,
                                 dtype={"mmsi": str}, low_memory=False,
                                 on_bad_lines="skip")
                df = df[df["mmsi"].isin(sampled_mmsi)]
                for mmsi_val, group in df.groupby("mmsi", sort=False):
                    if MAX_RECS_PER_MMSI and len(mmsi_data[mmsi_val]) >= MAX_RECS_PER_MMSI:
                        continue
                    remaining = MAX_RECS_PER_MMSI - len(mmsi_data[mmsi_val]) if MAX_RECS_PER_MMSI else len(group)
                    take = group[FEATURES].values[:remaining]
                    valid = ~np.isnan(take).any(axis=1) & ~np.isinf(take).any(axis=1)
                    take = take[valid]
                    mmsi_data[mmsi_val].extend(take.tolist())
            except Exception as e:
                print(f"  [2패스 스킵] {os.path.basename(fpath)}: {e}")
                continue
            if (idx + 1) % max(1, len(files) // 10) == 0:
                pct = (idx + 1) / len(files) * 100
                el  = time.time() - t2_start
                eta = el / (idx + 1) * (len(files) - idx - 1)
                rss_gb, avail_gb = _check_ram(f"2패스 {pct:.0f}%")
                ram_str = f" RAM={rss_gb:.1f}GB" if rss_gb else ""
                print(f"  2패스 {pct:.0f}% ({idx+1}/{len(files)}) "
                      f"경과 {el/60:.1f}min ETA {eta/60:.1f}min{ram_str}")
    else:
        for fpath in files:
            with open(fpath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    mmsi = row.get("mmsi", "")
                    if mmsi not in sampled_mmsi:
                        continue
                    if MAX_RECS_PER_MMSI and len(mmsi_data[mmsi]) >= MAX_RECS_PER_MMSI:
                        continue
                    try:
                        record = [float(row[col]) for col in FEATURES]
                        mmsi_data[mmsi].append(record)
                    except (ValueError, KeyError):
                        continue
    total_recs = sum(len(v) for v in mmsi_data.values())
    t2_elapsed = (time.time() - t2_start) / 60
    print(f"  로드 완료: {len(mmsi_data):,} MMSI | 총 레코드: {total_recs:,}  "
          f"(2패스 {t2_elapsed:.1f}min)")

    # ── 시퀀스 생성 ────────────────────────────────────────────────
    dt_idx      = FEATURES.index("dt")
    dist_km_idx = FEATURES.index("dist_km")
    sequences   = []

    for records in mmsi_data.values():
        segments, current = [], [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK_DT:
                segments.append(current)
                rec = list(rec)
                rec[dt_idx] = rec[dist_km_idx] = 0.0
                current = [rec]
            else:
                current.append(rec)
        segments.append(current)
        for seg in segments:
            if len(seg) < SEQ_LEN:
                continue
            for i in range(len(seg) - SEQ_LEN + 1):
                sequences.append(seg[i: i + SEQ_LEN])

    print(f"  총 시퀀스: {len(sequences):,}")

    flat   = [row for seq in sequences for row in seq]
    scaler = MinMaxScaler()
    scaler.fit(flat)
    scaled = [scaler.transform(seq) for seq in sequences]

    with open(scaler_path, "w") as f:
        json.dump({"features": FEATURES, "min": scaler.min_, "max": scaler.max_}, f, indent=2)
    print(f"  스케일러 저장: {scaler_path}")

    tensor = torch.tensor(scaled, dtype=torch.float32)

    # ── 캐시 저장 (다음 실행 시 로딩 생략) ──────────────────────────
    try:
        torch.save(tensor, CACHE_FILE)
        size_mb = os.path.getsize(CACHE_FILE) / 1024 / 1024
        # 메타 파일도 함께 저장 (캐시 유효성 검증용)
        with open(CACHE_META_FILE, "w", encoding="utf-8") as f:
            json.dump({"file_signature": file_sig,
                        "n_files": len(files),
                        "n_sequences": len(sequences),
                        "tensor_shape": list(tensor.shape),
                        "test_ratio": TEST_RATIO}, f, indent=2)
        print(f"  캐시 저장 완료: {CACHE_FILE} ({size_mb:.0f} MB)")
    except Exception as e:
        print(f"  캐시 저장 실패 (무시): {e}")

    return tensor


def make_loaders(tensor: torch.Tensor, batch_size: int):
    """시간순 분할: 앞쪽 90% train, 뒤쪽 10% val.

    시퀀스는 파일 순서(시간순)로 정렬된 상태이므로,
    뒤쪽 10%는 시간적으로 가장 늦은 데이터 → 데이터 누수 방지.
    (기존 random_split은 같은 선박의 인접 시퀀스가 train/val에 섞여 누수 발생)
    """
    n_total = len(tensor)
    n_val   = max(1, int(n_total * VAL_RATIO))
    n_train = n_total - n_val

    train_tensor = tensor[:n_train]
    val_tensor   = tensor[n_train:]

    pin = torch.cuda.is_available()
    train_loader = DataLoader(TensorDataset(train_tensor), batch_size=batch_size,
                              shuffle=True, drop_last=True, pin_memory=pin, num_workers=0)
    val_loader   = DataLoader(TensorDataset(val_tensor), batch_size=batch_size,
                              shuffle=False, drop_last=False, pin_memory=pin, num_workers=0)
    print(f"  학습: {n_train:,}  검증: {n_val:,}  배치: {batch_size}  (시간순 분할)")
    return train_loader, val_loader


def calc_threshold(model, val_loader, device, threshold_path: str = "threshold.txt") -> float:
    model.eval()
    errors = []
    with torch.no_grad():
        for (batch,) in val_loader:
            batch  = batch.to(device)
            output = model(batch)
            mse    = ((output - batch) ** 2).mean(dim=(1, 2))
            errors.extend(mse.cpu().tolist())
    errors.sort()
    idx = int(len(errors) * THRESHOLD_PERCENTILE / 100)
    thr = errors[min(idx, len(errors) - 1)]
    with open(threshold_path, "w") as f:
        f.write(str(thr))
    print(f"  임계값: {thr:.6f}  (상위 {100 - THRESHOLD_PERCENTILE}%, 검증 데이터 기준)")
    print(f"  임계값 저장: {threshold_path}")
    return thr


def export_onnx(model, device, onnx_path: str):
    # ONNX export는 항상 CPU에서 수행 (DirectML/ROCm 등 비표준 디바이스 대응)
    cpu_model = model.cpu().eval()
    dummy = torch.zeros(1, SEQ_LEN, N_FEAT, dtype=torch.float32)
    pt_path = onnx_path.replace(".onnx", ".pt")

    for opset in (13, 11):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                torch.onnx.export(
                    cpu_model, (dummy,), onnx_path,
                    dynamo=False,
                    opset_version=opset,
                    input_names=["x"],
                    output_names=["output"],
                    dynamic_axes={"x": {0: "batch"}, "output": {0: "batch"}},
                )
            model.to(device)
            print(f"  ONNX 저장 (opset={opset}): {onnx_path}")
            return
        except Exception as e:
            print(f"  ONNX opset={opset} 실패 ({e.__class__.__name__}) -- 다음 시도...")

    # 모든 opset 실패 -> .pt 로 저장
    torch.save(cpu_model.state_dict(), pt_path)
    model.to(device)
    print(f"  ONNX 불가 -- PyTorch 모델 저장: {pt_path}")


# ══════════════════════════════════════════════════════════════════
# 모델 1: USAD — KDD 2020
# Audibert et al., "USAD: UnSupervised Anomaly Detection on
# Multivariate Time Series"
#
# 핵심 아이디어:
#   두 Decoder(G1, G2)가 공유 Encoder를 두고 adversarial 학습.
#   G1은 정상 재구성에 집중하고, G2는 G1이 틀린 곳을 증폭시켜
#   이상 시점의 MSE가 더 크게 벌어지도록 유도.
#
# 구조:
#   Encoder E  : (B,T,F) flatten → MLP → latent z
#   Decoder G1 : z → MLP → (B,T,F)  (1차 재구성)
#   Decoder G2 : z → MLP → (B,T,F)  (adversarial 증폭)
#
# 손실 (n = epoch/N 스케줄):
#   L(θE,θG1) = (1/n)·MSE(G1(E(x)),x) + (1-1/n)·MSE(G2(G1(E(x))),x)
#   L(θE,θG2) = (1/n)·MSE(G2(E(x)),x) − (1-1/n)·MSE(G2(G1(E(x))),x)
#
# 추론: forward(x) → G1(E(x))  재구성 MSE로 이상 판정
# ══════════════════════════════════════════════════════════════════

class USAD(nn.Module):
    def __init__(self, seq_len: int, n_feat: int, latent_dim: int = 40,
                 hidden_dim: int = 128):
        super().__init__()
        self.seq_len   = seq_len
        self.n_feat    = n_feat
        self.input_dim = seq_len * n_feat

        def mlp_block(in_d, out_d):
            return nn.Sequential(
                nn.Linear(in_d, out_d),
                nn.ReLU(),
            )

        # Encoder
        self.encoder = nn.Sequential(
            mlp_block(self.input_dim, hidden_dim),
            mlp_block(hidden_dim, hidden_dim // 2),
            nn.Linear(hidden_dim // 2, latent_dim),
        )

        # Decoder G1
        self.decoder_g1 = nn.Sequential(
            mlp_block(latent_dim, hidden_dim // 2),
            mlp_block(hidden_dim // 2, hidden_dim),
            nn.Linear(hidden_dim, self.input_dim),
        )

        # Decoder G2
        self.decoder_g2 = nn.Sequential(
            mlp_block(latent_dim, hidden_dim // 2),
            mlp_block(hidden_dim // 2, hidden_dim),
            nn.Linear(hidden_dim, self.input_dim),
        )

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        # (B, T, F) → (B, T*F)
        return self.encoder(x.reshape(x.size(0), -1))

    def decode_g1(self, z: torch.Tensor) -> torch.Tensor:
        return self.decoder_g1(z).reshape(-1, self.seq_len, self.n_feat)

    def decode_g2(self, z: torch.Tensor) -> torch.Tensor:
        return self.decoder_g2(z).reshape(-1, self.seq_len, self.n_feat)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """ONNX 추론: G1 재구성만 반환"""
        return self.decode_g1(self.encode(x))


def train_usad(model: USAD, train_loader, val_loader, device,
               epochs: int, lr: float, patience: int):
    # 논문 식:
    #   L(θE,θG1) = (1/n)*||x-G1(E(x))||² + (1-1/n)*||x-G2(G1(E(x)))||²
    #   L(θE,θG2) = (1/n)*||x-G2(E(x))||² - (1-1/n)*||x-G2(G1(E(x)))||²
    opt_e_g1 = torch.optim.Adam(
        list(model.encoder.parameters()) + list(model.decoder_g1.parameters()), lr=lr)
    opt_e_g2 = torch.optim.Adam(
        list(model.encoder.parameters()) + list(model.decoder_g2.parameters()), lr=lr)

    best_val, best_state, patience_cnt = float("inf"), None, 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_l1 = train_l2 = 0.0
        n = 0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch = batch.to(device)
            n_ep  = epoch / epochs   # n/N

            # ── Phase 1: L(θE, θG1) ──────────────────────────────
            z  = model.encode(batch)
            w1 = model.decode_g1(z)                          # G1(E(x))
            w3 = model.decode_g2(model.encode(w1.detach()))  # G2(G1(E(x)))
            l1 = (1 / n_ep) * F.mse_loss(w1, batch) \
               + (1 - 1 / n_ep) * F.mse_loss(w3, batch)
            opt_e_g1.zero_grad()
            l1.backward()
            opt_e_g1.step()

            # ── Phase 2: L(θE, θG2) ──────────────────────────────
            z2 = model.encode(batch)
            w2 = model.decode_g2(z2)                          # G2(E(x))
            w3b = model.decode_g2(model.encode(
                model.decode_g1(z2).detach()))                # G2(G1(E(x)))
            l2 = (1 / n_ep) * F.mse_loss(w2, batch) \
               - (1 - 1 / n_ep) * F.mse_loss(w3b, batch)
            opt_e_g2.zero_grad()
            l2.backward()
            opt_e_g2.step()

            train_l1 += l1.item(); train_l2 += l2.item(); n += 1
            pbar.set_postfix(l1=f"{l1.item():.5f}", l2=f"{l2.item():.5f}")

        # 검증: G1 재구성 MSE
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                out   = model(batch)
                val_loss += F.mse_loss(out, batch).item()
        val_loss /= len(val_loader)

        print(f"  Epoch {epoch:3d}/{epochs} | "
              f"L1={train_l1/n:.5f} L2={train_l2/n:.5f} | val_mse={val_loss:.6f}")

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 2: TranAD — VLDB 2022
# Tuli et al., "TranAD: Deep Transformer Networks for Anomaly
# Detection in Multivariate Time Series Data"
#
# 핵심 아이디어:
#   두 Transformer Decoder(D1, D2)로 self-conditioning 학습.
#   D1이 1차 재구성 → D2가 D1 출력을 조건으로 2차 재구성.
#   학습 초반엔 재구성에 집중(1/n 스케일), 후반엔 adversarial
#   증폭(1-1/n 스케일)으로 이상 시점 MSE를 벌려나감.
#   window=10 단기 시퀀스를 염두에 두고 설계된 모델.
#
# 구조:
#   Input Proj + Positional Encoding → Transformer Encoder
#   Decoder D1: memory + x     → 1차 재구성 o1
#   Decoder D2: memory + o1    → 2차 재구성 o2 (self-conditioning)
#
# 손실 (n = epoch/N, L1은 enc+D1, L2는 enc+D2 별도 optimizer):
#   L1(θenc,θD1) = (1/n)·MSE(o1,x) + (1-1/n)·MSE(o2,x)
#   L2(θenc,θD2) = (1/n)·MSE(o1,x) − (1-1/n)·MSE(o2,o1)
#
# 추론: forward(x) → D1 재구성 MSE로 이상 판정
# ══════════════════════════════════════════════════════════════════

class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 64, dropout: float = 0.1):
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(max_len).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))   # (1, max_len, d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dropout(x + self.pe[:, :x.size(1)])


class TranAD(nn.Module):
    def __init__(self, seq_len: int, n_feat: int,
                 d_model: int = 64, nhead: int = 4,
                 num_encoder_layers: int = 1,
                 num_decoder_layers: int = 1,
                 dim_feedforward: int = 128,
                 dropout: float = 0.1):
        super().__init__()
        self.seq_len = seq_len
        self.n_feat  = n_feat
        self.d_model = d_model

        self.input_proj  = nn.Linear(n_feat, d_model)
        self.pos_enc     = PositionalEncoding(d_model, max_len=seq_len + 4, dropout=dropout)

        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True)
        self.encoder = nn.TransformerEncoder(enc_layer, num_layers=num_encoder_layers)

        dec_layer1 = nn.TransformerDecoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True)
        self.decoder1 = nn.TransformerDecoder(dec_layer1, num_layers=num_decoder_layers)

        dec_layer2 = nn.TransformerDecoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True)
        self.decoder2 = nn.TransformerDecoder(dec_layer2, num_layers=num_decoder_layers)

        self.output_proj1 = nn.Linear(d_model, n_feat)
        self.output_proj2 = nn.Linear(d_model, n_feat)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        z = self.pos_enc(self.input_proj(x))
        return self.encoder(z)

    def decode1(self, memory: torch.Tensor, tgt: torch.Tensor) -> torch.Tensor:
        tgt_emb = self.pos_enc(self.input_proj(tgt))
        out = self.decoder1(tgt_emb, memory)
        return self.output_proj1(out)

    def decode2(self, memory: torch.Tensor, tgt: torch.Tensor) -> torch.Tensor:
        tgt_emb = self.pos_enc(self.input_proj(tgt))
        out = self.decoder2(tgt_emb, memory)
        return self.output_proj2(out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """ONNX 추론: D1 재구성만 반환"""
        memory = self.encode(x)
        return self.decode1(memory, x)


def train_tranad(model: TranAD, train_loader, val_loader, device,
                 epochs: int, lr: float, patience: int):
    # 논문 식:
    #   L1(θ_enc, θ_D1) = (1/n)*MSE(D1(z,x), x) + (1-1/n)*MSE(D2(z,D1), x)
    #   L2(θ_enc, θ_D2) = (1/n)*MSE(D1(z,x), x) - (1-1/n)*MSE(D2(z,D1), D1)
    #   → 인코더는 L1에서만 업데이트, D2는 인코더 파라미터 제외
    enc_params = (list(model.encoder.parameters()) +
                  list(model.pos_enc.parameters()) +
                  list(model.input_proj.parameters()))
    opt_d1 = torch.optim.AdamW(
        enc_params +
        list(model.decoder1.parameters()) +
        list(model.output_proj1.parameters()),
        lr=lr, weight_decay=1e-4)
    opt_d2 = torch.optim.AdamW(
        list(model.decoder2.parameters()) +
        list(model.output_proj2.parameters()),
        lr=lr, weight_decay=1e-4)

    best_val, best_state, patience_cnt = float("inf"), None, 0

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        n = 0
        n_ep = epoch / epochs

        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch  = batch.to(device)

            # ── L1: enc + D1 업데이트 ─────────────────────────────
            memory = model.encode(batch)
            o1     = model.decode1(memory, batch)
            o2     = model.decode2(memory.detach(), o1.detach())
            l1 = (1 / n_ep) * F.mse_loss(o1, batch) \
               + (1 - 1 / n_ep) * F.mse_loss(o2, batch)
            opt_d1.zero_grad()
            l1.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt_d1.step()

            # ── L2: enc + D2 업데이트 ─────────────────────────────
            memory2 = model.encode(batch)
            o1b     = model.decode1(memory2.detach(), batch)
            o2b     = model.decode2(memory2, o1b.detach())
            l2 = (1 / n_ep) * F.mse_loss(o1b.detach(), batch) \
               - (1 - 1 / n_ep) * F.mse_loss(o2b, o1b.detach())
            opt_d2.zero_grad()
            l2.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt_d2.step()

            total_loss += (l1.item() + l2.item()); n += 1
            pbar.set_postfix(l1=f"{l1.item():.5f}", l2=f"{l2.item():.5f}")

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                out   = model(batch)
                val_loss += F.mse_loss(out, batch).item()
        val_loss /= len(val_loader)

        print(f"  Epoch {epoch:3d}/{epochs} | "
              f"train={total_loss/n:.5f} | val_mse={val_loss:.6f}")

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 3: Conv1D Autoencoder — 2011
# Masci et al., "Stacked Convolutional Auto-Encoders for
# Hierarchical Feature Extraction" (ICANN 2011) 기반 시계열 변형
#
# 핵심 아이디어:
#   1D 합성곱으로 시퀀스의 지역적(local) 패턴을 추출.
#   kernel_size=3, same padding으로 시퀀스 길이를 유지하며
#   채널 수를 압축(encoder)했다가 복원(decoder).
#   LSTM보다 빠르고 ONNX 변환이 안정적이며,
#   방향/속도의 급격한 단기 변화 탐지에 강함.
#
# 구조:
#   Encoder: (B,T,F) → permute → Conv1d(F→64, k=3) → BN → ReLU
#                              → Conv1d(64→32, k=3) → BN → ReLU
#   Decoder: ConvTranspose1d(32→64) → ReLU
#          → ConvTranspose1d(64→F)  → permute → (B,T,F)
# ══════════════════════════════════════════════════════════════════

class Conv1DAE(nn.Module):
    def __init__(self, n_feat: int, hidden_ch: int = 32):
        super().__init__()
        # Encoder
        self.enc1 = nn.Conv1d(n_feat, hidden_ch * 2, kernel_size=3, padding=1)
        self.enc2 = nn.Conv1d(hidden_ch * 2, hidden_ch, kernel_size=3, padding=1)
        # Decoder
        self.dec1 = nn.ConvTranspose1d(hidden_ch, hidden_ch * 2, kernel_size=3, padding=1)
        self.dec2 = nn.ConvTranspose1d(hidden_ch * 2, n_feat, kernel_size=3, padding=1)
        self.bn1  = nn.BatchNorm1d(hidden_ch * 2)
        self.bn2  = nn.BatchNorm1d(hidden_ch)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, F) → (B, F, T) for Conv1d
        z = x.permute(0, 2, 1)
        z = F.relu(self.bn1(self.enc1(z)))
        z = F.relu(self.bn2(self.enc2(z)))
        z = F.relu(self.dec1(z))
        z = self.dec2(z)
        return z.permute(0, 2, 1)   # (B, T, F)


def train_standard(model: nn.Module, train_loader, val_loader, device,
                   epochs: int, lr: float, patience: int):
    """표준 MSE 재구성 손실 학습 루프 (Conv1D, LSTM, TCN, DCdetector, FlattenAE 공용)"""
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    best_val, best_state, patience_cnt = float("inf"), None, 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch  = batch.to(device)
            output = model(batch)
            loss   = F.mse_loss(output, batch)
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            train_loss += loss.item()
            pbar.set_postfix(loss=f"{loss.item():.6f}")
        train_loss /= len(train_loader)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                val_loss += F.mse_loss(model(batch), batch).item()
        val_loss /= len(val_loader)
        print(f"  Epoch {epoch:3d}/{epochs} | train={train_loss:.6f} | val={val_loss:.6f}")

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음")
                break

    if best_state:
        model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 4: LSTM Autoencoder — 2015
# Srivastava et al., "Unsupervised Learning of Video Representations
# using LSTMs" (ICML 2015) Seq2Seq 구조를 이상 탐지에 적용
#
# 핵심 아이디어:
#   Encoder LSTM이 시퀀스를 압축해 hidden state를 생성하고,
#   Decoder LSTM이 step-by-step으로 시퀀스를 재구성.
#   정상 패턴을 학습한 후 이상 시점에서 재구성 오차가 커지는
#   원리를 이용.
#   단, 시퀀스 길이=10처럼 짧은 경우 LSTM의 장기 의존성
#   학습 이점이 제한적이며, Conv1D/TCN 대비 성능이 낮을 수 있음.
#
# 구조:
#   Encoder: LSTM(F→hidden, 2 layers) → (hidden, cell)
#   Decoder: LSTM(F→hidden, 2layers) + start_token → step-by-step
#          → Linear(hidden→F) × seq_len → (B,T,F)
# ══════════════════════════════════════════════════════════════════

class LSTMAutoencoder(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = 64, num_layers: int = 2):
        super().__init__()
        self.encoder      = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.decoder      = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.output_layer = nn.Linear(hidden_size, input_size)
        self.start_token  = nn.Parameter(torch.zeros(1, 1, input_size))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.size(0)
        _, (hidden, cell) = self.encoder(x)
        dec_input  = self.start_token.expand(batch_size, 1, -1).clone()
        dec_hidden, dec_cell = hidden, cell
        steps = []
        for _ in range(x.size(1)):
            out, (dec_hidden, dec_cell) = self.decoder(dec_input, (dec_hidden, dec_cell))
            step_out  = self.output_layer(out)
            steps.append(step_out)
            dec_input = step_out
        return torch.cat(steps, dim=1)


# ══════════════════════════════════════════════════════════════════
# 모델 5: TCN Autoencoder — 2018
# Bai et al., "An Empirical Evaluation of Generic Convolutional
# and Recurrent Networks for Sequence Modeling" (arXiv 2018)
#
# 핵심 아이디어:
#   Dilated Causal Convolution으로 receptive field를 지수적으로
#   확장. dilation=[1,2,4]이면 최대 7 스텝 과거를 참조 가능.
#   각 TCNBlock은 residual 연결로 gradient 소실을 방지.
#   seq=10에서 Conv1D AE보다 다양한 시간 스케일 패턴 포착에 유리.
#
# 구조:
#   Input Proj: Conv1d(F→hidden_ch, k=1)
#   Encoder: TCNBlock(dilation=1) → TCNBlock(dilation=2)
#          → TCNBlock(dilation=4)
#   Decoder: TCNBlock(dilation=4) → TCNBlock(dilation=2)
#          → TCNBlock(dilation=1)  (역순 symmetric)
#   Output Proj: Conv1d(hidden_ch→F, k=1)
#   TCNBlock: Conv1d×2 + BN + ReLU + Dropout + residual
# ══════════════════════════════════════════════════════════════════

class TCNBlock(nn.Module):
    def __init__(self, n_ch: int, kernel_size: int, dilation: int, dropout: float = 0.1):
        super().__init__()
        pad = (kernel_size - 1) * dilation
        self.conv1 = nn.Conv1d(n_ch, n_ch, kernel_size, dilation=dilation, padding=pad)
        self.conv2 = nn.Conv1d(n_ch, n_ch, kernel_size, dilation=dilation, padding=pad)
        self.bn1   = nn.BatchNorm1d(n_ch)
        self.bn2   = nn.BatchNorm1d(n_ch)
        self.drop  = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        T = x.size(2)
        z = self.drop(F.relu(self.bn1(self.conv1(x)[:, :, :T])))
        z = self.drop(F.relu(self.bn2(self.conv2(z)[:, :, :T])))
        return x + z


class TCNAE(nn.Module):
    def __init__(self, n_feat: int, hidden_ch: int = 32,
                 kernel_size: int = 3, dilations: list = None):
        super().__init__()
        if dilations is None:
            dilations = [1, 2, 4]
        self.input_proj  = nn.Conv1d(n_feat, hidden_ch, 1)
        self.enc_blocks  = nn.ModuleList([TCNBlock(hidden_ch, kernel_size, d) for d in dilations])
        self.dec_blocks  = nn.ModuleList([TCNBlock(hidden_ch, kernel_size, d) for d in reversed(dilations)])
        self.output_proj = nn.Conv1d(hidden_ch, n_feat, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.input_proj(x.permute(0, 2, 1))
        for block in self.enc_blocks:
            z = block(z)
        for block in self.dec_blocks:
            z = block(z)
        return self.output_proj(z).permute(0, 2, 1)


# ══════════════════════════════════════════════════════════════════
# 모델 6: Anomaly Transformer — NeurIPS 2022
# Xu et al., "Anomaly Transformer: Time Series Anomaly Detection
# with Association Discrepancy"
#
# 핵심 아이디어 (Association Discrepancy):
#   정상 구간: 어텐션이 인접 시점에 집중 → Gaussian prior와 유사
#   이상 구간: 어텐션이 분산되거나 편중 → prior와 크게 차이남
#   이 차이(KL divergence)를 손실에 포함해 두 분포 간 거리를
#   극대화하도록 학습 → 이상 시점의 재구성 오차가 더 커짐
#
# 구조:
#   각 Transformer Layer에 두 종류의 어텐션 내재:
#     Series Association: 학습된 self-attention (B,H,T,T)
#     Prior Association : 학습 가능한 sigma의 Gaussian kernel
#   재구성 손실 + Association Discrepancy 손실(KL)의 합으로 학습
#   loss = MSE(recon, x) − λ·(KL(prior‖series) + KL(series‖prior))
#
# ONNX 추론: forward(x) 는 재구성만 반환 (KL은 학습 전용)
# ══════════════════════════════════════════════════════════════════

class AnomalyAttentionLayer(nn.Module):
    def __init__(self, d_model: int, nhead: int, seq_len: int, dropout: float = 0.1):
        super().__init__()
        self.nhead   = nhead
        self.d_head  = d_model // nhead
        self.seq_len = seq_len
        self.q   = nn.Linear(d_model, d_model)
        self.k   = nn.Linear(d_model, d_model)
        self.v   = nn.Linear(d_model, d_model)
        self.out = nn.Linear(d_model, d_model)
        self.drop = nn.Dropout(dropout)
        # Learnable sigma for Gaussian prior (per head)
        self.sigma = nn.Parameter(torch.ones(nhead) * 0.5)

    def _prior_assoc(self, device) -> torch.Tensor:
        pos  = torch.arange(self.seq_len, dtype=torch.float32, device=device).unsqueeze(0)
        diff = (pos.T - pos) ** 2
        sig  = self.sigma.abs().clamp(min=1e-3)
        p    = torch.exp(-diff.unsqueeze(0) / (2 * sig.view(-1, 1, 1) ** 2))
        return p / (p.sum(-1, keepdim=True) + 1e-9)

    def forward(self, x: torch.Tensor):
        B, T, D = x.shape
        H, d = self.nhead, self.d_head
        Q = self.q(x).view(B, T, H, d).permute(0, 2, 1, 3)
        K = self.k(x).view(B, T, H, d).permute(0, 2, 1, 3)
        V = self.v(x).view(B, T, H, d).permute(0, 2, 1, 3)
        series = F.softmax(Q @ K.transpose(-1, -2) / (d ** 0.5), dim=-1)
        prior  = self._prior_assoc(x.device).unsqueeze(0)
        ctx = self.drop(series) @ V
        out = self.out(ctx.permute(0, 2, 1, 3).reshape(B, T, D))
        return out, series, prior


class AnomalyTransformerLayer(nn.Module):
    def __init__(self, d_model: int, nhead: int, seq_len: int,
                 dim_ff: int = 128, dropout: float = 0.1):
        super().__init__()
        self.attn  = AnomalyAttentionLayer(d_model, nhead, seq_len, dropout)
        self.ff    = nn.Sequential(
            nn.Linear(d_model, dim_ff), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(dim_ff, d_model))
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.drop  = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor):
        a, series, prior = self.attn(x)
        x = self.norm1(x + self.drop(a))
        x = self.norm2(x + self.drop(self.ff(x)))
        return x, series, prior


class AnomalyTransformerAE(nn.Module):
    def __init__(self, seq_len: int, n_feat: int,
                 d_model: int = 64, nhead: int = 4,
                 n_layers: int = 2, dim_ff: int = 128, dropout: float = 0.1):
        super().__init__()
        self.input_proj  = nn.Linear(n_feat, d_model)
        self.pos_enc     = PositionalEncoding(d_model, max_len=seq_len + 4, dropout=dropout)
        self.layers      = nn.ModuleList([
            AnomalyTransformerLayer(d_model, nhead, seq_len, dim_ff, dropout)
            for _ in range(n_layers)])
        self.output_proj = nn.Linear(d_model, n_feat)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """ONNX 추론: 재구성만"""
        z = self.pos_enc(self.input_proj(x))
        for layer in self.layers:
            z, _, _ = layer(z)
        return self.output_proj(z)

    def forward_train(self, x: torch.Tensor):
        z = self.pos_enc(self.input_proj(x))
        series_list, prior_list = [], []
        for layer in self.layers:
            z, s, p = layer(z)
            series_list.append(s); prior_list.append(p)
        return self.output_proj(z), series_list, prior_list


def _assoc_discrepancy(series_list, prior_list):
    loss = 0.0
    for s, p in zip(series_list, prior_list):
        p_ = p.expand_as(s) + 1e-9
        s_ = s + 1e-9
        loss += ((p_ * (p_ / s_).log()).sum(-1).mean() +
                 (s_ * (s_ / p_).log()).sum(-1).mean()) / 50.0
    return loss / len(series_list)


def train_anomtrans(model: AnomalyTransformerAE, train_loader, val_loader, device,
                    epochs: int, lr: float, patience: int):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    best_val, best_state, patience_cnt = float("inf"), None, 0
    for epoch in range(1, epochs + 1):
        model.train()
        t_loss = 0.0
        pbar = tqdm(train_loader, desc=f"Epoch {epoch:3d}/{epochs}", leave=False)
        for (batch,) in pbar:
            batch = batch.to(device)
            recon, series, prior = model.forward_train(batch)
            r = F.mse_loss(recon, batch)
            a = _assoc_discrepancy(series, prior)
            loss = r - a   # maximize discrepancy, minimize recon
            optimizer.zero_grad(); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            t_loss += r.item()
            pbar.set_postfix(recon=f"{r.item():.5f}", assoc=f"{a.item():.5f}")
        t_loss /= len(train_loader)
        model.eval()
        v_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                v_loss += F.mse_loss(model(batch.to(device)), batch.to(device)).item()
        v_loss /= len(val_loader)
        print(f"  Epoch {epoch:3d}/{epochs} | recon={t_loss:.6f} | val={v_loss:.6f}")
        if v_loss < best_val - 1e-6:
            best_val = v_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_cnt = 0
        else:
            patience_cnt += 1
            if patience_cnt >= patience:
                print(f"  조기 종료: {patience} epoch 개선 없음"); break
    if best_state: model.load_state_dict(best_state)
    print(f"  최적 검증 MSE: {best_val:.6f}")


# ══════════════════════════════════════════════════════════════════
# 모델 7: DCdetector — KDD 2023
# Yang et al., "DCdetector: Dual Attention Contrastive
# Representation Learning for Time Series Anomaly Detection"
#
# 핵심 아이디어 (Dual Attention):
#   피처 간 관계(Channel-wise)와 시간 패턴(Patch-wise) 두 관점을
#   동시에 학습. 정교하게 위장된 이상(F1-FeatSmooth, E5-Shadow 등)
#   처럼 단일 차원 분석으로 놓치기 쉬운 이상을 포착하는 데 강함.
#
# 구조:
#   1. Channel-wise Attention: (B,T,F) → Multi-head Attn(head=F)
#      → 피처 간 상관 패턴 학습, Add&Norm
#   2. Patchify: (B,T,F) → (B,n_patches, patch_size×F)
#      patch_size=2이면 seq=10 → 5 patches
#   3. Patch-wise Attention: patch embedding → Multi-head Attn
#      → 시퀀스 내 구간 간 패턴 학습
#   4. Decoder: Linear → reshape → (B,T,F)
# ══════════════════════════════════════════════════════════════════

class DCdetector(nn.Module):
    def __init__(self, seq_len: int, n_feat: int,
                 patch_size: int = 2, d_model: int = 64,
                 nhead: int = 4, dropout: float = 0.1):
        super().__init__()
        self.seq_len    = seq_len
        self.n_feat     = n_feat
        self.patch_size = patch_size
        self.n_patches  = seq_len // patch_size
        # Channel-wise attention
        self.ch_attn  = nn.MultiheadAttention(
            n_feat, num_heads=min(nhead, n_feat), dropout=dropout, batch_first=True)
        self.ch_norm  = nn.LayerNorm(n_feat)
        # Patch embedding + attention
        self.patch_embed = nn.Linear(patch_size * n_feat, d_model)
        self.pt_attn     = nn.MultiheadAttention(d_model, nhead, dropout=dropout, batch_first=True)
        self.pt_norm     = nn.LayerNorm(d_model)
        # Decoder
        self.decoder = nn.Linear(d_model, patch_size * n_feat)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, T, F = x.shape
        # Channel-wise
        ch, _ = self.ch_attn(x, x, x)
        x_ch  = self.ch_norm(x + ch)
        # Patchify
        n = T // self.patch_size
        patches  = x_ch[:, :n * self.patch_size, :].reshape(B, n, self.patch_size * F)
        pt_emb   = self.patch_embed(patches)
        pt_out, _= self.pt_attn(pt_emb, pt_emb, pt_emb)
        pt_out   = self.pt_norm(pt_emb + pt_out)
        # Decode
        recon = self.decoder(pt_out).reshape(B, n * self.patch_size, F)
        if recon.size(1) < T:
            recon = torch.cat([recon, x[:, recon.size(1):, :]], dim=1)
        return recon


# ══════════════════════════════════════════════════════════════════
# 모델 8/9: 비시계열 이상치 탐지 기반 Autoencoder
#
# IsolationForest — ICDM 2008
#   Liu et al., "Isolation Forest"
#   랜덤 트리로 샘플을 고립시키는 횟수로 이상도 측정.
#   고립이 빠를수록 이상. 트리 앙상블이라 빠르고 고차원에 강함.
#
# One-Class SVM — NIPS 2001
#   Schölkopf et al., "Estimating the Support of a High-Dimensional
#   Distribution"
#   정상 데이터의 결정 경계를 RBF 커널로 학습.
#   경계 밖 샘플을 이상으로 판정. 데이터가 많으면 느림.
#
# 적용 방식 (sklearn-guided AE):
#   (B,T,F) → flatten → (B,T×F) 벡터로 sklearn 학습
#   sklearn이 이상으로 판별한 하위 10% 샘플을 제거하고
#   나머지 정상 샘플만으로 FlattenAE(MLP AE)를 학습.
#   → sklearn이 정상 분포를 정의, AE가 재구성 기반 스코어 생성.
#
# ONNX: FlattenAE 그대로 export (input "x", shape (1,T,F) 호환)
# ══════════════════════════════════════════════════════════════════

class FlattenAE(nn.Module):
    """비시계열용 MLP AE: flatten → encode → decode → unflatten"""
    def __init__(self, seq_len: int, n_feat: int,
                 hidden_dim: int = 128, latent_dim: int = 32):
        super().__init__()
        self.seq_len   = seq_len
        self.n_feat    = n_feat
        self.input_dim = seq_len * n_feat
        self.encoder = nn.Sequential(
            nn.Linear(self.input_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(),
            nn.Linear(hidden_dim // 2, latent_dim),
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim // 2), nn.ReLU(),
            nn.Linear(hidden_dim // 2, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, self.input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B = x.size(0)
        return self.decoder(self.encoder(x.reshape(B, -1))).reshape(B, self.seq_len, self.n_feat)


def train_sklearn_ae(sk_name: str, model: FlattenAE, tensor: torch.Tensor,
                     train_loader, val_loader, device,
                     epochs: int, lr: float, patience: int):
    """sklearn으로 정상 샘플 필터링 후 FlattenAE 학습"""
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.svm import OneClassSVM
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("  ⚠ scikit-learn 없음 → pip install scikit-learn")
        train_standard(model, train_loader, val_loader, device, epochs, lr, patience)
        return

    print(f"  sklearn {sk_name} 학습 중 (flatten shape: {tensor.shape[0]}×{tensor.shape[1]*tensor.shape[2]})...")
    X_flat = tensor.reshape(len(tensor), -1).numpy()
    sc = StandardScaler()
    X_sc = sc.fit_transform(X_flat)

    if sk_name == "iforest":
        sk = IsolationForest(n_estimators=100, contamination=0.05, random_state=SEED, n_jobs=-1)
    else:
        sk = OneClassSVM(kernel="rbf", nu=0.05, gamma="scale")

    sk.fit(X_sc)
    scores = sk.score_samples(X_sc)   # 높을수록 정상
    thr_sk = float(np.percentile(scores, 10))
    mask   = scores >= thr_sk
    print(f"  정상 필터: {mask.sum():,}/{len(mask):,}개 ({mask.mean()*100:.1f}%) 로 AE 학습")

    normal_tensor  = tensor[torch.from_numpy(mask)]
    normal_loader  = DataLoader(TensorDataset(normal_tensor),
                                batch_size=train_loader.batch_size,
                                shuffle=True, drop_last=True)
    train_standard(model, normal_loader, val_loader, device, epochs, lr, patience)

# ══════════════════════════════════════════════════════════════════
# 신규 알고리즘 1: DeepSVDD (Ruff et al., ICML 2018)
# ══════════════════════════════════════════════════════════════════
class DeepSVDD(nn.Module):
    """
    Deep Support Vector Data Description + AE 하이브리드
    - 인코더+디코더로 재구성 학습 (AE loss)
    - 잠재 공간 중심 C 근방으로 집중 학습 (SVDD loss)
    - 이상 = 재구성 오차 高 + 잠재 중심 거리 遠
    OCSVM 대체 (GPU 가속, O(n) 복잡도)
    """
    def __init__(self, seq_len: int = SEQ_LEN, n_feat: int = N_FEAT,
                 hidden_dim: int = 128, latent_dim: int = 32):
        super().__init__()
        self.seq_len, self.n_feat, self.latent_dim = seq_len, n_feat, latent_dim
        self.encoder = nn.Sequential(
            nn.Flatten(),
            nn.Linear(seq_len * n_feat, hidden_dim),
            nn.GELU(),
            nn.LayerNorm(hidden_dim),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Linear(hidden_dim // 2, latent_dim),
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Linear(hidden_dim // 2, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, seq_len * n_feat),
        )
        self.register_buffer("center", torch.zeros(latent_dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B = x.shape[0]
        z = self.encoder(x)
        return self.decoder(z).view(B, self.seq_len, self.n_feat)

    def svdd_loss(self, x: torch.Tensor) -> torch.Tensor:
        z = self.encoder(x)
        return ((z - self.center) ** 2).mean()


def train_deepsvdd(model: DeepSVDD, train_loader, val_loader, device,
                   epochs: int, lr: float, patience: int):
    """AE loss + SVDD loss 결합 학습. center는 첫 배치 forward로 초기화."""
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=lr * 0.01)

    # center 초기화 (첫 에폭 인코더 출력 평균)
    model.eval()
    with torch.no_grad():
        z_list = []
        for (batch,) in train_loader:
            z = model.encoder(batch.to(device))
            z_list.append(z)
            if len(z_list) >= 10:
                break
        model.center.copy_(torch.cat(z_list).mean(0).detach())
    model.train()

    best_val, wait = float("inf"), 0
    svdd_w = 0.3  # SVDD loss 가중치

    for ep in range(1, epochs + 1):
        model.train()
        t_loss = 0.0
        for (batch,) in tqdm(train_loader, desc=f"Epoch {ep:3d}/{epochs}", leave=False):
            batch = batch.to(device)
            recon = model(batch)
            ae_loss   = F.mse_loss(recon, batch)
            svdd_loss = model.svdd_loss(batch)
            loss = ae_loss + svdd_w * svdd_loss
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            t_loss += loss.item()
        scheduler.step()

        model.eval()
        v_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                recon = model(batch)
                v_loss += F.mse_loss(recon, batch).item()
        v_loss /= max(len(val_loader), 1)

        if ep % 10 == 0 or ep == epochs:
            print(f"  Epoch {ep:3d}/{epochs} | train={t_loss/len(train_loader):.6f} | val={v_loss:.6f}")

        if v_loss < best_val - 1e-6:
            best_val, wait = v_loss, 0
        else:
            wait += 1
            if wait >= patience:
                print(f"  조기 종료 (epoch={ep})")
                break


# ══════════════════════════════════════════════════════════════════
# 신규 알고리즘 2: TimesNet AE (Wu et al., ICLR 2023 -- 간소화)
# ══════════════════════════════════════════════════════════════════
class TimesNetAE(nn.Module):
    """
    TimesNet 아이디어 적용: 시계열을 (T × d_model) 2D 공간으로 임베딩 후
    Conv2D 인코더/디코더로 temporal + feature 패턴을 동시에 포착.
    seq_len=10 짧은 시퀀스에 최적화된 경량 버전.
    """
    def __init__(self, seq_len: int = SEQ_LEN, n_feat: int = N_FEAT,
                 d_model: int = 32, n_ch: int = 16):
        super().__init__()
        self.seq_len, self.n_feat, self.d_model = seq_len, n_feat, d_model
        self.embed = nn.Linear(n_feat, d_model)
        # 인코더: (B, 1, T, d_model) → Conv2D 스택
        self.enc = nn.Sequential(
            nn.Conv2d(1, n_ch, kernel_size=(3, 3), padding=1),
            nn.GELU(),
            nn.Conv2d(n_ch, n_ch * 2, kernel_size=(3, 3), padding=1),
            nn.GELU(),
        )
        # 디코더
        self.dec = nn.Sequential(
            nn.ConvTranspose2d(n_ch * 2, n_ch, kernel_size=(3, 3), padding=1),
            nn.GELU(),
            nn.ConvTranspose2d(n_ch, 1, kernel_size=(3, 3), padding=1),
        )
        self.proj = nn.Linear(d_model, n_feat)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, F)
        h = self.embed(x).unsqueeze(1)          # (B, 1, T, d_model)
        h = self.dec(self.enc(h)).squeeze(1)     # (B, T, d_model)
        return self.proj(h)                      # (B, T, F)


# ══════════════════════════════════════════════════════════════════
# 신규 알고리즘 3: DAGMM (Zong et al., ICLR 2018)
# ══════════════════════════════════════════════════════════════════
class DAGMM(nn.Module):
    """
    Deep Autoencoding Gaussian Mixture Model
    - AE로 재구성 + 잠재벡터+재구성오차 결합 → GMM 멤버십 추정
    - 에너지(GMM 음로그우도) 기반 이상 점수
    - forward()는 eval_anomaly.py 호환을 위해 재구성 텐서 반환
    """
    def __init__(self, seq_len: int = SEQ_LEN, n_feat: int = N_FEAT,
                 latent_dim: int = 16, n_gmm: int = 4):
        super().__init__()
        self.seq_len, self.n_feat, self.latent_dim, self.n_gmm = seq_len, n_feat, latent_dim, n_gmm
        in_dim = seq_len * n_feat
        self.encoder = nn.Sequential(
            nn.Flatten(),
            nn.Linear(in_dim, 64), nn.Tanh(),
            nn.Linear(64, latent_dim),
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 64), nn.Tanh(),
            nn.Linear(64, in_dim),
        )
        # z_combined = [z(latent_dim) | recon_err(1) | cos_sim(1)]
        self.estimation = nn.Sequential(
            nn.Linear(latent_dim + 2, 16), nn.Tanh(),
            nn.Dropout(0.5),
            nn.Linear(16, n_gmm), nn.Softmax(dim=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B = x.shape[0]
        z = self.encoder(x)
        return self.decoder(z).view(B, self.seq_len, self.n_feat)

    def _z_combined(self, x: torch.Tensor):
        B = x.shape[0]
        z = self.encoder(x)
        x_hat = self.decoder(z).view(B, self.seq_len, self.n_feat)
        xf = x.view(B, -1); xhf = x_hat.view(B, -1)
        err = ((xf - xhf) ** 2).mean(1, keepdim=True)
        cos = F.cosine_similarity(xf, xhf, dim=1, eps=1e-8).unsqueeze(1)
        return torch.cat([z, err, cos], dim=1), x_hat

    def energy(self, x: torch.Tensor):
        """이상 점수 (에너지) -- 학습 후 사용"""
        zc, _ = self._z_combined(x)
        gamma = self.estimation(zc)        # (B, K)
        # batch 단위 GMM 파라미터 추정
        phi = gamma.mean(0)                # (K,)
        mu  = (gamma.T @ zc) / gamma.sum(0, keepdim=True).T  # (K, D)
        diff = zc.unsqueeze(1) - mu.unsqueeze(0)              # (B, K, D)
        sigma = (gamma.unsqueeze(-1) * diff.unsqueeze(-1) *
                 diff.unsqueeze(-2)).mean(0)                   # (K, D, D)
        # 에너지 계산
        energies = []
        for k in range(self.n_gmm):
            S = sigma[k] + 1e-6 * torch.eye(sigma.shape[-1], device=x.device)
            try:
                Sinv = torch.linalg.inv(S)
                det  = torch.linalg.det(S).clamp(min=1e-12)
            except Exception:
                Sinv = torch.eye(S.shape[-1], device=x.device)
                det  = torch.tensor(1.0, device=x.device)
            d = diff[:, k, :]  # (B, D)
            e = -0.5 * (d.unsqueeze(1) @ Sinv @ d.unsqueeze(2)).squeeze() \
                - 0.5 * det.log()
            energies.append(phi[k] * e.exp())
        return -torch.stack(energies, dim=1).sum(1).log().clamp(min=-100)


def train_dagmm(model: DAGMM, train_loader, val_loader, device,
                epochs: int, lr: float, patience: int):
    """AE loss + GMM 에너지 loss 결합 학습"""
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=lr*0.01)
    best_val, wait = float("inf"), 0

    for ep in range(1, epochs + 1):
        model.train()
        t_loss = 0.0
        for (batch,) in tqdm(train_loader, desc=f"Epoch {ep:3d}/{epochs}", leave=False):
            batch = batch.to(device)
            zc, x_hat = model._z_combined(batch)
            gamma = model.estimation(zc)
            ae_loss = F.mse_loss(x_hat, batch)
            # 에너지 정규화 손실 (간소화 버전)
            phi = gamma.mean(0)
            reg = -(phi * (phi + 1e-8).log()).sum()  # 엔트로피 최대화로 GMM 다양성 유지
            loss = ae_loss + 0.1 * reg
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            t_loss += ae_loss.item()
        scheduler.step()

        model.eval()
        v_loss = 0.0
        with torch.no_grad():
            for (batch,) in val_loader:
                batch = batch.to(device)
                _, x_hat = model._z_combined(batch)
                v_loss += F.mse_loss(x_hat, batch).item()
        v_loss /= max(len(val_loader), 1)

        if ep % 10 == 0 or ep == epochs:
            print(f"  Epoch {ep:3d}/{epochs} | train={t_loss/len(train_loader):.6f} | val={v_loss:.6f}")

        if v_loss < best_val - 1e-6:
            best_val, wait = v_loss, 0
        else:
            wait += 1
            if wait >= patience:
                print(f"  조기 종료 (epoch={ep})")
                break


# ══════════════════════════════════════════════════════════════════
# 모델별 하이퍼파라미터 ← 여기서 직접 수정
# ══════════════════════════════════════════════════════════════════
DEFAULTS = {
    #              epochs  lr      batch   patience
    "usad":      dict(epochs=50,  lr=1e-3, batch_size=2048, patience=7),
    "tranad":    dict(epochs=50,  lr=1e-3, batch_size=2048, patience=7),
    "conv1d":    dict(epochs=30,  lr=1e-3, batch_size=2048, patience=5),
    "lstm":      dict(epochs=30,  lr=1e-3, batch_size=2048, patience=5),
    "tcn":       dict(epochs=30,  lr=1e-3, batch_size=2048, patience=5),
    "anomtrans": dict(epochs=50,  lr=1e-3, batch_size=2048, patience=7),
    "dcdetect":  dict(epochs=30,  lr=1e-3, batch_size=2048, patience=5),
    "iforest":   dict(epochs=30,  lr=1e-3, batch_size=2048, patience=5),
    # 신규
    "deepsvdd":  dict(epochs=50,  lr=1e-3, batch_size=2048, patience=7),
    "timesnet":  dict(epochs=30,  lr=1e-3, batch_size=2048, patience=5),
    "dagmm":     dict(epochs=50,  lr=1e-3, batch_size=2048, patience=7),
}


# ══════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════
def run_model(model_name: str, tensor: torch.Tensor,
              epochs: int, lr: float, batch_size: int,
              patience: int, device: torch.device,
              onnx_path: str, scaler_path: str, threshold_path: str,
              full_tensor: torch.Tensor = None):
    # full_tensor: sklearn 모델용 (train/val 분리 전 전체 텐서)
    if full_tensor is None:
        full_tensor = tensor

    print(f"\n{'='*60}")
    print(f"  모델: {model_name.upper()}")
    print(f"  epochs={epochs}  lr={lr}  batch={batch_size}  patience={patience}")
    print(f"{'='*60}")

    train_loader, val_loader = make_loaders(tensor, batch_size)

    # LSTM은 DirectML 미지원 -- CPU 강제
    model_device = torch.device("cpu") if model_name == "lstm" else device

    if model_name == "usad":
        model = USAD(SEQ_LEN, N_FEAT, latent_dim=40, hidden_dim=128).to(model_device)
        train_usad(model, train_loader, val_loader, model_device, epochs, lr, patience)

    elif model_name == "tranad":
        model = TranAD(SEQ_LEN, N_FEAT, d_model=64, nhead=4,
                       num_encoder_layers=1, num_decoder_layers=1,
                       dim_feedforward=128).to(model_device)
        train_tranad(model, train_loader, val_loader, model_device, epochs, lr, patience)

    elif model_name == "conv1d":
        model = Conv1DAE(N_FEAT, hidden_ch=32).to(model_device)
        train_standard(model, train_loader, val_loader, model_device,
                       epochs, lr, patience)

    elif model_name == "lstm":
        # DirectML은 aten::_thnn_fused_lstm_cell 미지원 -- CPU로 학습
        model = LSTMAutoencoder(input_size=N_FEAT, hidden_size=64, num_layers=2).to(model_device)
        train_standard(model, train_loader, val_loader, model_device,
                       epochs, lr, patience)

    elif model_name == "tcn":
        model = TCNAE(N_FEAT, hidden_ch=32, kernel_size=3, dilations=[1, 2, 4]).to(model_device)
        train_standard(model, train_loader, val_loader, model_device,
                       epochs, lr, patience)

    elif model_name == "anomtrans":
        model = AnomalyTransformerAE(SEQ_LEN, N_FEAT, d_model=64, nhead=4,
                                     n_layers=2, dim_ff=128).to(model_device)
        train_anomtrans(model, train_loader, val_loader, model_device, epochs, lr, patience)

    elif model_name == "dcdetect":
        model = DCdetector(SEQ_LEN, N_FEAT, patch_size=2, d_model=64, nhead=4).to(model_device)
        train_standard(model, train_loader, val_loader, model_device,
                       epochs, lr, patience)

    elif model_name == "iforest":
        model = FlattenAE(SEQ_LEN, N_FEAT, hidden_dim=128, latent_dim=32).to(model_device)
        train_sklearn_ae(model_name, model, full_tensor, train_loader, val_loader,
                         model_device, epochs, lr, patience)

    elif model_name == "deepsvdd":
        model = DeepSVDD(SEQ_LEN, N_FEAT, hidden_dim=128, latent_dim=32).to(model_device)
        train_deepsvdd(model, train_loader, val_loader, model_device, epochs, lr, patience)

    elif model_name == "timesnet":
        model = TimesNetAE(SEQ_LEN, N_FEAT, d_model=32, n_ch=16).to(model_device)
        train_standard(model, train_loader, val_loader, model_device, epochs, lr, patience)

    elif model_name == "dagmm":
        model = DAGMM(SEQ_LEN, N_FEAT, latent_dim=16, n_gmm=4).to(model_device)
        train_dagmm(model, train_loader, val_loader, model_device, epochs, lr, patience)

    else:
        raise ValueError(f"Unknown model: {model_name}")

    calc_threshold(model, val_loader, model_device, threshold_path)
    export_onnx(model, model_device, onnx_path)
    return model


def main():
    global OUTPUT_DIR, CACHE_FILE, THRESHOLD_PERCENTILE

    parser = argparse.ArgumentParser(description="AIS 벤치마크 학습 (eval_anomaly.py 호환)")
    parser.add_argument("--model",      type=str, default="usad",
                        help="학습할 모델 (all: 전체 / 콤마 구분 복수: lstm,timesnet,usad)")
    parser.add_argument("--input",      type=str, default=INPUT_FILE)
    parser.add_argument("--epochs",     type=int, default=None)
    parser.add_argument("--lr",         type=float, default=None)
    parser.add_argument("--batch_size", type=int, default=None)
    parser.add_argument("--patience",   type=int, default=None)
    parser.add_argument("--output",     type=str, default=None,
                        help="모델/스케일러/임계값 저장 경로 (기본: D:\\JB-Pirate-King-ML-Results)")
    parser.add_argument("--cache",      type=str, default=None,
                        help="데이터 캐시 파일 경로 (기본: OUTPUT_DIR\\train_data_cache.pt)")
    parser.add_argument("--threshold-pct", type=int, default=None,
                        help="임계값 퍼센타일 (기본 95 → FPR≈5%%, 99 → FPR≈1%%)")
    args = parser.parse_args()

    # 전역 경로/설정 재정의 (scaling 비교 등 별도 실행 시 사용)
    if args.output:
        OUTPUT_DIR = args.output
    if args.cache:
        CACHE_FILE = args.cache
    elif args.output:
        # --output만 지정 시 캐시도 해당 디렉터리에
        CACHE_FILE = os.path.join(args.output, "train_data_cache.pt")
    if args.threshold_pct:
        THRESHOLD_PERCENTILE = args.threshold_pct
        print(f"[임계값] THRESHOLD_PERCENTILE={THRESHOLD_PERCENTILE} (FPR~{100-THRESHOLD_PERCENTILE}%)")

    device = get_best_device()
    print(f"[디바이스] {device}")
    print(f"[피처 수]  {N_FEAT}  |  시퀀스 길이: {SEQ_LEN}")
    print(f"[출력경로] {OUTPUT_DIR}")
    print(f"[캐시경로] {CACHE_FILE}")

    # D드라이브 출력 디렉터리 생성
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    t0 = time.time()
    all_models = ["usad","tranad","conv1d","lstm","tcn","anomtrans","dcdetect",
                  "iforest","deepsvdd","timesnet","dagmm"]
    if args.model == "all":
        models_to_run = all_models
    else:
        models_to_run = [m.strip() for m in args.model.split(",") if m.strip() in all_models]

    total = len(models_to_run)
    notify(f"비지도 학습 시작 -- {total}개 모델: {', '.join(models_to_run)}\n"
           f"SAMPLE_MMSI={SAMPLE_MMSI}  batch=2048  D드라이브 저장",
           "JB-Pirate-King | 비지도 학습 시작")

    # 데이터는 한 번만 로드
    first_scaler = os.path.join(OUTPUT_DIR, f"scaler_{models_to_run[0]}.json")
    tensor = load_and_prepare(args.input, scaler_path=first_scaler)

    done_count = 0
    for idx, name in enumerate(models_to_run):
        onnx_path      = os.path.join(OUTPUT_DIR, f"model_{name}.onnx")
        pt_path        = os.path.join(OUTPUT_DIR, f"model_{name}.pt")
        scaler_path    = os.path.join(OUTPUT_DIR, f"scaler_{name}.json")
        threshold_path = os.path.join(OUTPUT_DIR, f"threshold_{name}.txt")

        if os.path.exists(onnx_path) and os.path.getsize(onnx_path) > 0:
            print(f"\n[스킵] {name} -- 이미 ONNX 완료: {onnx_path}")
            done_count += 1
            continue
        if os.path.exists(pt_path) and os.path.getsize(pt_path) > 0:
            print(f"\n[스킵] {name} -- 이미 PT 완료: {pt_path}")
            done_count += 1
            continue

        d = DEFAULTS[name]
        epochs     = args.epochs     or d["epochs"]
        lr         = args.lr         or d["lr"]
        batch_size = args.batch_size or d["batch_size"]
        patience   = args.patience   or d["patience"]

        if name != models_to_run[0] and os.path.exists(first_scaler):
            shutil.copy(first_scaler, scaler_path)

        elapsed_min = round((time.time() - t0) / 60, 1)
        prog_pct    = round(idx / total * 100)
        notify(f"[{idx+1}/{total}] {name.upper()} 학습 시작\n"
               f"전체 진행: {prog_pct}%  |  경과: {elapsed_min}분",
               f"JB-Pirate-King | {name}")

        try:
            run_model(name, tensor, epochs, lr, batch_size, patience, device,
                      onnx_path, scaler_path, threshold_path, full_tensor=tensor)
            done_count += 1
            elapsed_min = round((time.time() - t0) / 60, 1)
            prog_pct    = round((idx + 1) / total * 100)
            notify(f"[{idx+1}/{total}] {name.upper()} 완료 -- {prog_pct}%\n"
                   f"경과: {elapsed_min}분  |  남은 모델: {total-idx-1}개",
                   f"JB-Pirate-King | {name} 완료")
        except Exception as _e:
            print(f"\n[모델 오류] {name}: {_e.__class__.__name__}: {_e}")
            print(f"  --> {name} 스킵, 다음 모델로 계속 진행")
            notify(f"[오류] {name}: {_e.__class__.__name__} -- 스킵 후 계속",
                   "JB-Pirate-King | 오류")

    total_min = round((time.time() - t0) / 60, 1)
    result_files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".onnx") or f.endswith(".pt")]
    notify(f"비지도 학습 전체 완료!\n"
           f"성공: {done_count}/{total}개  |  총 소요: {total_min}분\n"
           f"저장: {OUTPUT_DIR}",
           "JB-Pirate-King | 전체 완료")

    print(f"\n완료! 전체 소요: {total_min}분")
    print(f"저장 위치: {OUTPUT_DIR}")
    for f in sorted(result_files):
        size = round(os.path.getsize(os.path.join(OUTPUT_DIR, f)) / 1024, 1)
        print(f"  {f}  ({size} KB)")


if __name__ == "__main__":
    main()