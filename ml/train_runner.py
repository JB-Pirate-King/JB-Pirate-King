"""
독립 학습 실행기 (train_runner.py)
===================================
train_benchmark.py를 subprocess로 실행하되:
  - stdout/stderr를 직접 로그 파일에 저장
  - PowerShell 파이프에 종속되지 않음 (터미널 닫혀도 계속)
  - 완료/실패 시 Discord 알림
  - 실패 시 최대 MAX_RETRY 회 자가복구

사용:
  python train_runner.py
  python train_runner.py --model lstm,timesnet --log D:\...\my.log
"""

import argparse
import io
import os
import subprocess
import sys
import time
from pathlib import Path

# UTF-8 강제
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ML_DIR     = Path(__file__).parent
RESULT_DIR = Path(r"D:\JB-Pirate-King-ML-Results")
PREPROC    = Path(r"D:\JB-Pirate-King-AIS\preprocessed")
DEFAULT_LOG = RESULT_DIR / "train_full_v3.log"
MAX_RETRY   = 3


def notify(msg: str, title: str = "JB-Pirate-King | Training"):
    try:
        subprocess.run(
            [sys.executable, str(ML_DIR / "notify.py"), msg, title],
            timeout=10, capture_output=True
        )
    except Exception:
        pass


def run_training(model: str, log_path: Path, attempt: int) -> int:
    """train_benchmark.py 실행, stdout/stderr를 log_path에 저장. returncode 반환."""
    cmd = [
        sys.executable, "-u",
        str(ML_DIR / "train_benchmark.py"),
        "--model", model,
        "--input", str(PREPROC),
    ]
    print(f"[train_runner] 실행 (attempt {attempt}): {' '.join(cmd[:6])}...")
    print(f"[train_runner] 로그: {log_path}")

    with open(log_path, "a", encoding="utf-8", errors="replace") as log_f:
        log_f.write(f"\n===== train_runner attempt {attempt} | {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        log_f.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

        for line in proc.stdout:
            # 콘솔 + 파일 동시 출력
            sys.stdout.write(line)
            sys.stdout.flush()
            log_f.write(line)
            log_f.flush()

        proc.wait()

    return proc.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str,
                        default="lstm,timesnet,usad,dcdetect,iforest,deepsvdd,dagmm",
                        help="학습할 모델 (콤마 구분)")
    parser.add_argument("--log", type=str, default=str(DEFAULT_LOG),
                        help="로그 파일 경로")
    parser.add_argument("--retries", type=int, default=MAX_RETRY)
    args = parser.parse_args()

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log)

    notify(
        f"학습 재시작 (train_runner)\n"
        f"모델: {args.model}\n"
        f"로그: {args.log}",
        "JB | 학습 재시작"
    )

    t_start = time.time()
    for attempt in range(1, args.retries + 1):
        rc = run_training(args.model, log_path, attempt)
        elapsed = round((time.time() - t_start) / 60, 1)

        if rc == 0:
            # 성공
            onnx_count = len(list(RESULT_DIR.glob("model_*.onnx")))
            notify(
                f"학습 완료! (attempt {attempt})\n"
                f"ONNX: {onnx_count}개\n"
                f"소요: {elapsed}분",
                "JB | 학습 완료"
            )
            print(f"\n[train_runner] 완료! ONNX={onnx_count}개 | {elapsed}min")
            return 0
        else:
            notify(
                f"학습 실패 exit={rc} (attempt {attempt}/{args.retries})\n"
                f"경과: {elapsed}분\n"
                f"{'재시도 중...' if attempt < args.retries else '최대 재시도 초과'}",
                "JB | 학습 오류"
            )
            print(f"\n[train_runner] 실패 exit={rc} (attempt {attempt}/{args.retries})")
            if attempt < args.retries:
                print("60초 후 재시도...")
                time.sleep(60)

    elapsed = round((time.time() - t_start) / 60, 1)
    notify(
        f"학습 최대 재시도 초과 ({args.retries}회)\n"
        f"총 소요: {elapsed}분",
        "JB | 학습 실패"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
