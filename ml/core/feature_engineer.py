#!/usr/bin/env python3
"""
피처 엔지니어링 자동화  -  DCdetect Greedy Forward Selection

현재 12개 베이스 피처에서 출발해 후보 파생 피처를 하나씩 추가하며
홀드아웃 탐지율(FP ≈ 1% 기준)이 향상될 때만 채택하는
Greedy Forward Selection을 수행합니다.

사용법:
  python feature_engineer.py \\
    --input  C:\\Users\\imcas\\ais_data\\preprocessed\\2025\\ais_preprocessed_2025.csv \\
    --base_dir C:\\Users\\imcas \\
    --max_mmsi 500 --epochs 5 --n_anom 200

출력:
  콘솔 비교표 + --out_json 으로 결과 JSON 저장 가능
"""

import argparse
import csv as csv_mod
import json
import math
import os
import pickle
import random
import sys
import time
from collections import defaultdict
from datetime import datetime

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, TensorDataset, random_split
from tqdm import tqdm

# ── 경로 설정 (ml/ 폴더 기준) ─────────────────────────────────────
_ML_DIR = os.path.dirname(os.path.abspath(__file__))
if _ML_DIR not in sys.path:
    sys.path.insert(0, _ML_DIR)

# eval_anomaly 는 모듈 레벨에서 argparse 를 호출하므로 argv 임시 교체
_saved_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
try:
    from eval_anomaly import (
        SCENARIO_MAKERS,
        make_normal_seq,
    )
finally:
    sys.argv = _saved_argv

from train_benchmark import (
    DCdetector,
    Conv1DAE,
    LSTMAutoencoder,
    TCNAE,
    make_loaders,
    train_standard,
    SEED,
    VAL_RATIO,
)

# FE 지원 모델 (모두 단일 재구성 AE → 점수 ((out-x)^2).mean() / 제네릭 ONNX export 호환).
# usad/tranad/anomtrans 는 forward 가 튜플 반환 → 점수 로직 변경 필요해 제외.
# iforest/ocsvm 은 sklearn → 제외.
FE_SUPPORTED_MODELS = ["dcdetect", "conv1d", "lstm", "tcn"]

# ── 고정 상수 ──────────────────────────────────────────────────────
SEQ_LEN         = 10
SEQ_BREAK       = 600   # dt 임계값 (초) — 시퀀스 분리
MAX_SEQ_PER_MMSI = 500  # MMSI당 최대 시퀀스 수 (고빈도 선박 편향 방지)
EVAL_NORMAL_RATIO = 0.2  # FP(오탐) 측정용 홀드아웃 정상 시퀀스 비율 (학습 풀에서 분리)

# ── Greedy 목적함수 (약한 시나리오 가중) 기본값 ─────────────────────
# 전체 평균만 최적화하면 90~100%인 다수 시나리오에 묻혀
# 0% 고착 시나리오를 살리는 피처가 영원히 채택되지 않는다.
# 목적 점수 = 전체평균 + WEAK_WEIGHT × (베이스라인 약세 시나리오 평균)
WEAK_FLOOR_DEFAULT  = 50.0   # 베이스라인 탐지율 < 이 값 → "약세" 시나리오로 분류
WEAK_WEIGHT_DEFAULT = 1.0    # 약세 시나리오 평균에 곱할 가중치
MIN_GAIN_DEFAULT    = 3.0    # 목적 점수 채택 임계 (노이즈 오채택 방지)

# ── 베이스 피처 (현재 12개) ────────────────────────────────────────
BASE_FEATURES = [
    "sog", "cog", "heading", "status",
    "dt", "dist_km",
    "cog_hdg_diff", "sog_change", "cog_hdg_change",
    "speed_consistency", "lat_speed", "lon_speed",
]
_B = {name: i for i, name in enumerate(BASE_FEATURES)}

# ── 이전 반복에서 검증·채택된 피처 (Greedy 탐색의 출발점) ──────────────
# Iteration 1: accel (+12.0pp)
# Iteration 2: heading_rate (+3.1pp) → 14피처(nhead=2)
# Iteration 3: 희소피처 클리핑 버그 수정
# Iteration 4: 약세가중 목적함수 + heading_change·vec_sog_diff 채택 → 16피처(89.4%)
#              단, heading_change가 FN2(-39.5)·FN3(-21) 퇴보 유발 의심
# Iteration 5: heading_change 제외 후 재탐색 → heading_change 재선택(16피처 수렴 확정)
#              {accel, heading_rate, vec_sog_diff, heading_change} = 검증된 최적 16피처
# Iteration 6: 16피처 고정, 잔존 약점 FN3-COG경계(~28%)·FN4-status(~8%) 정조준
#              weak_floor 55로 FN3 보호, max_feat 18로 타겟 피처 추가 기회
# Iteration 7: hdg_perp_score·status_fn4_flag 신규 후보 추가, weak_weight=1.5
#              (Iteration 7 결과 따라 INITIAL_EXTRA 갱신 예정)
INITIAL_EXTRA: list = ["accel", "heading_rate", "vec_sog_diff", "heading_change"]


# ── 각도 차이 헬퍼 ────────────────────────────────────────────────
def _ang_diff(a: float, b: float) -> float:
    """두 각도의 절대 차이 [0, 180]"""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


