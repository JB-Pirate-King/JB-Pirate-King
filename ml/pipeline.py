"""
AIS 이상 탐지 멀티모델 학습 / 탐지율 비교 파이프라인

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
데이터 흐름
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  D:\\ais_data\\raw\\*.csv          ← 원본 AIS 데이터 (기본 raw 경로)
       │
       │  --preprocess
       ▼
  data/ais_preprocessed.csv        ← 전처리 완료 파일 (기본 data_file)
       │
       │  --train
       ▼
  output/{model}/model_{model}.onnx 등
       │
       │  --eval
       ▼
  output/pipeline/comparison_TIMESTAMP.txt / .csv

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용법
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # 전체 파이프라인 (전처리 → 학습 → 평가)
  python pipeline.py --preprocess --train --eval --models dcdetect tranad sup_moderntcn

  # 원본 데이터 경로 직접 지정
  python pipeline.py --preprocess --raw_data D:\\ais_data\\raw --train --eval --models dcdetect

  # 이미 전처리된 파일이 있는 경우 학습 + 평가만
  python pipeline.py --train --eval --models usad tranad dcdetect

  # 기존 모델을 재학습 없이 평가만
  python pipeline.py --eval --models usad tranad dcdetect sup_moderntcn

  # 비지도 / 지도 / 전체 모델
  python pipeline.py --train --eval --unsup
  python pipeline.py --train --eval --sup
  python pipeline.py --train --eval --all_models

  # 이미 학습된 모델은 건너뛰고 추가 모델만 학습
  python pipeline.py --train --eval --models usad tranad conv1d --skip_trained

  # 오탐율 목표값 / 시나리오 수 조정
  python pipeline.py --eval --models dcdetect tranad --fp_targets 1 5 10 --n_anom 1000

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
지원 모델
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  비지도 (9): usad tranad conv1d lstm tcn anomtrans dcdetect iforest ocsvm
  지도   (5): sup_patchtst sup_itrans sup_tsmixer sup_moderntcn sup_mamba

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
출력
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  output/pipeline/comparison_YYYYMMDD_HHMMSS.txt   텍스트 비교 테이블
  output/pipeline/comparison_YYYYMMDD_HHMMSS.csv   CSV 결과 파일
"""

import argparse
import csv as csv_mod
import glob
import json
import math
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime

# Windows cp949 콘솔 환경에서 UTF-8 출력 강제
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import numpy as np
import onnxruntime as ort
from tqdm import tqdm

# ── eval_anomaly에서 시나리오 메이커만 안전하게 임포트 ──────────────
# eval_anomaly.py 는 모듈 레벨에서 argparse.parse_known_args() 를 호출하므로
# 임포트 시 pipeline.py 의 --models 등 인수가 간섭하지 않도록 임시 교체.
_saved_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
try:
    from eval_anomaly import (  # noqa: E402
        FEATURES,
        SCENARIO_MAKERS,
        SEQ_LEN,
        make_normal_seq,
        scale_seq,
    )
finally:
    sys.argv = _saved_argv

# ── 기본 경로 설정 ────────────────────────────────────────────────────
# 원본 AIS 데이터 다운로드 위치 (D:\ais_data\raw)
_DEFAULT_RAW_DATA  = r"D:\ais_data\raw\2025"
# 전처리 완료 파일 (train_supervised.py 의 csv_candidates 첫 번째 항목과 일치)
_DEFAULT_DATA_FILE = r"D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv"

UNSUP_MODELS = [
    "usad", "tranad", "conv1d", "lstm", "tcn",
    "anomtrans", "dcdetect", "iforest", "ocsvm",
]
SUP_MODELS = [
    "sup_patchtst", "sup_itrans", "sup_tsmixer",
    "sup_moderntcn", "sup_mamba",
]
ALL_MODELS = UNSUP_MODELS + SUP_MODELS

OUTPUT_DIR   = "output"
DATA_DIR     = "data"
PIPELINE_DIR = os.path.join(OUTPUT_DIR, "pipeline")
SEQ_BREAK_DT = 600   # 시퀀스 분리 임계 dt (초)


