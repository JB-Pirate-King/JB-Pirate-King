#!/usr/bin/env python3
# coding: utf-8
"""
run_pipeline.py
───────────────
AIS 이상 탐지 전체 파이프라인 실행기

디렉터리 구조:
    {data_dir}/
    ├── raw/                    원본 CSV (ais-YYYY-MM-DD.csv)
    └── preprocessed/
        ├── feat17/             피쳐 수 기반 자동 명명
        │   ├── ais_preprocessed.csv
        │   └── preprocess_meta.json
        └── feat17_v2/          --tag 지정 시

사용법:
    python run_pipeline.py --data_dir D:/ais_data/
    python run_pipeline.py --data_dir D:/ais_data/ --years 3
    python run_pipeline.py --data_dir D:/ais_data/ --years 3 --tag v2
    python run_pipeline.py --data_dir D:/ais_data/ --models usad tranad conv1d
    python run_pipeline.py --data_dir D:/ais_data/ --skip_train
    python run_pipeline.py --data_dir D:/ais_data/ --only_compare
    python run_pipeline.py --force_preprocess --data_dir D:/ais_data/
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE    = Path(__file__).parent
PYTHON  = sys.executable
PREPROCESS = str(HERE / "preprocess.py")
TRAIN      = str(HERE / "train_benchmark.py")
EVAL       = str(HERE / "eval_anomaly.py")
COMPARE    = str(HERE / "compare_models.py")

ALL_MODELS = ["usad", "tranad", "conv1d", "lstm", "tcn", "anomtrans", "dcdetect"]


# ── 유틸 ─────────────────────────────────────────────────────────
def _run(cmd: list[str], label: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  $ {' '.join(cmd)}")
    print(f"{'='*60}")
    t0  = time.time()
    ret = subprocess.run(cmd)
    elapsed = time.time() - t0
    if ret.returncode != 0:
        print(f"\n  [오류] {label} 실패 (exit={ret.returncode})")
        return False
    print(f"\n  [완료] {label}  ({elapsed:.1f}s)")
    return True


def _get_out_cols() -> list[str]:
    """preprocess.py 의 OUT_COLS 를 임포트해서 반환"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("preprocess", PREPROCESS)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.OUT_COLS


def _folder_name(out_cols: list[str], tag: str | None) -> str:
    """feat{N} 또는 feat{N}_{tag}"""
    n = len(out_cols)
    return f"feat{n}_{tag}" if tag else f"feat{n}"


