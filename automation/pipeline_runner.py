"""
automation/pipeline_runner.py

전체 자동화 파이프라인 오케스트레이터.
ml/pipeline.py 실행 + MLflow + Google Sheets + Notion + GitHub 릴리즈 + Slack/Discord 알림.

사용법:
    python automation/pipeline_runner.py --models conv1d tranad dcdetect --epochs 10
    python automation/pipeline_runner.py --models conv1d --epochs 5 --release --tag v0.2.0
    python automation/pipeline_runner.py --eval-only --models conv1d

옵션:
    --models      학습할 모델 목록 (기본: conv1d tranad dcdetect)
    --epochs      에포크 수 (기본: 10)
    --n_anom      이상 시나리오 수 (기본: 200)
    --fp_targets  오탐율 목표 (기본: 1)
    --skip_trained 이미 학습된 모델 스킵
    --eval-only   평가만 실행
    --release     학습 완료 후 GitHub 릴리즈 생성
    --tag         릴리즈 태그 (--release 와 함께)
    --base_dir    모델/출력 루트 디렉토리 (기본: D:\\)
"""

import argparse
import subprocess
import sys
import time
import os
import glob
from datetime import datetime
from pathlib import Path

# automation 패키지 경로 등록
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation.config import OUTPUT_DIR, MODELS_DIR, DATA_FILE, BASE_DIR
from automation.mlflow_tracker import patch_pipeline_with_mlflow
from automation.notify import Notifier
from automation.sheets_tracker import SheetsTracker
from automation.notion_reporter import NotionReporter
from automation.github_release import GithubReleaser


def parse_args():
    p = argparse.ArgumentParser(description="AIS 이상탐지 자동화 파이프라인")
    p.add_argument("--models", nargs="+", default=["conv1d", "tranad", "dcdetect"])
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--n_anom", type=int, default=200)
    p.add_argument("--fp_targets", type=int, default=1)
    p.add_argument("--skip_trained", action="store_true")
    p.add_argument("--eval-only", action="store_true")
    p.add_argument("--release", action="store_true")
    p.add_argument("--tag", type=str, default="")
    p.add_argument("--base_dir", type=str, default=BASE_DIR)
    return p.parse_args()


def run_pipeline(args) -> tuple[bool, str]:
    """ml/pipeline.py 실행, (성공여부, 오류메시지) 반환"""
    cmd = [sys.executable, "ml/pipeline.py"]

    if args.eval_only:
        cmd += ["--eval"]
    else:
        cmd += ["--train", "--eval"]

    cmd += ["--models", *args.models]
    cmd += ["--epochs", str(args.epochs)]
    cmd += ["--n_anom", str(args.n_anom)]
    cmd += ["--fp_targets", str(args.fp_targets)]
    cmd += ["--base_dir", args.base_dir]
    if args.skip_trained:
        cmd.append("--skip_trained")

    print(f"[runner] 실행: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode != 0:
        return False, f"exit code {result.returncode}"
    return True, ""


def parse_eval_results(model: str, base_dir: str) -> dict:
    """최신 per-model CSV에서 탐지율 요약 추출"""
    out_dir = os.path.join(base_dir, "ais_output", "pipeline")
    pattern = os.path.join(out_dir, f"{model}_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return {"train_dr": 0.0, "holdout_dr": 0.0, "fp_rate": 1.0}

    import csv
    train_drs, holdout_drs = [], []
    with open(files[-1], newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            group = row.get("group", "")
            dr = float(row.get("detection_rate", 0))
            if group.startswith("F") or group.startswith("G"):
                holdout_drs.append(dr)
            else:
                train_drs.append(dr)

    return {
        "train_dr":   sum(train_drs)   / len(train_drs)   if train_drs   else 0.0,
        "holdout_dr": sum(holdout_drs) / len(holdout_drs) if holdout_drs else 0.0,
        "fp_rate":    1.0,
    }


def find_latest_comparison(base_dir: str) -> tuple[str, str]:
    out_dir = os.path.join(base_dir, "ais_output", "pipeline")
    txts = sorted(glob.glob(os.path.join(out_dir, "comparison_*.txt")))
    csvs = sorted(glob.glob(os.path.join(out_dir, "comparison_*.csv")))
    return (txts[-1] if txts else ""), (csvs[-1] if csvs else "")


def main():
    args = parse_args()
    notifier = Notifier()
    sheets   = SheetsTracker()
    notion   = NotionReporter()
    releaser = GithubReleaser()

    print(f"\n{'='*60}")
    print(f"AIS 자동화 파이프라인 시작")
    print(f"모델: {args.models}, 에포크: {args.epochs}, eval-only: {args.eval_only}")
    print(f"{'='*60}\n")

    start = time.time()
    success, err = run_pipeline(args)
    elapsed_min = (time.time() - start) / 60

    if not success:
        print(f"[runner] ❌ 파이프라인 실패: {err}")
        notifier.pipeline_failed(str(args.models), err)
        sys.exit(1)

    print(f"\n[runner] ✅ 파이프라인 완료 ({elapsed_min:.1f}분)\n")

    model_results = {}
    release_url = ""

    for model in args.models:
        res = parse_eval_results(model, args.base_dir)
        model_results[model] = res

        # MLflow 기록
        patch_pipeline_with_mlflow(model, args.epochs)

        # Google Sheets 기록
        sheets.log_result(
            model=model,
            epochs=args.epochs,
            train_dr=res["train_dr"],
            holdout_dr=res["holdout_dr"],
            fp_rate=res["fp_rate"],
        )

        # Notion 페이지 생성
        notion.create_experiment_page(
            model=model,
            epochs=args.epochs,
            train_dr=res["train_dr"],
            holdout_dr=res["holdout_dr"],
            fp_rate=res["fp_rate"],
        )

        # Slack + Discord 알림
        notifier.training_complete(
            model=model,
            dr=res["train_dr"],
            holdout=res["holdout_dr"],
            fp=res["fp_rate"],
            elapsed_min=elapsed_min,
            version=args.tag.lstrip("v") if args.tag else "",
        )

    # GitHub 릴리즈 생성 (--release 플래그)
    if args.release and args.tag:
        cmp_txt, cmp_csv = find_latest_comparison(args.base_dir)
        notes = {m: {"train_dr": model_results[m]["train_dr"],
                     "holdout_dr": model_results[m]["holdout_dr"]}
                 for m in args.models}

        release_url = releaser.create_release(
            tag=args.tag,
            title=f"{args.tag} — {' / '.join(args.models)}",
            models=args.models,
            notes=notes,
            comparison_txt=cmp_txt,
            comparison_csv=cmp_csv,
        )
        if release_url:
            notifier.release_created(args.tag, release_url, args.models)

    print(f"\n{'='*60}")
    print(f"파이프라인 완료 요약")
    for m, r in model_results.items():
        print(f"  {m}: DR {r['train_dr']:.1f}% / Holdout {r['holdout_dr']:.1f}%")
    if release_url:
        print(f"  릴리즈: {release_url}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