# ── 유틸: 한/영 혼용 우측 정렬 ────────────────────────────────────────
def rjust(s: str, width: int) -> str:
    display_len = sum(
        2 if 0xAC00 <= ord(c) <= 0xD7A3 or 0x3130 <= ord(c) <= 0x318F else 1
        for c in s
    )
    return " " * max(width - display_len, 0) + s


# ── 모델 파일 경로 계산 ───────────────────────────────────────────────
def _model_paths(name: str):
    if name.startswith("sup_"):
        mdir        = os.path.join(OUTPUT_DIR, name)
        model_f     = os.path.join(mdir, f"model_{name}.onnx")
        threshold_f = os.path.join(mdir, f"threshold_{name}.txt")
        scaler_f    = os.path.join(mdir, f"scaler_{name}.json")
        if not os.path.exists(scaler_f):
            scaler_f = os.path.join(OUTPUT_DIR, "scaler_sup.json")
    else:
        # train_benchmark.py 는 cwd(ml/) 에 직접 저장
        model_f     = f"model_{name}.onnx"
        scaler_f    = f"scaler_{name}.json"
        threshold_f = f"threshold_{name}.txt"
    return model_f, scaler_f, threshold_f


def model_exists(name: str) -> bool:
    return all(os.path.exists(p) for p in _model_paths(name))


def load_model(name: str):
    """ONNX 세션, 스케일러(mins/maxs), 저장 임계값 반환"""
    model_f, scaler_f, threshold_f = _model_paths(name)
    for p in [model_f, scaler_f, threshold_f]:
        if not os.path.exists(p):
            raise FileNotFoundError(f"파일 없음: {p}")
    session = ort.InferenceSession(model_f, providers=["CPUExecutionProvider"])
    with open(scaler_f) as f:
        j = json.load(f)
    mins, maxs = j["min"], j["max"]
    with open(threshold_f) as f:
        threshold = float(f.readline())
    return session, mins, maxs, threshold


# ── 추론 ──────────────────────────────────────────────────────────────
def infer_score(session, seq_scaled, is_supervised: bool) -> float:
    """비지도: 재구성 MSE, 지도: sigmoid 확률 반환"""
    x   = np.array(seq_scaled, dtype=np.float32)[np.newaxis]
    out = session.run(None, {"x": x})[0]
    if is_supervised:
        return float(out.flat[0])
    return float(np.mean((out - x) ** 2))


