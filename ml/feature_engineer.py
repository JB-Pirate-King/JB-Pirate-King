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
        _build_derived,
        make_normal_seq,
    )
finally:
    sys.argv = _saved_argv

from train_benchmark import (
    DCdetector,
    make_loaders,
    train_standard,
    SEED,
    VAL_RATIO,
    THRESHOLD_PERCENTILE,
)

# ── 고정 상수 ──────────────────────────────────────────────────────
SEQ_LEN   = 10
SEQ_BREAK = 600    # dt 임계값 (초) — 시퀀스 분리

# ── 베이스 피처 (현재 12개) ────────────────────────────────────────
BASE_FEATURES = [
    "sog", "cog", "heading", "status",
    "dt", "dist_km",
    "cog_hdg_diff", "sog_change", "cog_hdg_change",
    "speed_consistency", "lat_speed", "lon_speed",
]
_B = {name: i for i, name in enumerate(BASE_FEATURES)}


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
}


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


# ── 데이터 로드 ────────────────────────────────────────────────────
def load_raw_seqs(input_file: str, max_mmsi: int) -> list:
    """전처리 CSV → BASE_FEATURES 기준 raw 시퀀스 리스트"""
    print(f"\n[데이터] {input_file} 로드 중...")
    mmsi_data: dict = defaultdict(list)

    with open(input_file, encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            mmsi = row.get("mmsi", "")
            if not mmsi:
                continue
            try:
                record = [float(row[col]) for col in BASE_FEATURES]
                mmsi_data[mmsi].append(record)
            except (ValueError, KeyError):
                continue

    print(f"  고유 MMSI: {len(mmsi_data):,}")
    if max_mmsi and len(mmsi_data) > max_mmsi:
        keys = random.sample(list(mmsi_data.keys()), max_mmsi)
        mmsi_data = {k: mmsi_data[k] for k in keys}
        print(f"  샘플링 후 MMSI: {len(mmsi_data):,}")

    dt_idx = _B["dt"]
    sequences = []
    for records in mmsi_data.values():
        seg, cur = [], [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK:
                seg.append(cur)
                cur = [rec]
            else:
                cur.append(rec)
        seg.append(cur)
        for s in seg:
            if len(s) < SEQ_LEN:
                continue
            for i in range(len(s) - SEQ_LEN + 1):
                sequences.append(s[i : i + SEQ_LEN])

    print(f"  총 시퀀스: {len(sequences):,}")
    return sequences


# ── Tensor 준비 ────────────────────────────────────────────────────
def prepare_tensor(raw_seqs: list, extra_names: list):
    """파생 피처 추가 → MinMaxScaling → Tensor. (tensor, scaler) 반환."""
    aug = augment_seqs(raw_seqs, extra_names)
    flat = [row for seq in aug for row in seq]
    scaler = MinMaxScaler()
    scaler.fit(flat)
    scaled = [scaler.transform(seq).tolist() for seq in aug]
    tensor = torch.tensor(scaled, dtype=torch.float32)
    return tensor, scaler


# ── DCdetector 학습 ───────────────────────────────────────────────
def train_dcdetect(
    tensor: torch.Tensor,
    n_feat: int,
    epochs: int,
    lr: float = 1e-3,
    batch_size: int = 256,
    patience: int = 5,
    nhead_max: int = 4,
):
    """DCdetector 학습 후 (model, val_loader) 반환.
    nhead 는 n_feat 의 약수 중 nhead_max 이하 최댓값으로 자동 조정."""
    device = torch.device("cpu")
    d_model = 64
    # n_feat % nhead == 0  AND  d_model % nhead == 0 을 동시에 만족하는 최대 nhead
    nhead = max(h for h in range(1, nhead_max + 1) if n_feat % h == 0 and d_model % h == 0)
    model = DCdetector(SEQ_LEN, n_feat, patch_size=2, d_model=d_model, nhead=nhead).to(device)
    train_loader, val_loader = make_loaders(tensor, batch_size)
    train_standard(model, train_loader, val_loader, device, epochs, lr, patience)
    return model, val_loader


# ── 임계값 계산 ────────────────────────────────────────────────────
def compute_threshold(model, val_loader) -> float:
    device = next(model.parameters()).device
    model.eval()
    errors = []
    with torch.no_grad():
        for (batch,) in val_loader:
            batch = batch.to(device)
            out = model(batch)
            mse = ((out - batch) ** 2).mean(dim=(1, 2))
            errors.extend(mse.cpu().tolist())
    errors.sort()
    idx = int(len(errors) * THRESHOLD_PERCENTILE / 100)
    return errors[min(idx, len(errors) - 1)]


# ── 평가 ────────────────────────────────────────────────────────────
def evaluate(
    model,
    scaler: MinMaxScaler,
    extra_names: list,
    n_anom: int = 200,
    n_normal: int = 3000,
    raw_seqs: list = None,
):
    """
    FP ≈ 1% 기준 탐지율 계산.
    반환: (train_avg, holdout_avg) — 학습 시나리오 / 홀드아웃 평균 탐지율(%)
    """
    device = next(model.parameters()).device
    model.eval()
    mins = scaler.data_min_
    maxs = scaler.data_max_
    scale_range = maxs - mins

    def _scale_row(row):
        return [
            (v - mn) / (rng + 1e-9)
            for v, mn, rng in zip(row, mins, scale_range)
        ]

    def _score(seq: list) -> float:
        """raw 시퀀스(base) → 파생 추가 → 스케일 → MSE 스코어"""
        aug = augment_seq(seq, extra_names)
        scaled = [_scale_row(row) for row in aug]
        x = torch.tensor(scaled, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            out = model(x)
            return float(((out - x) ** 2).mean())

    # 정상 시퀀스 점수 → FP 1% 임계값
    if raw_seqs:
        normal_raw = random.sample(raw_seqs, min(n_normal, len(raw_seqs)))
    else:
        normal_raw = [make_normal_seq() for _ in range(n_normal)]

    normal_scores = [_score(seq) for seq in normal_raw]
    fp1_thr = float(np.percentile(normal_scores, 99))  # 상위 1% = FP 1% 임계

    # 시나리오별 탐지율
    anom_scenarios = [
        (name, maker, is_holdout)
        for name, maker, is_anom, is_holdout in SCENARIO_MAKERS
        if is_anom
    ]

    all_dets = []
    scenario_results = []   # [(name, det), ...]
    for sc_name, maker, is_holdout in tqdm(anom_scenarios, desc="  시나리오 평가", leave=False):
        anom_seqs = [maker() for _ in range(n_anom)]
        anom_scores = [_score(seq) for seq in anom_seqs]
        det = sum(1 for s in anom_scores if s > fp1_thr) / len(anom_scores) * 100.0
        all_dets.append(det)
        scenario_results.append((sc_name, det, is_holdout))

    return float(np.mean(all_dets)), scenario_results


# ── Permutation Importance ────────────────────────────────────────
def permutation_importance(
    model,
    scaler,
    extra_names: list,
    raw_seqs: list,
    n_anom: int = 200,
    n_normal: int = 3000,
    n_repeat: int = 3,
) -> list:
    """
    학습된 모델로 피처별 순열 중요도 계산.
    각 피처를 랜덤 셔플 → 탐지율 하락량 = 중요도.
    반환: [(feat_name, base_det, shuffled_det, importance), ...] 내림차순
    """
    device = next(model.parameters()).device
    model.eval()
    mins  = scaler.data_min_
    maxs  = scaler.data_max_
    scale_range = maxs - mins
    all_feat_names = BASE_FEATURES + extra_names

    def _scale_row(row):
        return [(v - mn) / (rng + 1e-9)
                for v, mn, rng in zip(row, mins, scale_range)]

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


# ── Greedy Forward Selection 메인 루프 ────────────────────────────
def greedy_forward_selection(raw_seqs: list, args) -> tuple:
    current_extra: list = []
    remaining: list = list(CANDIDATE_FEATURES.keys())
    history: list = []

    W = 65
    print("\n" + "=" * W)
    print("  피처 엔지니어링 자동화  (Greedy Forward Selection)")
    print(f"  베이스 피처: {len(BASE_FEATURES)}개  |  후보: {len(remaining)}개")
    print(f"  epochs={args.epochs}  max_mmsi={args.max_mmsi}  n_anom={args.n_anom}")
    print("=" * W)

    # ── 베이스라인 ──────────────────────────────────────────────
    print(f"\n{'─'*W}")
    print(f"[베이스라인]  피처 {len(BASE_FEATURES)}개: {BASE_FEATURES}")
    print(f"{'─'*W}")
    t0 = time.time()
    tensor, scaler = prepare_tensor(raw_seqs, [])
    model, val_loader = train_dcdetect(tensor, len(BASE_FEATURES), args.epochs)
    det0, sc0 = evaluate(model, scaler, [], args.n_anom, raw_seqs=raw_seqs)
    elapsed = time.time() - t0
    best_det = det0
    print(f"  → 전체 평균 탐지율 {det0:.1f}%  [{elapsed/60:.1f}분]")
    history.append(
        dict(step=0, added="베이스라인", n_feat=len(BASE_FEATURES),
             det=det0, extra=list(current_extra), scenarios=sc0)
    )

    step = 1
    while remaining:
        print(f"\n{'─'*W}")
        print(f"[Step {step}]  현재 {len(BASE_FEATURES)+len(current_extra)}개 피처  "
              f"| 후보 {len(remaining)}개")
        if current_extra:
            print(f"  현재 추가됨: {current_extra}")
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
            tensor, scaler = prepare_tensor(raw_seqs, trial_extra)
            model, val_loader = train_dcdetect(tensor, n_feat, args.epochs)
            det, sc = evaluate(model, scaler, trial_extra, args.n_anom, raw_seqs=raw_seqs)
            elapsed = time.time() - t0
            gain = det - best_det
            arrow = "▲" if gain > 0.5 else ("▼" if gain < -0.5 else "─")
            print(
                f"  전체평균 {det:5.1f}%"
                f"  {arrow}{abs(gain):4.1f}pp  [{elapsed/60:.1f}분]"
            )
            if gain > step_best_gain:
                step_best_gain = gain
                step_best_feat = cand
                step_best_result = (det, trial_extra, tensor, scaler, model, sc)

        # 0.5pp 이상 향상 시 채택
        if step_best_gain > 0.5:
            current_extra = step_best_result[1]
            best_det = step_best_result[0]
            remaining.remove(step_best_feat)
            desc, _ = CANDIDATE_FEATURES[step_best_feat]
            print(f"\n  ✓ 채택: [{step_best_feat}] ({desc})")
            print(f"    탐지율  {history[-1]['det']:.1f}%"
                  f" → {best_det:.1f}%  (+{step_best_gain:.1f}pp)")
            history.append(
                dict(step=step, added=step_best_feat,
                     n_feat=len(BASE_FEATURES) + len(current_extra),
                     det=best_det,
                     extra=list(current_extra),
                     scenarios=step_best_result[5])
            )
        else:
            best_cand = step_best_feat
            print(f"\n  ✗ 개선 없음 (최고 후보: {best_cand}  {step_best_gain:+.1f}pp)"
                  f" → 종료")
            break

        step += 1

    return history, current_extra


# ── 보고서 출력 ───────────────────────────────────────────────────
def print_report(history: list):
    W = 65
    best = max(history, key=lambda x: x["det"])
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

    best = max(history, key=lambda x: x["det"])
    added = best.get("extra", [])
    if added:
        L(f"  추가된 파생 피처 ({len(added)}개):")
        for f in added:
            desc, _ = CANDIDATE_FEATURES[f]
            L(f"    ★ {f:<22}  ({desc})")
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
    for name, (desc, _) in CANDIDATE_FEATURES.items():
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
    args = ap.parse_args()

    # 재현성 시드
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n피처 엔지니어링 자동화  {ts}")
    print(f"입력: {args.input}")
    print(f"설정: max_mmsi={args.max_mmsi}  epochs={args.epochs}  n_anom={args.n_anom}")

    raw_seqs = load_raw_seqs(args.input, args.max_mmsi)
    history, best_extra = greedy_forward_selection(raw_seqs, args)
    print_report(history)

    # ── 최적 피처셋으로 재학습 → Permutation Importance ──────────────
    best = max(history, key=lambda x: x["det"])
    best_extra = best.get("extra", [])
    print(f"\n[피처 중요도] 최적 피처셋({best['n_feat']}개)으로 재학습 중...")
    tensor_best, scaler_best = prepare_tensor(raw_seqs, best_extra)
    model_best, _ = train_dcdetect(tensor_best, best["n_feat"], args.epochs)
    perm_results = permutation_importance(
        model_best, scaler_best, best_extra, raw_seqs, n_anom=args.n_anom
    )

    print("\n  피처 중요도 순위 (탐지율 하락량):")
    print(f"  {'피처':<25s}  {'중요도':>8}")
    print(f"  {'─'*36}")
    for feat, base_det, imp in perm_results:
        bar = "█" * max(0, int(imp / 2)) if imp > 0 else ""
        print(f"  {feat:<25s}  {imp:>+7.2f}pp  {bar}")

    # 저장 경로 준비
    out_dir = os.path.join(args.base_dir, "ais_output", "feat_eng")
    os.makedirs(out_dir, exist_ok=True)

    # JSON 저장
    json_path = args.out_json or os.path.join(out_dir, f"feat_eng_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
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

    print(f"\n  JSON:  {json_path}")
    print(f"\n  최적 추가 피처: {best_extra}")
    print(f"  탐지율 향상: {history[0]['det']:.1f}% → {best['det']:.1f}%"
          f"  ({best['det']-history[0]['det']:+.1f}pp)")


if __name__ == "__main__":
    main()
