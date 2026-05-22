"""
2C 학습 완료 후 처리: eval 실행 → Discord 알림 → GDrive 마커
"""
import subprocess, sys, time, os
from pathlib import Path

TRAIN_PID  = 12720
ML_DIR     = Path(__file__).parent
RESULT_DIR = Path(r"D:\JB-Pirate-King-ML-Results")
ENSEMBLE   = RESULT_DIR / "ensemble_full"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def wlog(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}"
    print(line, flush=True)
    try:
        with open(RESULT_DIR / "finish_2c.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def notify(msg, title="JB | Phase2-2C"):
    try:
        sys.path.insert(0, str(ML_DIR))
        from notify import send_status_card
        send_status_card(title=title, stage=msg[:80], progress_pct=0,
                         eta_str="-", steps=[], resources={}, elapsed_str="-", notes=msg)
    except Exception:
        try:
            from notify import send
            send(msg, title)
        except Exception:
            pass


def is_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=5)
            return str(pid) in r.stdout
        except Exception:
            return False


def main():
    wlog("=" * 60)
    wlog(f"finish_2c 시작 — 학습 PID {TRAIN_PID} 완료 대기")
    wlog("=" * 60)

    # 학습 완료 대기
    last_alert = time.time()
    while is_alive(TRAIN_PID):
        elapsed_since = (time.time() - last_alert) / 60
        if elapsed_since >= 30:
            # 30분마다 진행 중 알림
            onnx_done = len(list(ENSEMBLE.glob("model_*.onnx"))) if ENSEMBLE.exists() else 0
            notify(f"2-C LSTM 학습 진행 중...\nONNX 완료: {onnx_done}/1\n계속 기다리는 중", "JB | 2C 학습 진행")
            last_alert = time.time()
        time.sleep(60)

    wlog("학습 프로세스 종료 확인")

    # 결과 확인
    onnx = list(ENSEMBLE.glob("model_*.onnx")) if ENSEMBLE.exists() else []
    wlog(f"ONNX 파일: {len(onnx)}개")

    if not onnx:
        wlog("경고: ONNX 없음 — 학습 실패 가능성")
        notify("2-C 학습 종료, ONNX 없음 — 실패 가능성!", "JB | 2C 결과 이상")
    else:
        notify(f"2-C 학습 완료!\nONNX: {[f.name for f in onnx]}\neval 시작...", "JB | 2C 학습 완료")

    # eval 실행
    wlog("[eval] 2-D 앙상블 eval 시작")
    eval_log = str(RESULT_DIR / "eval_ensemble_full.log")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    test_files_path = RESULT_DIR / "test_files.json"
    eval_cmd = [sys.executable, "-u", str(ML_DIR / "eval_all.py"),
         "--model-dir", str(ENSEMBLE),
         "--data-dir",  str(Path(r"D:\JB-Pirate-King-AIS\preprocessed_all")),
         "--n-normal",  "2000",
         "--attacks-per-type", "200",
         "--bootstrap", "100",
         "--severity",  "all"]
    if test_files_path.exists():
        eval_cmd += ["--test-files", str(test_files_path)]
    eval_proc = subprocess.Popen(
        eval_cmd,
        stdout=open(eval_log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        cwd=str(ML_DIR),
        env=env,
    )
    eval_proc.wait()
    wlog(f"eval 완료 (exit={eval_proc.returncode})")

    # 결과 읽기
    result_txt = ""
    summary = ENSEMBLE / "eval_summary.txt"
    if summary.exists():
        try:
            result_txt = summary.read_text(encoding="utf-8")[:400]
        except Exception:
            pass

    notify(
        f"Phase 2 ALL DONE!\n모델: lstm (Det@1%FPR)\n\n{result_txt[:300]}",
        "JB | Phase 2 ALL DONE"
    )
    wlog("=" * 60)
    wlog("Phase 2 ALL DONE")
    wlog("=" * 60)

    # GDrive 업로드 마커
    try:
        from gdrive_upload_helper import mark_upload_ready
        mark_upload_ready()
    except Exception as e:
        wlog(f"업로드 마커 실패: {e}")


if __name__ == "__main__":
    main()