# ── 후보 파생 피처 정의 ────────────────────────────────────────────
# 형식: {name: (설명, fn)}
# fn(seq: List[List[float]], t: int) -> float
#   seq[t] 는 BASE_FEATURES 순서의 현재 행
CANDIDATE_FEATURES: dict = {
    # ── 기존 후보 (Iteration 1 미채택) ──
    "cog_change": (
        "COG 변화량 (도)",
        lambda seq, t: (
            _ang_diff(seq[t][_B["cog"]], seq[t - 1][_B["cog"]]) if t > 0 else 0.0
        ),
    ),
    "heading_change": (
        "Heading 변화량 (도)",
        lambda seq, t: (
            _ang_diff(seq[t][_B["heading"]], seq[t - 1][_B["heading"]]) if t > 0 else 0.0
        ),
    ),
    "turn_rate": (
        "COG 변화율 (도/초)",
        lambda seq, t: (
            _ang_diff(seq[t][_B["cog"]], seq[t - 1][_B["cog"]])
            / max(seq[t][_B["dt"]], 1.0)
            if t > 0
            else 0.0
        ),
    ),
    "heading_rate": (
        "Heading 변화율 (도/초)",
        lambda seq, t: (
            _ang_diff(seq[t][_B["heading"]], seq[t - 1][_B["heading"]])
            / max(seq[t][_B["dt"]], 1.0)
            if t > 0
            else 0.0
        ),
    ),
    "accel": (
        "속도 변화율 (노트/초)",
        lambda seq, t: seq[t][_B["sog_change"]] / max(seq[t][_B["dt"]], 1.0),
    ),
    "dist_speed_err": (
        "거리/속도 불일치 (km)",
        lambda seq, t: abs(
            seq[t][_B["dist_km"]]
            - seq[t][_B["sog"]] * seq[t][_B["dt"]] / 3600.0 * 1.852
        ),
    ),
    "status_change": (
        "상태코드 변화 (0/1)",
        lambda seq, t: (
            float(int(seq[t][_B["status"]]) != int(seq[t - 1][_B["status"]]))
            if t > 0
            else 0.0
        ),
    ),
    "anchor_suspicion": (
        "정박의심 (저속+Heading변화)",
        lambda seq, t: (
            float(seq[t][_B["sog"]] < 0.5
                  and _ang_diff(seq[t][_B["heading"]], seq[t - 1][_B["heading"]]) > 15)
            if t > 0 else 0.0
        ),
    ),
    "cog_move_diff": (
        "COG vs 실이동방향 차이 (도)",
        lambda seq, t: _ang_diff(
            seq[t][_B["cog"]],
            math.degrees(math.atan2(seq[t][_B["lon_speed"]], seq[t][_B["lat_speed"]])) % 360
        ) if (abs(seq[t][_B["lat_speed"]]) + abs(seq[t][_B["lon_speed"]]) > 1e-6) else 0.0,
    ),
    "speed_ratio": (
        "상대 속도 변화율 (변화/현재속도)",
        lambda seq, t: abs(seq[t][_B["sog_change"]]) / max(seq[t][_B["sog"]], 0.5),
    ),
    "dist_speed_ratio": (
        "거리/속도 비율 (차이 대신 비율)",
        lambda seq, t: seq[t][_B["dist_km"]] / max(
            seq[t][_B["sog"]] * seq[t][_B["dt"]] / 3600.0 * 1.852, 0.001
        ),
    ),
    # ── Iteration 2 신규 후보 ──────────────────────────────────────
    "hdg_vec_diff": (
        "Heading vs GPS이동방향 차이 (도)",
        # lat_speed/lon_speed로 실제 이동 방위각 추정 → heading과 비교
        # G3-PhantomHDG 퇴보(-36pp) 복구 타겟
        lambda seq, t: _ang_diff(
            seq[t][_B["heading"]],
            math.degrees(math.atan2(
                seq[t][_B["lon_speed"]], seq[t][_B["lat_speed"]]
            )) % 360,
        ) if (abs(seq[t][_B["lat_speed"]]) + abs(seq[t][_B["lon_speed"]]) > 1e-6) else 0.0,
    ),
    "status_motion": (
        "정박/계류 상태이동 (anchored×sog)",
        # status 1(anchor)·5(moored)이면서 sog>0 → 충돌 신호
        # 정박이동·D1-LowSlow 0% 탐지 타겟
        lambda seq, t: float(int(round(seq[t][_B["status"]])) in (1, 5))
        * seq[t][_B["sog"]],
    ),
    "sog_vec_kn": (
        "GPS유도 속력 (노트)",
        # lat_speed·lon_speed(deg/s) → km/s 변환 후 노트로
        # 1 deg ≈ 111.32 km,  1 knot = 1.852 km/h
        lambda seq, t: math.sqrt(
            (seq[t][_B["lat_speed"]] * 111320.0) ** 2
            + (seq[t][_B["lon_speed"]] * 111320.0) ** 2
        ) / 1852.0 * 3600.0,
    ),
    "vec_sog_diff": (
        "SOG vs GPS속력 절대차이 (노트)",
        lambda seq, t: abs(
            seq[t][_B["sog"]]
            - math.sqrt(
                (seq[t][_B["lat_speed"]] * 111320.0) ** 2
                + (seq[t][_B["lon_speed"]] * 111320.0) ** 2
            ) / 1852.0 * 3600.0
        ),
    ),
    # ── Iteration 4 신규 후보 (0% 고착 시나리오 타겟, 노이즈 플로어/도메인 기반) ──
    "anchored_excess_speed": (
        "정박/계류 중 초과속력 (status∈{1,5,6} × max(0, sog-1.5kn))",
        # 정박이동 타겟. 1.5kn = GPS 지터 허용 임계(해양 관행) → 정상 정박선은 0
        lambda seq, t: float(int(round(seq[t][_B["status"]])) in (1, 5, 6))
        * max(0.0, seq[t][_B["sog"]] - 1.5),
    ),
    "uncommon_status": (
        "비통상 항행상태 (0=underway/1=anchored/5=moored 외 = 1)",
        # FN4-status 타겟. 통상 3개 상태 외는 운용상 드묾(도메인 지식, 공격값 비하드코딩)
        lambda seq, t: 0.0 if int(round(seq[t][_B["status"]])) in (0, 1, 5) else 1.0,
    ),
    "lowspeed_crab": (
        "저속 crab각 (cog_hdg_diff × max(0, 1-sog/3kn))",
        # D1-LowSlow 타겟. 저속일수록 heading-course 불일치 가중 (3kn 이하)
        lambda seq, t: seq[t][_B["cog_hdg_diff"]]
        * max(0.0, 1.0 - seq[t][_B["sog"]] / 3.0),
    ),
    # ── Iteration 7 신규 후보 (FN3 정조준) ────────────────────────────
    "hdg_perp_score": (
        "COG-Heading 직교성 (|sin(cog_hdg_diff)|, FN3 정조준)",
        # FN3-COG경界: 헤딩이 진침로에 수직(91~99°) → sin값 ≈ 1.0 (최대)
        # 정상선박: cog_hdg_diff < 30° → sin < 0.5  |  반대방향(180°): sin = 0
        # cog_hdg_diff가 이미 있지만 선형 스케일에서 90° 구분력이 낮음 →
        # sin 변환으로 90°에서 극대, 0°/180° 에서 0으로 비선형 강조
        # cog_hdg_diff = -1.0 은 heading 불가(511) 센티넬 → 0.0 반환
        lambda seq, t: 0.0 if seq[t][_B["cog_hdg_diff"]] < 0.0 else abs(math.sin(math.radians(seq[t][_B["cog_hdg_diff"]]))),
    ),
    "status_fn4_flag": (
        "FN4 비통상 상태 누적 (시퀀스 내 uncommon_status 비율)",
        # FN4-status: status∈{2,3,7,8,11,12} 가 SEQ_LEN 내내 지속 → 비율=1.0
        # 정상: status=0/1/5만 → 비율=0.0
        # uncommon_status(단일 타임스텝) 보다 시퀀스 맥락 반영
        lambda seq, t: sum(
            1.0 for row in seq if int(round(row[_B["status"]])) not in (0, 1, 5)
        ) / len(seq),
    ),
}


# ── 편향 제거용 스케일러 ─────────────────────────────────────────
class ClippedMinMaxScaler:
    """1~99 퍼센타일 클리핑 후 MinMaxScaling.

    - 이상치가 전체 스케일을 왜곡하는 MinMaxScaler 단점 보완
    - data_min_ / data_max_ 속성을 그대로 노출해 기존 코드 호환 유지
    - 학습·평가 transform 모두 동일하게 클리핑(대칭) → 모델은 클리핑된
      분포를 학습하고, 탐지는 클리핑 범위 내 패턴 재구성 오차로 이뤄짐.
      (참고: 평가 시 이상치를 클리핑하지 않으면 범위 밖 값이 [0,1]을 초과해
       재구성 오차를 키울 수 있으나, 현재는 학습/평가 일관성을 우선함)

    희소 피처 보호:
      status_motion 처럼 정상 데이터의 99%가 0인 희소 피처는
      99퍼센타일(clip_hi)이 0이 되어 클리핑이 신호 전체를 죽인다.
      clip_lo ≈ clip_hi(붕괴) 열은 클리핑을 끄고 실제 min/max를 사용해
      희소 이상신호를 보존한다.
    """
    def __init__(self):
        self._scaler  = MinMaxScaler()
        self.clip_lo  = None   # 1 퍼센타일 (붕괴열은 실제 min)
        self.clip_hi  = None   # 99 퍼센타일 (붕괴열은 실제 max)

    def fit(self, X: np.ndarray):
        X = np.asarray(X, dtype=float)
        lo = np.percentile(X, 1,  axis=0)
        hi = np.percentile(X, 99, axis=0)
        # 붕괴 열(클리핑 구간이 0폭) → 실제 min/max로 대체해 클리핑 비활성화
        degenerate = (hi - lo) <= 1e-9
        if np.any(degenerate):
            real_lo = X.min(axis=0)
            real_hi = X.max(axis=0)
            lo = np.where(degenerate, real_lo, lo)
            hi = np.where(degenerate, real_hi, hi)
        self.clip_lo = lo
        self.clip_hi = hi
        self._scaler.fit(np.clip(X, self.clip_lo, self.clip_hi))
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return self._scaler.transform(np.clip(np.asarray(X), self.clip_lo, self.clip_hi))

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)

    # ── MinMaxScaler 인터페이스 호환 ──
    @property
    def data_min_(self):  return self._scaler.data_min_
    @property
    def data_max_(self):  return self._scaler.data_max_