# ── 실제 정상 시퀀스 로더 (raw; 스케일러 적용 전) ─────────────────────
def load_raw_normal_seqs(data_file: str = _DEFAULT_DATA_FILE,
                          n_seqs: int = 3000):
    """지정한 전처리 CSV 에서 raw 정상 시퀀스 로드.
    스케일러는 모델마다 다르므로 스케일링은 호출자에서 담당.
    data_file 이 없으면 data/ 폴더를 glob 으로 탐색 후 최신 파일 사용.
    """
    # 지정 파일 우선, 없으면 data/ glob fallback
    if os.path.exists(data_file):
        target = data_file
    else:
        candidates = sorted(
            glob.glob(os.path.join(DATA_DIR, "*_preprocessed.csv")) +
            glob.glob(os.path.join(DATA_DIR, "ais-*.csv"))
        )
        if not candidates:
            return None
        target = candidates[-1]
        print(f"  ⚠ {data_file} 없음 → fallback: {target}")

    mmsi_rows = defaultdict(list)
    with open(target, encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        for i, row in enumerate(reader):
            if i >= 500_000:
                break
            try:
                record = [float(row[feat]) for feat in FEATURES]
                mmsi_rows[row["mmsi"]].append(record)
            except (ValueError, KeyError):
                continue

    dt_idx   = FEATURES.index("dt")
    all_seqs = []
    for records in mmsi_rows.values():
        seg, cur = [], [records[0]]
        for rec in records[1:]:
            if rec[dt_idx] >= SEQ_BREAK_DT:
                seg.append(cur)
                cur = [rec]
            else:
                cur.append(rec)
        seg.append(cur)
        for s in seg:
            if len(s) < SEQ_LEN:
                continue
            for i in range(len(s) - SEQ_LEN + 1):
                all_seqs.append(s[i:i + SEQ_LEN])

    if not all_seqs:
        return None

    import random
    random.shuffle(all_seqs)
    sampled = all_seqs[:n_seqs] if n_seqs and n_seqs < len(all_seqs) else all_seqs
    print(f"  정상 시퀀스 로드: {len(sampled):,}개  (풀: {len(all_seqs):,})  ← {target}")
    return sampled


# ── 단일 모델 전체 시나리오 스코어 계산 ──────────────────────────────
def compute_scores(session, mins, maxs, is_supervised: bool,
                   n_anom: int, raw_seqs=None, n_normal: int = 3000):
    """모델 하나의 전체 시나리오 스코어를 수집.

    Returns
    -------
    normal_scores   : list[float]
    scenario_scores : dict[str, list[float]]   (is_anom=True 시나리오만)
    """
    # 정상 스코어
    if raw_seqs is not None:
        pool = raw_seqs[:n_normal] if n_normal and n_normal < len(raw_seqs) else raw_seqs
        normal_scores = [
            infer_score(session, scale_seq(seq, mins, maxs), is_supervised)
            for seq in pool
        ]
    else:
        normal_scores = [
            infer_score(session, scale_seq(make_normal_seq(), mins, maxs), is_supervised)
            for _ in range(n_normal or 3000)
        ]

    # 이상 시나리오별 스코어
    anom_makers = [(n, mk) for n, mk, ia, _ in SCENARIO_MAKERS if ia]
    scenario_scores = {}
    for sc_name, maker in tqdm(anom_makers, desc="  시나리오", leave=False, unit="개"):
        seqs = [scale_seq(maker(), mins, maxs) for _ in range(n_anom)]
        scenario_scores[sc_name] = [
            infer_score(session, seq, is_supervised) for seq in seqs
        ]

    return normal_scores, scenario_scores


def detection_rates(normal_scores, scenario_scores, threshold):
    """임계값 적용 → (오탐율%, {시나리오명: 탐지율%})"""
    n      = len(normal_scores)
    fp_pct = sum(1 for s in normal_scores if s > threshold) / n * 100
    dr     = {
        name: sum(1 for s in scores if s > threshold) / len(scores) * 100
        for name, scores in scenario_scores.items()
    }
    return fp_pct, dr


# ── 전처리 실행 ───────────────────────────────────────────────────────
def run_preprocess(raw_data: str, output_file: str) -> bool:
    """preprocess.py 를 subprocess 로 실행.
    raw_data  : 원본 CSV 가 있는 폴더 (예: D:\\ais_data\\raw)
    output_file : 전처리 결과 저장 경로 (예: data/ais_preprocessed.csv)
    """
    if not os.path.isdir(raw_data):
        print(f"  ✗ 원본 데이터 폴더 없음: {raw_data}")
        return False

    script_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = [
        sys.executable, "preprocess.py",
        raw_data,
        "--output", output_file,
    ]
    print(f"\n{'─'*60}")
    print(f"  전처리 시작")
    print(f"  원본: {raw_data}")
    print(f"  출력: {output_file}")
    print(f"  명령: {' '.join(cmd)}")
    print(f"{'─'*60}")
    t0  = time.time()
    ret = subprocess.run(cmd, cwd=script_dir)
    elapsed = time.time() - t0
    if ret.returncode == 0:
        print(f"  ✓ 전처리 완료  ({elapsed/60:.1f}분)  → {output_file}")
        return True
    print(f"  ✗ 전처리 실패  (exit {ret.returncode}, {elapsed/60:.1f}분)")
    return False


# ── 학습 실행 ─────────────────────────────────────────────────────────
def run_training(name: str, data_file: str, extra_args: list) -> tuple:
    """모델 하나를 학습. (성공여부: bool, 소요시간: float) 반환.

    data_file 은 전처리된 CSV 경로.
    train_benchmark.py 에는 --input, train_supervised.py 는
    data/ais_preprocessed.csv 를 candidates[0]에서 자동 탐지하므로
    data_file == _DEFAULT_DATA_FILE 이면 별도 인자 불필요.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if name.startswith("sup_"):
        short = name[4:]   # sup_moderntcn → moderntcn
        cmd   = [sys.executable, "train_supervised.py", "--model", short] + extra_args
        # train_supervised.py 는 data/ 폴더 후보 목록에서 자동 탐지.
        # 비표준 경로일 때만 DATA_DIR env 처리 (현재 버전은 후보 하드코딩이므로 경고만 출력).
        if data_file != _DEFAULT_DATA_FILE and not os.path.exists(
            os.path.join(DATA_DIR, "ais_preprocessed.csv")
        ):
            print(f"  ⚠ train_supervised.py 는 data/ 폴더를 고정 탐색합니다.")
            print(f"    {data_file} 을 data/ais_preprocessed.csv 에 심볼릭 링크하거나 복사하세요.")
    else:
        cmd   = [sys.executable, "train_benchmark.py",
                 "--model", name, "--input", data_file] + extra_args

    print(f"\n{'─'*60}")
    print(f"  학습 시작: {name}")
    print(f"  데이터:   {data_file}")
    print(f"  명령: {' '.join(cmd)}")
    print(f"{'─'*60}")
    t0  = time.time()
    ret = subprocess.run(cmd, cwd=script_dir)
    elapsed = time.time() - t0
    if ret.returncode == 0:
        print(f"  ✓ {name} 학습 완료  ({elapsed/60:.1f}분)")
        return True, elapsed
    print(f"  ✗ {name} 학습 실패  (exit {ret.returncode}, {elapsed/60:.1f}분)")
    return False, elapsed


# ── 출력 포맷터 ───────────────────────────────────────────────────────
def _fmt(v, w=10):
    if isinstance(v, float) and not math.isnan(v):
        return rjust(f"{v:.1f}%", w)
    return rjust("-", w)


def _avg(vals):
    clean = [v for v in vals if isinstance(v, float) and not math.isnan(v)]
    return float(np.mean(clean)) if clean else float("nan")


def format_comparison_table(model_names, fp_rates, det_by_threshold,
                             det_at_fp, train_time, data_file, timestamp):
    """텍스트 비교 테이블 문자열 생성"""
    W_SC  = 22
    W_COL = 11

    def _header():
        h = " " * W_SC
        for m in model_names:
            label = m.replace("sup_", "s_").replace("_", "")[:9]
            h += rjust(label, W_COL)
        return h

    def _sep(ch="─"):
        return ch * (W_SC + W_COL * len(model_names))

    anom_rows = [(n, ih) for n, _, ia, ih in SCENARIO_MAKERS if ia]
    lines = []
    lines.append("=" * 70)
    lines.append(f"  AIS 이상 탐지 모델 비교  -  {timestamp}")
    lines.append(f"  모델 ({len(model_names)}): {', '.join(model_names)}")
    lines.append(f"  데이터: {data_file}")
    lines.append("=" * 70)

    # ────────────────────────────────────────────────────────────
    # 저장 임계값 기준
    # ────────────────────────────────────────────────────────────
    lines.append("\n  [저장 임계값 기준 탐지율]  (학습 후 저장된 threshold.txt 사용)")
    lines.append("  " + _sep())
    lines.append("  " + _header())
    lines.append("  " + _sep())

    fp_row = rjust("오탐율", W_SC)
    for m in model_names:
        fp_row += _fmt(fp_rates.get(m, float("nan")), W_COL)
    lines.append("  " + fp_row)
    lines.append("  " + _sep())

    prev_h = None
    t_sums = {m: [] for m in model_names}
    h_sums = {m: [] for m in model_names}
    for sc_name, is_holdout in anom_rows:
        if prev_h is not None and is_holdout != prev_h:
            lines.append("  " + _sep())
            lines.append("  ── 홀드아웃 (학습 미포함) ──")
        prefix = "[H] " if is_holdout else "    "
        row = rjust(prefix + sc_name, W_SC)
        for m in model_names:
            v = det_by_threshold.get(m, {}).get(sc_name, float("nan"))
            row += _fmt(v, W_COL)
            (h_sums if is_holdout else t_sums)[m].append(v)
        lines.append("  " + row)
        prev_h = is_holdout

    lines.append("  " + _sep())
    for label, sd in [("학습 평균", t_sums), ("홀드아웃 평균 ← 일반화", h_sums)]:
        row = rjust(label, W_SC)
        for m in model_names:
            row += _fmt(_avg(sd[m]), W_COL)
        lines.append("  " + row)
    lines.append("  " + _sep("═"))

    # ────────────────────────────────────────────────────────────
    # FP 목표값별 탐지율
    # ────────────────────────────────────────────────────────────
    for fp_t in sorted(det_at_fp):
        model_dr = det_at_fp[fp_t]
        lines.append(f"\n  [FP ≈ {fp_t:.0f}% 기준 탐지율]")
        lines.append("  " + _sep())
        lines.append("  " + _header())
        lines.append("  " + _sep())

        prev_h = None
        t_fp = {m: [] for m in model_names}
        h_fp = {m: [] for m in model_names}
        for sc_name, is_holdout in anom_rows:
            if prev_h is not None and is_holdout != prev_h:
                lines.append("  " + _sep())
                lines.append("  ── 홀드아웃 ──")
            prefix = "[H] " if is_holdout else "    "
            row = rjust(prefix + sc_name, W_SC)
            for m in model_names:
                v = model_dr.get(m, {}).get(sc_name, float("nan"))
                row += _fmt(v, W_COL)
                (h_fp if is_holdout else t_fp)[m].append(v)
            lines.append("  " + row)
            prev_h = is_holdout

        lines.append("  " + _sep())
        for label, sd in [("학습 평균", t_fp), ("홀드아웃 평균 ← 일반화", h_fp)]:
            row = rjust(label, W_SC)
            for m in model_names:
                row += _fmt(_avg(sd[m]), W_COL)
            lines.append("  " + row)
        lines.append("  " + _sep("═"))

    # ────────────────────────────────────────────────────────────
    # 모델별 요약
    # ────────────────────────────────────────────────────────────
    best_fp = max(det_at_fp) if det_at_fp else None
    fp_col_w = 14

    lines.append("\n  [모델별 종합 요약]")
    header_row = f"  {'모델':<22} {'오탐율':>7}"
    for fp_t in sorted(det_at_fp):
        header_row += rjust(f"FP{fp_t:.0f}%학습평균", fp_col_w)
    header_row += rjust("홀드아웃(최고FP)", fp_col_w + 2)
    header_row += rjust("학습시간", 10)
    lines.append(header_row)
    lines.append("  " + "─" * (22 + 8 + fp_col_w * len(det_at_fp) + fp_col_w + 12))

    for m in model_names:
        fp_str = f"{fp_rates[m]:.1f}%" if m in fp_rates else "-"
        fp_avgs = ""
        for fp_t in sorted(det_at_fp):
            vals = [v for v in det_at_fp[fp_t].get(m, {}).values()
                    if not math.isnan(v)]
            fp_avgs += rjust(f"{np.mean(vals):.1f}%" if vals else "-", fp_col_w)
        if best_fp is not None:
            ho_vals = [
                v for sc, v in det_at_fp[best_fp].get(m, {}).items()
                if any(sc == n and ih for n, _, _, ih in SCENARIO_MAKERS)
                and not math.isnan(v)
            ]
            ho_str = f"{np.mean(ho_vals):.1f}%" if ho_vals else "-"
        else:
            ho_str = "-"
        t_sec = train_time.get(m, 0.0)
        t_str = f"{t_sec/60:.1f}분" if t_sec > 0 else "-"
        lines.append(
            f"  {m:<22} {fp_str:>7}{fp_avgs}"
            f"{rjust(ho_str, fp_col_w + 2)}{rjust(t_str, 10)}"
        )

    lines.append("")
    return "\n".join(lines)


# ── CSV 저장 ──────────────────────────────────────────────────────────
def save_csv(out_path, model_names, fp_rates, det_by_threshold, det_at_fp):
    """비교 결과를 CSV 로 저장"""
    anom_rows = [(n, ih) for n, _, ia, ih in SCENARIO_MAKERS if ia]

    fieldnames = ["scenario", "is_holdout"]
    for m in model_names:
        s = m.replace("sup_", "s_")
        fieldnames.append(f"{s}_default")
        for fp_t in sorted(det_at_fp):
            fieldnames.append(f"{s}_fp{fp_t:.0f}")

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv_mod.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        # 오탐율 행 (메타)
        meta = {"scenario": "오탐율(저장임계값)", "is_holdout": ""}
        for m in model_names:
            s = m.replace("sup_", "s_")
            meta[f"{s}_default"] = f"{fp_rates.get(m,''):.2f}%" if m in fp_rates else ""
            for fp_t in sorted(det_at_fp):
                meta[f"{s}_fp{fp_t:.0f}"] = f"{fp_t:.0f}% 목표"
        writer.writerow(meta)

        for sc_name, is_holdout in anom_rows:
            row = {"scenario": sc_name, "is_holdout": "홀드아웃" if is_holdout else "학습"}
            for m in model_names:
                s = m.replace("sup_", "s_")
                v_d = det_by_threshold.get(m, {}).get(sc_name, "")
                row[f"{s}_default"] = f"{v_d:.1f}%" if isinstance(v_d, float) else ""
                for fp_t in sorted(det_at_fp):
                    v = det_at_fp[fp_t].get(m, {}).get(sc_name, "")
                    row[f"{s}_fp{fp_t:.0f}"] = f"{v:.1f}%" if isinstance(v, float) else ""
            writer.writerow(row)


# ── 메인 ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AIS 이상 탐지 멀티모델 학습/탐지율 비교 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # 단계 선택
    parser.add_argument("--preprocess", action="store_true",
                        help="원본 CSV → 전처리 실행 (preprocess.py 호출)")
    parser.add_argument("--train",      action="store_true",
                        help="모델 학습 실행")
    parser.add_argument("--eval",       action="store_true",
                        help="탐지율 비교 평가 실행")

    # 데이터 경로
    parser.add_argument("--raw_data",  type=str, default=_DEFAULT_RAW_DATA,
                        help=f"원본 AIS CSV 폴더 (기본: {_DEFAULT_RAW_DATA})")
    parser.add_argument("--data_file", type=str, default=_DEFAULT_DATA_FILE,
                        help=f"전처리 완료 CSV 경로 (기본: {_DEFAULT_DATA_FILE})")

    # 모델 선택
    parser.add_argument("--models",     nargs="+", metavar="MODEL",
                        help=f"모델 목록. 가능: {', '.join(ALL_MODELS)}")
    parser.add_argument("--all_models", action="store_true", help="전체 14개 모델")
    parser.add_argument("--unsup",      action="store_true", help="비지도 9개 모델")
    parser.add_argument("--sup",        action="store_true", help="지도 5개 모델")

    # 학습 옵션
    parser.add_argument("--epochs",       type=int,   default=None)
    parser.add_argument("--lr",           type=float, default=None)
    parser.add_argument("--n_normal",     type=int,   default=None,
                        help="정상 학습 샘플 수 (train_supervised.py --n_normal)")
    parser.add_argument("--n_anom_train", type=int,   default=None,
                        help="이상 학습 샘플 수 (train_supervised.py --n_anom)")
    parser.add_argument("--max_mmsi",     type=int,   default=None)
    parser.add_argument("--skip_trained", action="store_true",
                        help="이미 학습된 모델은 재학습 건너뜀")

    # 평가 옵션
    parser.add_argument("--n_anom",        type=int,   default=500,
                        help="시나리오당 이상 시퀀스 수 (기본: 500)")
    parser.add_argument("--n_eval_normal", type=int,   default=3000,
                        help="오탐율 계산용 정상 시퀀스 수 (기본: 3000, 0=전체)")
    parser.add_argument("--fp_targets",    type=float, nargs="+",
                        default=[1.0, 5.0, 10.0],
                        help="비교 기준 오탐율 목표값 %% (기본: 1 5 10)")

    # 출력
    parser.add_argument("--output", type=str, default=None,
                        help="결과 파일 접두사 (기본: output/pipeline/comparison_TIMESTAMP)")

    args = parser.parse_args()

    # ── 모델 목록 결정 ────────────────────────────────────────────
    model_names = []
    if args.all_models:
        model_names = list(ALL_MODELS)
    if args.unsup:
        model_names += [m for m in UNSUP_MODELS if m not in model_names]
    if args.sup:
        model_names += [m for m in SUP_MODELS if m not in model_names]
    if args.models:
        model_names += [m for m in args.models if m not in model_names]

    # 학습/평가가 필요한 경우에만 모델 목록 필수
    needs_models = args.train or args.eval
    if needs_models and not model_names:
        parser.print_help()
        print("\n오류: --models, --all_models, --unsup, --sup 중 하나 이상을 지정하세요.")
        sys.exit(1)

    unknown = [m for m in model_names if m not in ALL_MODELS]
    if unknown:
        print(f"오류: 알 수 없는 모델: {', '.join(unknown)}")
        print(f"가능: {', '.join(ALL_MODELS)}")
        sys.exit(1)

    if not args.preprocess and not args.train and not args.eval:
        parser.print_help()
        print("\n오류: --preprocess, --train, --eval 중 하나 이상을 지정하세요.")
        sys.exit(1)

    os.makedirs(PIPELINE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_prefix = args.output or os.path.join(PIPELINE_DIR, f"comparison_{timestamp}")
    out_txt    = out_prefix + ".txt"
    out_csv    = out_prefix + ".csv"

    print(f"\n{'='*60}")
    print(f"  AIS 이상 탐지 파이프라인  -  {timestamp}")
    if model_names:
        print(f"  모델 ({len(model_names)}): {', '.join(model_names)}")
    print(f"  단계: "
          + ("전처리 → " if args.preprocess else "")
          + ("학습 → " if args.train else "")
          + ("평가" if args.eval else "").rstrip(" → "))
    print(f"  원본 데이터: {args.raw_data}")
    print(f"  전처리 파일: {args.data_file}")
    if args.eval:
        print(f"  시나리오당 이상: {args.n_anom}  |  정상: {args.n_eval_normal or '전체'}")
        print(f"  FP 목표값: {args.fp_targets}")
    if args.eval and model_names:
        print(f"  결과: {out_txt}")
    print(f"{'='*60}\n")

    # ─────────────────────────────────────────────────────────────
    # 1. 전처리 단계
    # ─────────────────────────────────────────────────────────────
    if args.preprocess:
        print("[전처리 단계]")
        ok = run_preprocess(args.raw_data, args.data_file)
        if not ok:
            print("\n전처리 실패. --preprocess 없이 기존 파일로 진행하거나 경로를 확인하세요.")
            if not args.train and not args.eval:
                sys.exit(1)

    # ─────────────────────────────────────────────────────────────
    # 2. 학습 단계
    # ─────────────────────────────────────────────────────────────
    train_time    = {m: 0.0  for m in model_names}
    train_success = {m: True for m in model_names}

    if args.train:
        if not os.path.exists(args.data_file):
            print(f"\n오류: 학습 데이터 없음: {args.data_file}")
            print("  먼저 --preprocess 를 실행하거나 --data_file 경로를 확인하세요.")
            sys.exit(1)

        extra = []
        if args.epochs:       extra += ["--epochs",   str(args.epochs)]
        if args.lr:           extra += ["--lr",       str(args.lr)]
        if args.max_mmsi:     extra += ["--max_mmsi", str(args.max_mmsi)]
        if args.n_normal:     extra += ["--n_normal", str(args.n_normal)]
        if args.n_anom_train: extra += ["--n_anom",   str(args.n_anom_train)]

        print(f"[학습 단계]  모델 {len(model_names)}개\n")
        for i, name in enumerate(model_names, 1):
            print(f"  ({i}/{len(model_names)}) {name}")
            if args.skip_trained and model_exists(name):
                print("    → 이미 학습됨, 건너뜀")
                continue
            ok, elapsed = run_training(name, args.data_file, extra)
            train_time[name]    = elapsed
            train_success[name] = ok

        ok_list   = [m for m in model_names if train_success[m]]
        fail_list = [m for m in model_names if not train_success[m]]
        print(f"\n학습 완료: {len(ok_list)}/{len(model_names)}")
        if fail_list:
            print(f"학습 실패: {', '.join(fail_list)}")

    # ─────────────────────────────────────────────────────────────
    # 3. 평가 단계
    # ─────────────────────────────────────────────────────────────
    if not args.eval:
        print("\n평가 건너뜀 (--eval 미지정)")
        return

    eval_models = []
    for name in model_names:
        if not train_success.get(name, True):
            print(f"  ✗ {name}: 학습 실패 → 평가 건너뜀")
            continue
        if not model_exists(name):
            print(f"  ✗ {name}: 모델 파일 없음 → 평가 건너뜀 (먼저 --train 실행)")
            continue
        eval_models.append(name)

    if not eval_models:
        print("\n오류: 평가 가능한 모델이 없습니다.")
        sys.exit(1)

    print(f"\n[평가 단계]  모델 {len(eval_models)}개: {', '.join(eval_models)}")

    # 실제 정상 시퀀스 로드 (raw)
    n_eval_normal = None if args.n_eval_normal == 0 else args.n_eval_normal
    print("\n정상 시퀀스 로드 중...")
    raw_seqs = load_raw_normal_seqs(data_file=args.data_file, n_seqs=n_eval_normal)
    if raw_seqs is None:
        print(f"  ⚠ 전처리 CSV 없음 → 합성 정상 시퀀스 사용")

    # 모델별 스코어 수집
    all_normal_scores   = {}
    all_scenario_scores = {}
    loaded_thresholds   = {}

    for idx, name in enumerate(eval_models, 1):
        print(f"\n  ({idx}/{len(eval_models)}) {name} 스코어 계산 중...")
        try:
            session, mins, maxs, threshold = load_model(name)
        except FileNotFoundError as e:
            print(f"    ✗ {e}")
            continue

        is_sup = name.startswith("sup_")
        n_sc, s_sc = compute_scores(
            session, mins, maxs, is_sup,
            n_anom=args.n_anom,
            raw_seqs=raw_seqs,
            n_normal=n_eval_normal or 3000,
        )
        all_normal_scores[name]   = n_sc
        all_scenario_scores[name] = s_sc
        loaded_thresholds[name]   = threshold
        print(f"    정상 {len(n_sc):,}개, 시나리오 {len(s_sc)}개×{args.n_anom}개 완료")

    if not all_normal_scores:
        print("\n오류: 스코어를 계산한 모델이 없습니다.")
        sys.exit(1)

    final_models = list(all_normal_scores.keys())

    # 저장 임계값 기준 탐지율
    fp_rates         = {}
    det_by_threshold = {}
    for name in final_models:
        fp, dr = detection_rates(
            all_normal_scores[name],
            all_scenario_scores[name],
            loaded_thresholds[name],
        )
        fp_rates[name]         = fp
        det_by_threshold[name] = dr

    # FP 목표값별 탐지율
    det_at_fp = {}
    for fp_t in args.fp_targets:
        det_at_fp[fp_t] = {}
        for name in final_models:
            thr = float(np.percentile(all_normal_scores[name], 100.0 - fp_t))
            _, dr = detection_rates(
                all_normal_scores[name],
                all_scenario_scores[name],
                thr,
            )
            det_at_fp[fp_t][name] = dr

    # 결과 포맷 + 저장
    table = format_comparison_table(
        final_models, fp_rates, det_by_threshold, det_at_fp,
        train_time, args.data_file, timestamp,
    )

    print("\n" + table)

    with open(out_txt, "w", encoding="utf-8") as f:
        f.write(table)
    print(f"→ 텍스트 저장: {out_txt}")

    save_csv(out_csv, final_models, fp_rates, det_by_threshold, det_at_fp)
    print(f"→ CSV 저장:    {out_csv}")


if __name__ == "__main__":
    main()