def _find_preprocessed(prep_base: Path, out_cols: list[str]) -> Path | None:
    """
    preprocessed/ 하위 폴더를 순회해서
    현재 피쳐 목록과 일치하는 ais_preprocessed.csv 경로 반환
    """
    if not prep_base.exists():
        return None
    for meta_path in sorted(prep_base.glob("*/preprocess_meta.json")):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("features") == out_cols:
                csv_path = meta_path.parent / "ais_preprocessed.csv"
                if csv_path.exists():
                    return csv_path
        except Exception:
            continue
    return None


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AIS 이상 탐지 파이프라인",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data_dir",   type=str, required=False, default=None,
                        help="최상위 데이터 디렉터리 (raw/, preprocessed/ 포함)")
    parser.add_argument("--years",      type=int, default=3,
                        help="최근 N년치 데이터 사용")
    parser.add_argument("--tag",        type=str, default=None,
                        help="전처리 폴더 태그 (feat17_{tag})")
    parser.add_argument("--models",     type=str, nargs="+", default=ALL_MODELS,
                        help="학습/평가할 모델 목록")
    parser.add_argument("--output_dir", type=str, default="output",
                        help="모델 결과 저장 디렉터리")
    parser.add_argument("--force_preprocess", action="store_true",
                        help="피쳐 동일해도 강제 재전처리")
    parser.add_argument("--skip_train", action="store_true",
                        help="학습 스킵")
    parser.add_argument("--skip_eval",  action="store_true",
                        help="평가 스킵")
    parser.add_argument("--skip_compare", action="store_true",
                        help="비교표 생성 스킵")
    parser.add_argument("--only_compare", action="store_true",
                        help="비교표만 재생성")
    parser.add_argument("--epochs",     type=int,   default=None)
    parser.add_argument("--lr",         type=float, default=None)
    parser.add_argument("--batch_size", type=int,   default=None)
    parser.add_argument("--n_normal",   type=int,   default=3000)
    args = parser.parse_args()

    t_total = time.time()
    failed  = []

    print()
    print("=" * 60)
    print("  AIS 이상 탐지 파이프라인")
    print(f"  모델: {', '.join(args.models)}")
    print("=" * 60)

    # ── 비교표만 ──────────────────────────────────────────────────
    if args.only_compare:
        _run([PYTHON, COMPARE, "--output_dir", args.output_dir], "비교표 생성")
        print(f"\n  비교표: {args.output_dir}/model_comparison.csv")
        return

    # ── data_dir 필수 확인 ────────────────────────────────────────
    if not args.data_dir:
        print("[오류] --data_dir 을 지정해주세요.")
        print("  예) python run_pipeline.py --data_dir D:/ais_data/")
        sys.exit(1)

    data_dir  = Path(args.data_dir)
    raw_dir   = data_dir / "raw"
    prep_base = data_dir / "preprocessed"

    # ── [1] 전처리 ────────────────────────────────────────────────
    print(f"\n[1] 전처리 확인 중...")
    out_cols    = _get_out_cols()
    folder_name = _folder_name(out_cols, args.tag)
    prep_dir    = prep_base / folder_name
    existing    = None if args.force_preprocess else _find_preprocessed(prep_base, out_cols)

    if existing:
        data_path = str(existing)
        print(f"  → 기존 전처리 데이터 사용: {existing.parent.name}/")
        print(f"     피쳐 {len(out_cols)}개 일치, 전처리 스킵")
    else:
        reason = "--force_preprocess" if args.force_preprocess else \
                 f"피쳐 {len(out_cols)}개에 맞는 전처리 파일 없음"
        print(f"  → 전처리 실행 ({reason})")
        print(f"     저장 위치: {prep_dir}/")

        if not raw_dir.exists():
            print(f"  [오류] raw 디렉터리 없음: {raw_dir}")
            sys.exit(1)

        prep_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            PYTHON, PREPROCESS,
            str(raw_dir),
            "--output_dir", str(prep_dir),
            "--years",      str(args.years),
        ]
        if args.tag:
            cmd += ["--tag", args.tag]

        if not _run(cmd, f"전처리 (최근 {args.years}년치 → {folder_name}/)"):
            print("  전처리 실패. 파이프라인 중단.")
            sys.exit(1)

        data_path = str(prep_dir / "ais_preprocessed.csv")

    # ── [2] 학습 ──────────────────────────────────────────────────
    if not args.skip_train:
        print(f"\n[2] 학습 ({len(args.models)}개 모델)")
        for i, model in enumerate(args.models, 1):
            cmd = [PYTHON, TRAIN, "--model", model, "--input", data_path]
            if args.epochs:     cmd += ["--epochs",     str(args.epochs)]
            if args.lr:         cmd += ["--lr",         str(args.lr)]
            if args.batch_size: cmd += ["--batch_size", str(args.batch_size)]
            if not _run(cmd, f"[{i}/{len(args.models)}] 학습: {model}"):
                failed.append(f"train:{model}")
    else:
        print(f"\n[2] 학습 스킵")

    # ── [3] 평가 ──────────────────────────────────────────────────
    if not args.skip_eval:
        print(f"\n[3] 평가 ({len(args.models)}개 모델)")
        for i, model in enumerate(args.models, 1):
            cmd = [
                PYTHON, EVAL,
                "--model",      model,
                "--data",       data_path,
                "--output_dir", args.output_dir,
                "--n_normal",   str(args.n_normal),
            ]
            if not _run(cmd, f"[{i}/{len(args.models)}] 평가: {model}"):
                failed.append(f"eval:{model}")
    else:
        print(f"\n[3] 평가 스킵")

    # ── [4] 비교표 ────────────────────────────────────────────────
    if not args.skip_compare and not args.skip_eval:
        print(f"\n[4] 비교표 생성")
        cmd = [PYTHON, COMPARE,
               "--output_dir", args.output_dir,
               "--models"] + args.models
        if not _run(cmd, "비교표 생성"):
            failed.append("compare")

    # ── 최종 요약 ─────────────────────────────────────────────────
    elapsed = time.time() - t_total
    print()
    print("=" * 60)
    print(f"  파이프라인 완료  ({elapsed:.1f}s)")
    if failed:
        print(f"  [실패] {', '.join(failed)}")
    else:
        print(f"  모든 단계 성공")
        print(f"  전처리: {folder_name}/")
        print(f"  비교표: {args.output_dir}/model_comparison.csv")
    print("=" * 60)


if __name__ == "__main__":
    main()