# ── 시퀀스 파생 피처 보강 ─────────────────────────────────────────
def augment_seq(seq: list, extra_names: list) -> list:
    """단일 시퀀스에 파생 피처 열 추가. 원본 seq 는 BASE_FEATURES 순서."""
    result = []
    for t, row in enumerate(seq):
        new_row = list(row)
        for name in extra_names:
            _, fn = CANDIDATE_FEATURES[name]
            new_row.append(fn(seq, t))
        result.append(new_row)
    return result


def augment_seqs(seqs: list, extra_names: list) -> list:
    if not extra_names:
        return seqs
    return [augment_seq(seq, extra_names) for seq in seqs]


# ── 데이터 로드 (편향 제거) ────────────────────────────────────────
def load_raw_seqs(input_file: str, max_mmsi: int,
                  eval_ratio: float = EVAL_NORMAL_RATIO) -> tuple:
    """전처리 CSV → (train_seqs, eval_normal_seqs) 반환.

    편향 제거:
    1. 연·월별 균등 MMSI 샘플링 — 연도/계절 편향 방지 (YYYY-MM 버킷)
    2. MMSI당 시퀀스 상한       — 고빈도 선박 지배 방지
    3. 홀드아웃 정상셋          — FP(오탐) 측정용 정상 시퀀스를 MMSI 단위로 학습과 분리.
       eval_ratio 만큼을 '월별 균등하게' 떼어내 학습에 한 번도 쓰지 않음(누수 방지).
    (스케일러 편향은 ClippedMinMaxScaler 에서 처리)
    """
    # ── 시퀀스 캐시 (반복 iteration 간 대용량 재파싱 방지) ──────────────
    # 키: 입력파일 + max_mmsi + eval_ratio + SEED + 시퀀스 파라미터 (결정적)
    cache_path = (f"{input_file}.s{max_mmsi}_seed{SEED}_ev{eval_ratio}"
                  f"_L{SEQ_LEN}_b{SEQ_BREAK}_c{MAX_SEQ_PER_MMSI}.holdout.pkl")
    if (os.path.exists(cache_path)
            and os.path.getmtime(cache_path) >= os.path.getmtime(input_file)):
        print(f"\n[데이터] 시퀀스 캐시 로드: {cache_path}")
        try:
            with open(cache_path, "rb") as cf:
                train_seqs, eval_seqs = pickle.load(cf)
            print(f"  학습 정상 {len(train_seqs):,} / FP측정 정상 {len(eval_seqs):,} (캐시 적중)")
            return train_seqs, eval_seqs
        except Exception as e:
            print(f"  [캐시 로드 실패 → 원본 재파싱] {e}")

    print(f"\n[데이터] {input_file} 로드 중...")

    mmsi_data:        dict = defaultdict(list)   # mmsi → [record, ...]
    mmsi_period_cnt:  dict = defaultdict(lambda: defaultdict(int))  # mmsi → {"YYYY-MM": cnt}

    with open(input_file, encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            mmsi = row.get("mmsi", "")
            if not mmsi:
                continue
            try:
                record = [float(row[col]) for col in BASE_FEATURES]
            except (ValueError, KeyError):
                continue
            mmsi_data[mmsi].append(record)
            # 연-월 추출 (base_date_time: "YYYY-MM-DD HH:MM:SS")
            dt_str = row.get("base_date_time", "")
            if len(dt_str) >= 7 and dt_str[4] == "-":
                mmsi_period_cnt[mmsi][dt_str[0:7]] += 1   # "YYYY-MM"

    all_mmsis = list(mmsi_data.keys())
    print(f"  고유 MMSI: {len(all_mmsis):,}")

    # MMSI별 주요 기간 (최빈 YYYY-MM) — 균등 샘플링/홀드아웃 분리에 공통 사용
    mmsi_main_period = {
        m: (max(cnt, key=cnt.get) if cnt else "")
        for m, cnt in mmsi_period_cnt.items()
    }

    # ── 1. 연·월별 균등 MMSI 샘플링 ───────────────────────────────
    if max_mmsi and len(all_mmsis) > max_mmsi:
        period_buckets: dict = defaultdict(list)
        for mmsi in all_mmsis:
            period_buckets[mmsi_main_period.get(mmsi, "")].append(mmsi)

        active_periods = sorted(p for p in period_buckets if p)
        if active_periods:
            per_period = max(1, max_mmsi // len(active_periods))
            selected: list = []
            for p in active_periods:
                pool = period_buckets[p]
                selected.extend(random.sample(pool, min(per_period, len(pool))))
            # 부족분 랜덤 보충
            if len(selected) < max_mmsi:
                leftover = [m for m in all_mmsis if m not in set(selected)]
                extra    = min(max_mmsi - len(selected), len(leftover))
                selected.extend(random.sample(leftover, extra))
            selected = selected[:max_mmsi]
            dist = defaultdict(int)
            for m in selected:
                dist[mmsi_main_period.get(m, "")] += 1
            dist_str = "  ".join(
                f"{p}:{c}" for p, c in sorted(dist.items()) if p
            )
            print(f"  연·월별 균등 샘플: {len(selected):,}개 MMSI  "
                  f"({len(active_periods)}개 기간)  [{dist_str}]")
        else:
            selected = random.sample(all_mmsis, max_mmsi)
            print(f"  랜덤 샘플: {len(selected):,}개 MMSI")
    else:
        selected = all_mmsis

    # ── 2. 홀드아웃 정상셋 분리 (월별 균등하게 eval_ratio 만큼) ──────────
    # 각 YYYY-MM 버킷에서 eval_ratio 비율씩 떼어내 학습/FP측정 모두 월 균등 유지.
    split_rng = random.Random(SEED + 777)
    eval_set: set = set()
    sel_buckets: dict = defaultdict(list)
    for m in selected:
        sel_buckets[mmsi_main_period.get(m, "")].append(m)
    for p, ms in sel_buckets.items():
        k = int(round(len(ms) * eval_ratio))
        k = min(k, len(ms) - 1) if len(ms) > 1 else 0   # 월별 최소 1개는 학습에
        if k > 0:
            eval_set.update(split_rng.sample(ms, k))
    train_mmsis = [m for m in selected if m not in eval_set]
    eval_mmsis  = [m for m in selected if m in eval_set]
    print(f"  홀드아웃: 학습 {len(train_mmsis):,} MMSI / FP측정 {len(eval_mmsis):,} MMSI "
          f"(eval_ratio={eval_ratio}, 월별 균등)")

    # ── 3. 시퀀스 생성 + MMSI당 상한 (MMSI 집합별로 독립 생성) ──────────
    dt_idx = _B["dt"]

    def _build(mmsi_keys: list) -> tuple:
        seqs: list = []
        capped = 0
        for m in mmsi_keys:
            records = mmsi_data[m]
            seg, cur = [], [records[0]]
            for rec in records[1:]:
                if rec[dt_idx] >= SEQ_BREAK:
                    seg.append(cur); cur = [rec]
                else:
                    cur.append(rec)
            seg.append(cur)
            mmsi_seqs: list = []
            for s in seg:
                if len(s) < SEQ_LEN:
                    continue
                for i in range(len(s) - SEQ_LEN + 1):
                    mmsi_seqs.append(s[i: i + SEQ_LEN])
            if len(mmsi_seqs) > MAX_SEQ_PER_MMSI:
                mmsi_seqs = random.sample(mmsi_seqs, MAX_SEQ_PER_MMSI)
                capped += 1
            seqs.extend(mmsi_seqs)
        return seqs, capped

    train_seqs, cap_tr = _build(train_mmsis)
    eval_seqs,  cap_ev = _build(eval_mmsis)
    print(f"  시퀀스: 학습 {len(train_seqs):,} (상한적용 {cap_tr}) / "
          f"FP측정 {len(eval_seqs):,} (상한적용 {cap_ev})")

    # 다음 iteration을 위해 캐시 저장 (실패해도 진행에 영향 없음)
    try:
        with open(cache_path, "wb") as cf:
            pickle.dump((train_seqs, eval_seqs), cf, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  시퀀스 캐시 저장: {cache_path}")
    except Exception as e:
        print(f"  [캐시 저장 실패] {e}")

    return train_seqs, eval_seqs


# ── Tensor 준비 ────────────────────────────────────────────────────
def prepare_tensor(raw_seqs: list, extra_names: list):
    """파생 피처 추가 → ClippedMinMaxScaling → Tensor. (tensor, scaler) 반환.

    ClippedMinMaxScaler: 1~99 퍼센타일로 이상치 클리핑 후 MinMaxScale.
    이상치 1개가 전체 스케일을 망치는 MinMaxScaler 단점 해결.
    """
    aug  = augment_seqs(raw_seqs, extra_names)
    flat = np.array([row for seq in aug for row in seq])
    scaler = ClippedMinMaxScaler()
    scaler.fit(flat)
    scaled = [scaler.transform(np.array(seq)).tolist() for seq in aug]
    tensor = torch.tensor(scaled, dtype=torch.float32)
    return tensor, scaler


# ── DCdetector 학습 ───────────────────────────────────────────────
def _build_model(model_name: str, n_feat: int, d_model: int = 64, nhead_max: int = 8):
    """모델명 → nn.Module 생성. 모두 (B, SEQ_LEN, n_feat) → 동형 재구성 출력.

    DCdetector 만 nhead 자동 조정 필요 (n_feat·d_model 공통 약수 중 최댓값).
    conv1d/lstm/tcn 은 채널 수만 받으므로 n_feat 만 전달."""
    if model_name == "dcdetect":
        nhead = max(h for h in range(1, nhead_max + 1)
                    if n_feat % h == 0 and d_model % h == 0)
        return DCdetector(SEQ_LEN, n_feat, patch_size=2, d_model=d_model, nhead=nhead)
    if model_name == "conv1d":
        return Conv1DAE(n_feat, hidden_ch=32)
    if model_name == "lstm":
        return LSTMAutoencoder(input_size=n_feat, hidden_size=64, num_layers=2)
    if model_name == "tcn":
        return TCNAE(n_feat, hidden_ch=32, kernel_size=3, dilations=[1, 2, 4])
    raise ValueError(f"FE 미지원 모델: {model_name} (지원: {FE_SUPPORTED_MODELS})")


def train_recon_model(
    model_name: str,
    tensor: torch.Tensor,
    n_feat: int,
    epochs: int,
    lr: float = 1e-3,
    batch_size: int = 256,
    patience: int = 5,
):
    """재구성 AE 학습 후 (model, val_loader) 반환. model_name 으로 아키텍처 분기.
    학습 루프(train_standard)·로더·점수·export 는 모델 공통 (설계 무변경).

    호출마다 고정 SEED 로 재시드 → 모든 후보가 동일 init 에서 학습되어
    Greedy 비교가 공정(학습 노이즈 제거)하고, 채택 후 재학습 점수가 스캔 점수와
    재현됨. (재시드 없으면 RNG 드리프트로 winner's curse — 운 좋은 후보를 채택한
    뒤 재학습 시 회귀해 탐지율이 하락함.)"""
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    device = torch.device("cpu")
    model = _build_model(model_name, n_feat).to(device)
    train_loader, val_loader = make_loaders(tensor, batch_size)
    train_standard(model, train_loader, val_loader, device, epochs, lr, patience)
    return model, val_loader


# ── 임계값 계산 (실제 정상 시퀀스 기준 목표 FP) ──────────────────────
def compute_fp_threshold(model, scaler, extra_names: list, raw_seqs: list,
                         fp_pct: float = 1.0, n_normal: int = 10000) -> float:
    """실제 정상 시퀀스 점수의 (100-fp_pct) 퍼센타일 = 목표 FP 임계값.
    evaluate()의 fp1_thr 와 동일 산식 → 배포 threshold가 평가 탐지율과 같은 FP 기준."""
    device = next(model.parameters()).device
    model.eval()
    mins        = scaler.data_min_
    scale_range = scaler.data_max_ - mins
    clip_lo = getattr(scaler, "clip_lo", None)
    clip_hi = getattr(scaler, "clip_hi", None)

    def _score(seq):
        aug = augment_seq(seq, extra_names)
        rows = []
        for row in aug:
            arr = np.asarray(row)
            if clip_lo is not None:
                arr = np.clip(arr, clip_lo, clip_hi)
            rows.append([(v - mn) / (rng + 1e-9)
                         for v, mn, rng in zip(arr.tolist(), mins, scale_range)])
        x = torch.tensor(rows, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            out = model(x)
            return float(((out - x) ** 2).mean())

    if raw_seqs:
        sample = random.sample(raw_seqs, min(n_normal, len(raw_seqs)))
    else:
        sample = [make_normal_seq() for _ in range(n_normal)]
    scores = [_score(s) for s in sample]
    return float(np.percentile(scores, 100 - fp_pct))


# ── 평가 ────────────────────────────────────────────────────────────
def evaluate(
    model,
    scaler,
    extra_names: list,
    n_anom: int = 200,
    n_normal: int = 10000,
    raw_seqs: list = None,
    extra_fp: tuple = (),   # 추가로 계산할 FP% 목록 ex) (5.0, 10.0)
):
    """
    FP ≈ 1% 기준 탐지율 계산.
    반환: (avg_det_fp1, scenario_results, extra_results, fp1_thr)
      extra_results: {fp_pct: {sc_name: det}} — extra_fp 지정 시 채워짐
      fp1_thr: FP=1% 임계값 (정상 점수 99퍼센타일)
    """
    device = next(model.parameters()).device
    model.eval()
    mins        = scaler.data_min_
    maxs        = scaler.data_max_
    scale_range = maxs - mins
    clip_lo = getattr(scaler, "clip_lo", None)
    clip_hi = getattr(scaler, "clip_hi", None)

    def _scale_row(row):
        arr = np.asarray(row)
        if clip_lo is not None:
            arr = np.clip(arr, clip_lo, clip_hi)
        return [(v - mn) / (rng + 1e-9)
                for v, mn, rng in zip(arr.tolist(), mins, scale_range)]

    def _score(seq: list) -> float:
        aug = augment_seq(seq, extra_names)
        scaled = [_scale_row(row) for row in aug]
        x = torch.tensor(scaled, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            out = model(x)
            return float(((out - x) ** 2).mean())

    if raw_seqs:
        normal_raw = random.sample(raw_seqs, min(n_normal, len(raw_seqs)))
    else:
        normal_raw = [make_normal_seq() for _ in range(n_normal)]

    normal_scores = [_score(seq) for seq in normal_raw]
    fp1_thr = float(np.percentile(normal_scores, 99))

    # extra_fp 임계값 사전 계산
    extra_thrs = {fp: float(np.percentile(normal_scores, 100 - fp)) for fp in extra_fp}

    anom_scenarios = [
        (name, maker, is_holdout)
        for name, maker, is_anom, is_holdout in SCENARIO_MAKERS
        if is_anom
    ]

    all_dets = []
    scenario_results = []
    # extra_fp별 결과 수집 {fp: {sc_name: det}}
    extra_results: dict = {fp: {} for fp in extra_fp}

    for sc_name, maker, is_holdout in tqdm(anom_scenarios, desc="  시나리오 평가", leave=False):
        anom_seqs   = [maker() for _ in range(n_anom)]
        anom_scores = [_score(seq) for seq in anom_seqs]
        det = sum(1 for s in anom_scores if s > fp1_thr) / len(anom_scores) * 100.0
        all_dets.append(det)
        scenario_results.append((sc_name, det, is_holdout))
        for fp, thr in extra_thrs.items():
            extra_results[fp][sc_name] = (
                sum(1 for s in anom_scores if s > thr) / len(anom_scores) * 100.0
            )

    # extra_fp 출력
    if extra_fp:
        W = 52
        for fp in sorted(extra_fp):
            sc_dets = extra_results[fp]
            avg = float(np.mean(list(sc_dets.values())))
            print(f"\n  [FP≈{fp:.0f}%] 평균 탐지율 {avg:.1f}%")
            for sc_name, det in sorted(sc_dets.items(), key=lambda x: x[1]):
                mark = "❌" if det < 50 else "✅"
                print(f"  {mark} {sc_name:<28} {det:>6.1f}%")

    return float(np.mean(all_dets)), scenario_results, extra_results, fp1_thr


# ── Permutation Importance ────────────────────────────────────────
def permutation_importance(
    model,
    scaler,
    extra_names: list,
    raw_seqs: list,
    n_anom: int = 200,
    n_normal: int = 10000,
    n_repeat: int = 3,
) -> list:
    """
    학습된 모델로 피처별 순열 중요도 계산.
    각 피처를 랜덤 셔플 → 탐지율 하락량 = 중요도.
    반환: [(feat_name, base_det, shuffled_det, importance), ...] 내림차순
    """
    device = next(model.parameters()).device
    model.eval()
    mins        = scaler.data_min_
    maxs        = scaler.data_max_
    scale_range = maxs - mins
    clip_lo     = getattr(scaler, "clip_lo", None)
    clip_hi     = getattr(scaler, "clip_hi", None)
    all_feat_names = BASE_FEATURES + extra_names

    def _scale_row(row):
        arr = np.asarray(row)
        if clip_lo is not None:
            arr = np.clip(arr, clip_lo, clip_hi)
        return [(v - mn) / (rng + 1e-9)
                for v, mn, rng in zip(arr.tolist(), mins, scale_range)]

    def _score(seq: list) -> float:
        aug = augment_seq(seq, extra_names)
        scaled = [_scale_row(row) for row in aug]
        x = torch.tensor(scaled, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            out = model(x)
            return float(((out - x) ** 2).mean())

    # 기준 탐지율
    normal_raw  = random.sample(raw_seqs, min(n_normal, len(raw_seqs)))
    normal_scores = [_score(seq) for seq in normal_raw]
    fp1_thr     = float(np.percentile(normal_scores, 99))

    anom_scenarios = [(name, maker) for name, maker, is_anom, _ in SCENARIO_MAKERS if is_anom]
    anom_seqs_all  = {name: [maker() for _ in range(n_anom)] for name, maker in anom_scenarios}

    def _det_rate(score_fn):
        dets = []
        for name, _ in anom_scenarios:
            scores = [score_fn(seq) for seq in anom_seqs_all[name]]
            dets.append(sum(1 for s in scores if s > fp1_thr) / len(scores) * 100.0)
        return float(np.mean(dets))

    # 이상 시퀀스 스케일링 미리 계산 (시퀀스 간 셔플을 위해)
    anom_scaled_all: dict = {}
    for sc_name, _ in anom_scenarios:
        seqs = anom_seqs_all[sc_name]
        anom_scaled_all[sc_name] = [
            [_scale_row(row) for row in augment_seq(seq, extra_names)]
            for seq in seqs
        ]

    def _det_rate_scaled(scaled_dict):
        """스케일된 시퀀스 딕셔너리로 탐지율 계산"""
        dets = []
        for sc_name, _ in anom_scenarios:
            cnt = 0
            for scaled in scaled_dict[sc_name]:
                x = torch.tensor(scaled, dtype=torch.float32).unsqueeze(0).to(device)
                with torch.no_grad():
                    out = model(x)
                    score = float(((out - x) ** 2).mean())
                if score > fp1_thr:
                    cnt += 1
            dets.append(cnt / len(scaled_dict[sc_name]) * 100.0)
        return float(np.mean(dets))

    print("\n[피처 중요도] Permutation Importance 계산 중 (시퀀스 간 셔플)...")
    base_det = _det_rate_scaled(anom_scaled_all)
    print(f"  기준 탐지율: {base_det:.1f}%")

    results = []
    for fi, feat in enumerate(tqdm(all_feat_names, desc="  피처 순열", leave=False)):
        drop_sum = 0.0
        for _ in range(n_repeat):
            # fi번 열을 시퀀스 간 셔플 (각 timestep마다 독립적으로)
            shuffled = {
                sc_name: [list(map(list, s)) for s in seqs]
                for sc_name, seqs in anom_scaled_all.items()
            }
            for t in range(SEQ_LEN):
                # 모든 시나리오의 t번 timestep fi번 피처값 모아서 셔플
                pool = []
                keys = []
                for sc_name in shuffled:
                    for si, seq in enumerate(shuffled[sc_name]):
                        pool.append(seq[t][fi])
                        keys.append((sc_name, si, t))
                random.shuffle(pool)
                for (sc_name, si, t_), val in zip(keys, pool):
                    shuffled[sc_name][si][t_][fi] = val

            drop_sum += base_det - _det_rate_scaled(shuffled)

        importance = drop_sum / n_repeat
        results.append((feat, base_det, importance))
        print(f"  {feat:<25s}  중요도: {importance:+.2f}pp")

    results.sort(key=lambda x: x[2], reverse=True)
    return results


# ── Greedy 목적 점수 (전체평균 + 약세 시나리오 가중) ────────────────
def _objective(sc: list, weak_names: set, weak_weight: float) -> float:
    """목적 점수 = 전체 시나리오 평균 + weak_weight × 약세 시나리오 평균.

    sc: [(name, det, is_holdout), ...]
    weak_names: 베이스라인에서 약세로 분류된 시나리오 이름 집합 (고정)
    """
    overall = float(np.mean([d for _, d, _ in sc])) if sc else 0.0
    if not weak_names:
        return overall
    weak_dets = [d for n, d, _ in sc if n in weak_names]
    weak_mean = float(np.mean(weak_dets)) if weak_dets else 0.0
    return overall + weak_weight * weak_mean


# ── Greedy Forward Selection 메인 루프 ────────────────────────────
def greedy_forward_selection(train_seqs: list, eval_seqs: list, args) -> tuple:
    weak_floor  = getattr(args, "weak_floor",  WEAK_FLOOR_DEFAULT)
    weak_weight = getattr(args, "weak_weight", WEAK_WEIGHT_DEFAULT)
    min_gain    = getattr(args, "min_gain",    MIN_GAIN_DEFAULT)

    # INITIAL_EXTRA: 이전 반복에서 이미 채택된 피처 → 탐색 시작점
    current_extra: list = list(INITIAL_EXTRA)
    # accel 등 INITIAL_EXTRA에 포함된 항목은 다시 탐색하지 않음
    remaining: list = [k for k in CANDIDATE_FEATURES.keys() if k not in INITIAL_EXTRA]
    # 후보 수 제한 (--max_candidates): 정의 순서대로 앞 N개만 탐색 (속도)
    max_cand = getattr(args, "max_candidates", None)
    if max_cand is not None and max_cand > 0:
        remaining = remaining[:max_cand]
    history: list = []

    n_base_total = len(BASE_FEATURES) + len(current_extra)
    W = 65
    print("\n" + "=" * W)
    print("  피처 엔지니어링 자동화  (Greedy Forward Selection)")
    print(f"  베이스 피처: {len(BASE_FEATURES)}개  +  기채택: {len(INITIAL_EXTRA)}개"
          f"  →  시작 {n_base_total}개  |  후보: {len(remaining)}개")
    print(f"  epochs={args.epochs}  max_mmsi={args.max_mmsi}  n_anom={args.n_anom}")
    print(f"  목적함수: 전체평균 + {weak_weight}×약세평균"
          f"  (약세<{weak_floor}%, 채택임계 {min_gain}점)")
    print("=" * W)

    # ── 베이스라인 (INITIAL_EXTRA 포함) ────────────────────────────
    print(f"\n{'─'*W}")
    print(f"[베이스라인]  피처 {n_base_total}개: {BASE_FEATURES + current_extra}")
    print(f"{'─'*W}")
    t0 = time.time()
    tensor, scaler = prepare_tensor(train_seqs, current_extra)
    model, val_loader = train_recon_model(args.model, tensor, n_base_total, args.epochs)
    det0, sc0, _, _ = evaluate(model, scaler, current_extra, args.n_anom,
                               raw_seqs=eval_seqs, extra_fp=(5.0, 10.0))
    elapsed = time.time() - t0

    # 약세 시나리오 집합 = 베이스라인 탐지율 < weak_floor (전 과정 고정)
    weak_names = {n for n, d, _ in sc0 if d < weak_floor}
    best_score = _objective(sc0, weak_names, weak_weight)
    best_det   = det0
    print(f"  → 전체 평균 탐지율 {det0:.1f}%  (목적점수 {best_score:.1f})  [{elapsed/60:.1f}분]")
    if weak_names:
        weak_str = ", ".join(f"{n}({d:.0f}%)" for n, d, _ in sc0 if n in weak_names)
        print(f"  약세 시나리오({len(weak_names)}개): {weak_str}")
    history.append(
        dict(step=0, added="베이스라인", n_feat=n_base_total,
             det=det0, score=best_score, extra=list(current_extra), scenarios=sc0)
    )

    max_feat  = getattr(args, "max_feat",  None)
    max_steps = getattr(args, "max_steps", None)  # None = 수렴까지 전체 실행

    step = 1
    while remaining:
        # 피처 수 상한 도달 시 중단 (nhead=8 유지 목적: 16개 권장)
        if max_feat is not None and (len(BASE_FEATURES) + len(current_extra)) >= max_feat:
            print(f"\n  [상한] 피처 수 {max_feat}개 도달 → 탐색 종료")
            break
        print(f"\n{'─'*W}")
        print(f"[Step {step}]  현재 {len(BASE_FEATURES)+len(current_extra)}개 피처  "
              f"| 후보 {len(remaining)}개")
        if current_extra:
            adopted_new = [f for f in current_extra if f not in INITIAL_EXTRA]
            if adopted_new:
                print(f"  이번 반복 채택: {adopted_new}")
        print(f"{'─'*W}")

        step_best_gain = -999.0
        step_best_feat = None
        step_best_result: tuple = ()

        for cand in remaining:
            desc, _ = CANDIDATE_FEATURES[cand]
            trial_extra = current_extra + [cand]
            n_feat = len(BASE_FEATURES) + len(trial_extra)
            print(f"  + {cand:<20s}  ({desc})  →  {n_feat}개 학습 중...",
                  end="", flush=True)
            t0 = time.time()
            tensor, scaler = prepare_tensor(train_seqs, trial_extra)
            model, val_loader = train_recon_model(args.model, tensor, n_feat, args.epochs)
            det, sc, _, _ = evaluate(model, scaler, trial_extra, args.n_anom, raw_seqs=eval_seqs)
            elapsed = time.time() - t0
            score = _objective(sc, weak_names, weak_weight)
            gain  = score - best_score          # 목적 점수 기준 향상
            det_gain = det - best_det           # 참고용 전체평균 변화
            arrow = "▲" if gain > min_gain else ("▼" if gain < -min_gain else "─")
            print(
                f"  전체평균 {det:5.1f}%({det_gain:+.1f})"
                f"  점수 {score:6.1f} {arrow}{gain:+5.1f}  [{elapsed/60:.1f}분]"
            )
            if gain > step_best_gain:
                step_best_gain = gain
                step_best_feat = cand
                step_best_result = (det, trial_extra, tensor, scaler, model, sc, score)

        # 목적 점수 min_gain 이상 향상 시 채택
        if step_best_gain > min_gain:
            current_extra = step_best_result[1]
            best_det   = step_best_result[0]
            best_score = step_best_result[6]
            remaining.remove(step_best_feat)
            desc, _ = CANDIDATE_FEATURES[step_best_feat]
            print(f"\n  ✓ 채택: [{step_best_feat}] ({desc})")
            print(f"    전체평균 {history[-1]['det']:.1f}% → {best_det:.1f}%"
                  f"  |  목적점수 +{step_best_gain:.1f}")
            history.append(
                dict(step=step, added=step_best_feat,
                     n_feat=len(BASE_FEATURES) + len(current_extra),
                     det=best_det, score=best_score,
                     extra=list(current_extra),
                     scenarios=step_best_result[5])
            )
        else:
            best_cand = step_best_feat
            print(f"\n  ✗ 개선 없음 (최고 후보: {best_cand}  목적점수 {step_best_gain:+.1f})"
                  f" → 종료")
            break

        if max_steps is not None and step >= max_steps:
            print(f"\n  [max_steps={max_steps}] 스텝 제한 도달 → 종료")
            break

        step += 1

    return history, current_extra


# ── 보고서 출력 ───────────────────────────────────────────────────
def print_report(history: list):
    W = 65
    best = max(history, key=lambda x: x.get("score", x["det"]))
    base = history[0]
    gain = best["det"] - base["det"]

    print("\n" + "=" * W)
    print("  최종 결과 요약")
    print("=" * W)
    print(f"  {'Step':>4}  {'추가 피처':<22}  {'N피처':>5}  {'탐지율(전체평균)':>14}")
    print(f"  {'─'*55}")
    for r in history:
        gain_str = ""
        if r["step"] > 0:
            gain_r = r["det"] - history[r["step"] - 1]["det"]
            gain_str = f"  ({gain_r:+.1f}pp)"
        print(
            f"  {r['step']:>4}  {r['added']:<22}  {r['n_feat']:>5}  "
            f"{r['det']:>13.1f}%{gain_str}"
        )

    print(f"\n  최적 피처셋 ({best['n_feat']}개) — 탐지율 {best['det']:.1f}%"
          f"  (베이스라인 대비 {gain:+.1f}pp)")
    added = best.get("extra", [])
    if added:
        print(f"  추가된 피처 ({len(added)}개): {added}")
    else:
        print("  추가된 피처: 없음 (베이스라인이 최적)")

    print(f"\n  전체 피처 목록:")
    for f in BASE_FEATURES:
        print(f"      {f}")
    for f in added:
        desc, _ = CANDIDATE_FEATURES[f]
        print(f"    ★ {f}  ({desc})")


# ── 텍스트 보고서 저장 ────────────────────────────────────────────
def save_txt_report(history: list, args, txt_path: str, ts: str, perm_results: list = None):
    """결과를 사람이 읽기 쉬운 .txt 파일로 저장"""
    W = 70
    lines = []

    def L(s=""): lines.append(s)

    L("=" * W)
    L(f"  DCdetect 피처 엔지니어링 결과  -  {ts}")
    L(f"  데이터:   {args.input}")
    L(f"  설정:     max_mmsi={args.max_mmsi}  epochs={args.epochs}  n_anom={args.n_anom}")
    L("=" * W)

    # ── 피처 목록 ──
    L()
    L("[ 피처 목록 ]")
    L(f"  베이스 피처 ({len(BASE_FEATURES)}개):")
    for f in BASE_FEATURES:
        L(f"    {f}")

    best = max(history, key=lambda x: x.get("score", x["det"]))
    added = best.get("extra", [])   # INITIAL_EXTRA + 이번 greedy 채택 모두 포함
    if added:
        L(f"  추가된 파생 피처 ({len(added)}개):")
        for f in added:
            if f in CANDIDATE_FEATURES:
                desc, _ = CANDIDATE_FEATURES[f]
            else:
                desc = "(이전 반복 채택)"
            prefix = "◆ 기채택" if f in INITIAL_EXTRA else "★ 신채택"
            L(f"    {prefix}  {f:<22}  ({desc})")
    else:
        L("  추가된 파생 피처: 없음 (베이스라인이 최적)")
    L(f"  최종 피처 수: {best['n_feat']}개")

    # ── 단계별 요약 ──
    L()
    L("[ 단계별 선택 과정 ]")
    L(f"  {'Step':>4}  {'추가 피처':<22}  {'N피처':>5}  {'탐지율(전체평균)':>14}")
    L(f"  {'─'*55}")
    base_det_val = history[0]["det"]
    for r in history:
        gain_str = ""
        if r["step"] > 0:
            gain = r["det"] - history[r["step"] - 1]["det"]
            gain_str = f"  ({gain:+.1f}pp)"
        L(
            f"  {r['step']:>4}  {r['added']:<22}  {r['n_feat']:>5}  "
            f"{r['det']:>13.1f}%{gain_str}"
        )
    total_gain = best["det"] - base_det_val
    L(f"  {'─'*55}")
    L(f"  총 향상: {base_det_val:.1f}% → {best['det']:.1f}%  ({total_gain:+.1f}pp)")

    # ── 최적 피처셋 시나리오별 상세 ──
    L()
    L("[ 최적 피처셋 시나리오별 탐지율 (FP ≈ 1%) ]")
    baseline_sc = {name: d for name, d, _ in history[0].get("scenarios", [])}
    best_sc     = {name: d for name, d, _ in best.get("scenarios", [])}

    if best_sc:
        L()
        L(f"  {'시나리오':<22}  {'베이스라인':>10}  {'최적피처셋':>10}  {'변화':>8}")
        L(f"  {'─'*55}")
        for sc_name, det_v, _ in best.get("scenarios", []):
            base_v = baseline_sc.get(sc_name, float("nan"))
            diff = det_v - base_v if not math.isnan(base_v) else float("nan")
            diff_str = f"{diff:+.1f}pp" if not math.isnan(diff) else "  —"
            L(f"  {sc_name:<22}  {base_v:>9.1f}%  {det_v:>9.1f}%  {diff_str:>8}")
        L(f"  {'─'*55}")
        L(f"  {'전체 평균':<22}  "
          f"{float(np.mean(list(baseline_sc.values()))):>9.1f}%  "
          f"{best['det']:>9.1f}%")

    # ── 피처 중요도 (Permutation Importance) ──
    if perm_results:
        L()
        L("[ 피처 중요도 (Permutation Importance, FP 1% 기준) ]")
        L(f"  탐지율 하락량 = 해당 피처 셔플 시 탐지율 변화. 클수록 중요.")
        L()
        L(f"  {'순위':>4}  {'피처':<25}  {'중요도':>9}  막대")
        L(f"  {'─'*55}")
        for rank, (feat, base_det, imp) in enumerate(perm_results, 1):
            bar = "█" * max(0, int(abs(imp) / 1.5))
            sign = "+" if imp >= 0 else ""
            L(f"  {rank:>4}  {feat:<25}  {sign}{imp:>7.2f}pp  {bar}")
        L(f"  {'─'*55}")
        L(f"  기준 탐지율: {perm_results[0][1]:.1f}%")

    # ── 후보 피처 전체 목록 ──
    L()
    L("[ 후보 피처 목록 ]")
    # INITIAL_EXTRA (이전 반복 채택) 먼저 표시
    for name in INITIAL_EXTRA:
        if name in CANDIDATE_FEATURES:
            desc, _ = CANDIDATE_FEATURES[name]
        else:
            desc = "(이전 반복 채택)"
        L(f"  ◆ 기채택  {name:<22}  {desc}")
    # 이번 반복 후보들
    for name, (desc, _) in CANDIDATE_FEATURES.items():
        if name in INITIAL_EXTRA:
            continue
        adopted = "★ 채택" if name in added else "  미채택"
        L(f"  {adopted}  {name:<22}  {desc}")

    L()
    L("=" * W)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  텍스트 보고서: {txt_path}")


# ── 진입점 ────────────────────────────────────────────────────────
def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="DCdetect 피처 엔지니어링 자동화 (Greedy Forward Selection)"
    )
    ap.add_argument("--model",    default="dcdetect", choices=FE_SUPPORTED_MODELS,
                    help=f"FE 학습 모델 (기본: dcdetect, 지원: {FE_SUPPORTED_MODELS})")
    ap.add_argument("--input",    required=True,
                    help="전처리 CSV 경로")
    ap.add_argument("--base_dir", default=r"C:\Users\imcas",
                    help="결과 저장 베이스 경로 (기본: C:\\Users\\imcas)")
    ap.add_argument("--max_mmsi", type=int, default=500,
                    help="MMSI 샘플링 수 (기본: 500)")
    ap.add_argument("--epochs",   type=int, default=5,
                    help="에폭 수 (기본: 5)")
    ap.add_argument("--n_anom",   type=int, default=200,
                    help="시나리오당 이상 시퀀스 수 (기본: 200)")
    ap.add_argument("--out_json", default=None,
                    help="결과 JSON 저장 경로 (선택)")
    ap.add_argument("--initial_extra", nargs="*", default=None,
                    help="이전 반복 채택 피처 목록 (공백 구분). 미지정 시 코드 내 INITIAL_EXTRA 사용")
    ap.add_argument("--weak_floor", type=float, default=WEAK_FLOOR_DEFAULT,
                    help=f"약세 시나리오 분류 임계 %% (기본: {WEAK_FLOOR_DEFAULT})")
    ap.add_argument("--weak_weight", type=float, default=WEAK_WEIGHT_DEFAULT,
                    help=f"약세 시나리오 평균 가중치 (기본: {WEAK_WEIGHT_DEFAULT})")
    ap.add_argument("--min_gain", type=float, default=MIN_GAIN_DEFAULT,
                    help=f"목적점수 채택 임계 (기본: {MIN_GAIN_DEFAULT})")
    ap.add_argument("--max_feat", type=int, default=None,
                    help="총 피처 수 상한 (nhead=8 유지하려면 16 권장)")
    ap.add_argument("--max_candidates", type=int, default=None,
                    help="Greedy 후보 수 제한 (정의 순서 앞 N개만, 속도용)")
    ap.add_argument("--max_steps", type=int, default=None,
                    help="Greedy 최대 채택 스텝 수 (orchestrator에서 1로 지정해 1회 채택 후 종료)")
    ap.add_argument("--export_dir", default=None,
                    help="최적 피처셋 모델을 배포용 ONNX/scaler/threshold로 저장할 디렉터리 (선택)")
    ap.add_argument("--eval_ratio", type=float, default=EVAL_NORMAL_RATIO,
                    help=f"FP 측정용 홀드아웃 정상 시퀀스 비율 (월별 균등, 기본: {EVAL_NORMAL_RATIO})")
    args = ap.parse_args()

    # 이전 반복 채택 피처를 CLI로 받았으면 전역 INITIAL_EXTRA 오버라이드
    global INITIAL_EXTRA
    if args.initial_extra is not None:
        INITIAL_EXTRA = list(args.initial_extra)

    # 재현성 시드
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n피처 엔지니어링 자동화  {ts}")
    print(f"입력: {args.input}")
    print(f"설정: max_mmsi={args.max_mmsi}  epochs={args.epochs}  n_anom={args.n_anom}")

    train_seqs, eval_normal_seqs = load_raw_seqs(
        args.input, args.max_mmsi, args.eval_ratio)
    history, best_extra = greedy_forward_selection(train_seqs, eval_normal_seqs, args)
    print_report(history)

    # ── 최적 피처셋으로 재학습 → Permutation Importance ──────────────
    # 학습은 train_seqs, FP(오탐)·중요도 평가는 홀드아웃 eval_normal_seqs 사용
    best = max(history, key=lambda x: x.get("score", x["det"]))
    best_extra = best.get("extra", [])
    print(f"\n[피처 중요도] 최적 피처셋({best['n_feat']}개)으로 재학습 중...")
    tensor_best, scaler_best = prepare_tensor(train_seqs, best_extra)
    model_best, _ = train_recon_model(args.model, tensor_best, best["n_feat"], args.epochs)
    perm_results = permutation_importance(
        model_best, scaler_best, best_extra, eval_normal_seqs, n_anom=args.n_anom
    )

    print("\n  피처 중요도 순위 (탐지율 하락량):")
    print(f"  {'피처':<25s}  {'중요도':>8}")
    print(f"  {'─'*36}")
    for feat, base_det, imp in perm_results:
        bar = "█" * max(0, int(imp / 2)) if imp > 0 else ""
        print(f"  {feat:<25s}  {imp:>+7.2f}pp  {bar}")

    # ── 최종 모델 다중 FP 평가 (threshold + FP=5%/10%) ──────────────
    print(f"\n[최종 모델 다중 FP 평가]  피처 {best['n_feat']}개...")
    det_final, sc_final, extra_final, threshold = evaluate(
        model_best, scaler_best, best_extra, args.n_anom,
        raw_seqs=eval_normal_seqs, extra_fp=(5.0, 10.0)
    )
    det_fp5  = float(np.mean(list(extra_final[5.0].values()))) if extra_final.get(5.0) else None
    det_fp10 = float(np.mean(list(extra_final[10.0].values()))) if extra_final.get(10.0) else None
    print(f"  FP=1%: {det_final:.1f}%  FP=5%: {det_fp5:.1f}%  FP=10%: {det_fp10:.1f}%")
    print(f"  threshold (FP=1% 기준): {threshold:.8f}")

    # 저장 경로 준비
    #   --out_json 지정 시 그 위치(오케스트레이터의 repo-local 임시 디렉터리)에 보고서 생성.
    #   미지정(단독 실행) 시에만 base_dir/ais_output/feat_eng 사용.
    if args.out_json:
        out_dir = os.path.dirname(os.path.abspath(args.out_json)) or "."
    else:
        out_dir = os.path.join(args.base_dir, "ais_output", "feat_eng")
    os.makedirs(out_dir, exist_ok=True)

    # JSON 저장
    json_path = args.out_json or os.path.join(out_dir, f"feat_eng_{ts}.json")

    # 피처 설명 딕셔너리 (베이스 + 후보)
    _base_descs = {
        "sog":              "선속 (Speed Over Ground, knots)",
        "cog":              "항로 방향 (Course Over Ground, 0~360°)",
        "heading":          "선수 방향 (True Heading, 0~360°, 511=불가)",
        "status":           "AIS 항법 상태 코드 (0=운항, 1=정박, 5=계류 등)",
        "dt":               "이전 포인트와의 시간 간격 (초)",
        "dist_km":          "이전 포인트로부터의 이동 거리 (km)",
        "cog_hdg_diff":     "COG-Heading 각도 차이 (0~180°, -1=heading 불가)",
        "sog_change":       "SOG 변화량 (현재 - 이전, knots)",
        "cog_hdg_change":   "cog_hdg_diff 변화량 (현재 - 이전)",
        "speed_consistency":"SOG와 거리/시간 속력의 일관성 (1=완전일치)",
        "lat_speed":        "위도 방향 속도 성분 (deg/s, GPS 기반)",
        "lon_speed":        "경도 방향 속도 성분 (deg/s, GPS 기반)",
    }
    _all_feat_descs: dict = {}
    _all_feat_descs.update(_base_descs)
    for name, (desc, _) in CANDIDATE_FEATURES.items():
        _all_feat_descs[name] = desc

    all_feats_in_result = list(dict.fromkeys(INITIAL_EXTRA + best_extra))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "best_extra": best_extra,
                "best_det":      det_final,
                "det_fp5":       det_fp5,
                "det_fp10":      det_fp10,
                "threshold":     threshold,
                "baseline_det":  history[0]["det"],
                "baseline_score": history[0].get("score"),
                "initial_extra": INITIAL_EXTRA,
                "feature_descriptions": {
                    f: _all_feat_descs.get(f, "") for f in
                    BASE_FEATURES + all_feats_in_result
                },
                "scenario_fp1":  {n: d for n, d, _ in sc_final},
                "scenario_fp5":  dict(extra_final.get(5.0, {})),
                "scenario_fp10": dict(extra_final.get(10.0, {})),
                "history": [
                    {k: v for k, v in r.items() if k != "scenarios"}
                    | {"scenarios": [(n, d, h) for n, d, h in r.get("scenarios", [])]}
                    for r in history
                ],
                "permutation_importance": [
                    {"feature": feat, "base_det": base_det, "importance": imp}
                    for feat, base_det, imp in perm_results
                ],
            },
            f, indent=2, ensure_ascii=False,
        )

    # 텍스트 보고서 저장 (perm_results 전달)
    txt_path = os.path.join(out_dir, f"feat_eng_{ts}.txt")
    save_txt_report(history, args, txt_path, ts, perm_results=perm_results)

    # ── 배포용 모델 export (--export_dir 지정 시) ──────────────────────
    # 최적 피처셋으로 학습된 model_best 를 플러그인 배포용 ONNX/scaler/threshold 로 저장.
    # 실패해도 FE 결과(JSON/txt)에는 영향 없도록 try/except 로 격리.
    if args.export_dir:
        try:
            import torch as _torch
            import warnings as _warnings
            os.makedirs(args.export_dir, exist_ok=True)
            all_feat_names = BASE_FEATURES + best_extra
            n_feat         = best["n_feat"]
            onnx_path      = os.path.join(args.export_dir, f"model_{args.model}.onnx")
            scaler_path    = os.path.join(args.export_dir, f"scaler_{args.model}.json")
            threshold_path = os.path.join(args.export_dir, f"threshold_{args.model}.txt")

            # ONNX (input "x", shape (1, SEQ_LEN, n_feat))
            model_best.eval()
            dummy = _torch.zeros(1, SEQ_LEN, n_feat, dtype=_torch.float32)
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                _torch.onnx.export(
                    model_best, (dummy,), onnx_path,
                    dynamo=False, opset_version=14,
                    input_names=["x"], output_names=["output"],
                )

            # scaler (C++ MLScaler 호환: features/min/max. clamp == 클리핑이므로 min/max만)
            with open(scaler_path, "w", encoding="utf-8") as sf:
                json.dump({
                    "features": all_feat_names,
                    "min": [float(x) for x in scaler_best.data_min_],
                    "max": [float(x) for x in scaler_best.data_max_],
                }, sf, indent=2, ensure_ascii=False)

            # threshold: 위에서 evaluate()가 반환한 fp1_thr (정상 점수 99퍼센타일)
            with open(threshold_path, "w", encoding="utf-8") as tf:
                tf.write(str(threshold))

            print(f"\n  [배포 export] {args.export_dir}  (피처 {n_feat}개)")
            print(f"    features: {all_feat_names}")
            print(f"    threshold: {threshold}")
        except Exception as _e:
            print(f"  [배포 export 실패] {_e}")

    print(f"\n  JSON:  {json_path}")
    print(f"\n  최적 추가 피처: {best_extra}")
    print(f"  탐지율 향상: {history[0]['det']:.1f}% → {best['det']:.1f}%"
          f"  ({best['det']-history[0]['det']:+.1f}pp)")

    # ── Discord / Notion 알림 ──────────────────────────────────────
    try:
        from ml.integrations.notify import notify_iteration
        gain = best["det"] - history[0]["det"]
        new_adopted = [f for f in best_extra if f not in INITIAL_EXTRA]
        # 상위 중요 피처 3개
        top_imp = ", ".join(
            f"{feat}({imp:+.1f})" for feat, _, imp in perm_results[:3]
        )
        summary = (
            f"최종 피처 {best['n_feat']}개 | 탐지율 {history[0]['det']:.1f}% → "
            f"{best['det']:.1f}% ({gain:+.1f}pp)\n"
            f"신규 채택: {new_adopted or '없음'}\n"
            f"기채택: {INITIAL_EXTRA}\n"
            f"중요 피처 Top3: {top_imp}"
        )
        with open(txt_path, "r", encoding="utf-8") as _f:
            report_text = _f.read()
        notify_iteration(
            title=f"DCdetect 피처 엔지니어링 (max_mmsi={args.max_mmsi})",
            summary=summary,
            report_text=report_text,
            color=0x2ECC71 if gain >= 0 else 0xE74C3C,
        )
    except Exception as e:
        print(f"  [알림] 전송 건너뜀: {e}")


if __name__ == "__main__":
    main()
