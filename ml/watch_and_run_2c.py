"""
감시 스크립트: eval 완료 + 다운로드 완료 대기 → 오케스트레이터 교체 → 2C 실행
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ML_DIR     = Path(__file__).parent
RESULT_DIR = Path(r"D:\JB-Pirate-King-ML-Results")
ALL_PRE    = Path(r"D:\JB-Pirate-King-AIS\preprocessed_all")
ENSEMBLE   = RESULT_DIR / "ensemble_full"
LOG        = RESULT_DIR / "watch_2c.log"

EVAL_PID     = 22472   # eval_all.py PID (완료됨)
DOWNLOAD_PID = 14364   # download_ais_allmonths.py PID (스트리밍 최적화 + 8워커)
ORCH_PID     = 19212   # 구버전 오케스트레이터 (이미 종료됨)


def wlog(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5
            )
            return str(pid) in result.stdout
        except Exception:
            return False


def read_best_ensemble() -> str:
    best_file = RESULT_DIR / "best_ensemble.txt"
    if best_file.exists():
        try:
            combo = best_file.read_text(encoding="utf-8").strip()
            # "lstm + dcdetect" → "lstm,dcdetect"
            clean = ",".join(m.strip() for m in combo.replace("+", ",").split(",") if m.strip())
            if clean:
                return clean
        except Exception:
            pass
    wlog("  best_ensemble.txt 없음 → 기본값 lstm,dcdetect")
    return "lstm,dcdetect"


def disk_free_gb():
    import shutil
    try: return shutil.disk_usage("D:\\").free / (1024**3)
    except: return 0.0


def current_alert_interval() -> int:
    """주간 30분 / 야간(23~7시) 60분"""
    h = time.localtime().tm_hour
    return 60 if (h >= 23 or h < 7) else 30


def notify(msg, title="JB | Phase2-2C"):
    try:
        sys.path.insert(0, str(ML_DIR))
        from notify import send_status_card
        send_status_card(
            title=title, stage=msg[:80], progress_pct=0,
            eta_str="진행 중", steps=[], resources={}, elapsed_str="-", notes=msg
        )
    except Exception:
        try:
            from notify import send
            send(msg, title)
        except Exception:
            pass


def notify_status(stage: str, progress: int, notes: str = "", eta: str = "?",
                  best_models: str = ""):
    """주기 상태 카드 알림 — 친화적 포맷"""
    pre_count = len(list(ALL_PRE.glob("*_preprocessed.csv")))
    target    = 1800   # 10년 × 180일 목표
    pre_pct   = int(pre_count / target * 100)
    disk_gb   = disk_free_gb()

    # 완료된 작업 목록
    completed_list = [
        "11개 탐지 알고리즘 단기 학습 + 성능 비교 완료",
        "최적 모델 조합 선정"
        + (f" ({best_models})" if best_models else " (진행 중)"),
    ]

    # 지금 하는 작업 설명 (단계별 분기)
    if progress <= 60:
        # eval 분석 중
        doing = (
            "11개 모델의 10년치 데이터 탐지 성능을 분석하고 있습니다.\n"
            f"→ 10년치(2016~2025) AIS 데이터 다운로드 병행 중: {pre_count}/{target}개 완료 ({pre_pct}%)\n"
            f"→ D드라이브 여유: {disk_gb:.0f}GB"
        )
        nxt = [
            "다운로드 완료 후 자동으로 앙상블 모델 학습 시작",
            "학습 완료 후 성능 최종 평가 (탐지율 측정)",
            "결과 Google Drive 업로드 + 최종 알림",
        ]
        tb = {
            "현재 eval 분석": "진행 중",
            f"10년치 다운로드 ({pre_count}/{target})": "진행 중 (수 시간 예상)",
            "앙상블 학습 (다운로드 완료 후)": "약 3~5시간",
            "최종 성능 평가": "약 1~2시간",
        }
    else:
        # 다운로드 대기 중
        doing = (
            f"10년치(2016~2025) 선박 AIS 위치 데이터를 다운로드하고 있습니다.\n"
            f"→ 진행: {pre_count}/{target}개 ({pre_pct}%) — 데이터가 많을수록 탐지 정확도 향상\n"
            f"→ D드라이브 여유: {disk_gb:.0f}GB"
        )
        nxt = [
            f"다운로드 완료 → {best_models or '최적 조합'} 앙상블 모델 자동 학습",
            "12가지 공격 시나리오로 탐지율 최종 평가",
            "결과 Google Drive 업로드 + 최종 알림",
        ]
        tb = {
            f"10년치 다운로드 ({pre_count}/{target})": eta if eta not in ("?", "-") else "진행 중",
            "앙상블 학습 (다운로드 완료 후)": "약 3~5시간",
            "최종 성능 평가": "약 1~2시간",
        }

    try:
        sys.path.insert(0, str(ML_DIR))
        from notify import send_status_card
        send_status_card(
            title="JB-Pirate-King 선박 이상탐지 | 학습 파이프라인 진행 중",
            progress_pct=progress,
            eta_str=eta,
            completed=completed_list,
            doing_now=doing,
            next_up=nxt,
            time_breakdown=tb,
            notes=notes if notes else None,
        )
    except Exception as e:
        notify(f"{stage} | {notes}", "JB | TASK 2 진행")


def main():
    wlog("=" * 60)
    wlog("감시 스크립트 시작")
    wlog(f"  eval PID: {EVAL_PID}")
    wlog(f"  download PID: {DOWNLOAD_PID}")
    wlog(f"  orch PID: {ORCH_PID}")
    wlog("=" * 60)

    # ── 1단계: eval 완료 대기 (주기 알림 포함) ────────────────────
    wlog("[1단계] eval 완료 대기...")
    last_alert = time.time() - current_alert_interval() * 60  # 즉시 첫 알림
    while is_alive(EVAL_PID):
        elapsed = (time.time() - last_alert) / 60
        if elapsed >= current_alert_interval():
            pre_count = len(list(ALL_PRE.glob("*_preprocessed.csv")))
            notify_status(
                stage="eval 분석 + 10년치 데이터 다운로드 병행",
                progress=55,
                eta="eval ~30min + 다운로드 ~수시간",
            )
            last_alert = time.time()
        time.sleep(30)
    wlog("  eval 완료!")

    best_models = read_best_ensemble()
    wlog(f"  최적 조합: {best_models}")
    notify(f"eval 완료!\n최적 조합: {best_models}\n다운로드 완료 대기 중...", "JB | eval 완료 — 최적 조합 확정")

    # ── 2단계: 다운로드 완료 대기 (주기 알림) ────────────────────
    wlog("[2단계] 다운로드 완료 대기...")
    last_alert = time.time() - current_alert_interval() * 60  # 즉시 첫 알림
    last_count = -1
    while is_alive(DOWNLOAD_PID):
        count = len(list(ALL_PRE.glob("*_preprocessed.csv")))
        if count != last_count:
            wlog(f"  preprocessed_all: {count}개")
            last_count = count
        elapsed = (time.time() - last_alert) / 60
        if elapsed >= current_alert_interval():
            notify_status(
                stage="10년치 AIS 데이터 다운로드 중",
                progress=65,
                eta="다운로드 완료 대기 중",
                best_models=best_models,
            )
            last_alert = time.time()
        time.sleep(60)
    wlog("  다운로드 완료!")

    pre_count = len(list(ALL_PRE.glob("*_preprocessed.csv")))
    wlog(f"  최종 preprocessed_all: {pre_count}개")

    # ── 3단계: 구버전 오케스트레이터 종료 ────────────────────────
    wlog(f"[3단계] 구버전 오케스트레이터(PID {ORCH_PID}) 종료...")
    if is_alive(ORCH_PID):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(ORCH_PID)],
                           capture_output=True, timeout=10)
            wlog(f"  PID {ORCH_PID} 종료됨")
        except Exception as e:
            wlog(f"  종료 실패: {e}")
    else:
        wlog(f"  PID {ORCH_PID} 이미 종료됨")

    # ── 4단계: 1월 데이터 통합 ────────────────────────────────────
    wlog("[4단계] 1월 preprocessed → preprocessed_all 통합")
    import shutil
    PREPROC = Path(r"D:\JB-Pirate-King-AIS\preprocessed")
    jan_files = [f for f in PREPROC.glob("ais-*-01-*_preprocessed.csv")
                 if int(f.stem.split("-")[1]) >= 2016]
    wlog(f"  1월 파일 {len(jan_files)}개")
    for src in jan_files:
        dst = ALL_PRE / src.name
        if not dst.exists():
            try:
                os.link(src, dst)
            except Exception:
                shutil.copy2(src, dst)
    pre_count = len(list(ALL_PRE.glob("*_preprocessed.csv")))
    wlog(f"  통합 후: {pre_count}개")

    # ── 5단계: 2-C 앙상블 학습 ────────────────────────────────────
    wlog(f"[5단계] 2-C 앙상블 학습 시작: {best_models}")
    ENSEMBLE.mkdir(parents=True, exist_ok=True)
    cache_all = ENSEMBLE / "train_data_cache.pt"
    if cache_all.exists():
        cache_all.unlink()
        wlog("  old cache removed")

    notify(
        f"2-C 앙상블 학습 시작!\n모델: {best_models}\nFPR 1% 임계값",
        "JB | TASK 2-C 시작"
    )

    train_log = str(RESULT_DIR / "train_ensemble_full.log")
    ok = False
    for attempt in range(1, 4):
        wlog(f"  학습 attempt {attempt}/3")
        with open(train_log, "a", encoding="utf-8") as lf:
            lf.write(f"\n===== 2C attempt {attempt} | {time.strftime('%H:%M:%S')} =====\n")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ML_DIR / "train_benchmark.py"),
             "--model",         best_models,
             "--input",         str(ALL_PRE),
             "--output",        str(ENSEMBLE),
             "--cache",         str(cache_all),
             "--threshold-pct", "99"],
            stdout=open(train_log, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=str(ML_DIR),
        )
        proc.wait()
        if proc.returncode == 0:
            wlog("  학습 완료!")
            ok = True
            break
        wlog(f"  학습 실패 (exit={proc.returncode})")
        if attempt < 3:
            time.sleep(30)

    # ── 6단계: 2-D eval ───────────────────────────────────────────
    wlog("[6단계] 2-D 앙상블 eval 실행")
    eval_log = str(RESULT_DIR / "eval_ensemble_full.log")
    # test_files.json이 있으면 테스트 전용 데이터만 평가
    test_files_path = str(RESULT_DIR / "test_files.json")
    eval_cmd = [sys.executable, "-u", str(ML_DIR / "eval_all.py"),
         "--model-dir", str(ENSEMBLE),
         "--data-dir",  str(ALL_PRE),
         "--n-normal",  "2000",
         "--attacks-per-type", "200",
         "--bootstrap", "100",
         "--severity",  "all"]
    if Path(test_files_path).exists():
        eval_cmd += ["--test-files", test_files_path]
        wlog(f"  테스트 파일 목록 사용: {test_files_path}")
    eval_proc = subprocess.Popen(
        eval_cmd,
        stdout=open(eval_log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        cwd=str(ML_DIR),
    )
    eval_proc.wait()
    wlog("  eval 완료!")

    # ── 7단계: 완료 알림 ──────────────────────────────────────────
    result_txt = ""
    if (ENSEMBLE / "eval_summary.txt").exists():
        try:
            result_txt = (ENSEMBLE / "eval_summary.txt").read_text(encoding="utf-8")[:400]
        except Exception:
            pass

    notify(
        f"Phase 2 TASK 2 완료!\n조합: {best_models}\n\n{result_txt[:300]}",
        "JB | Phase 2 ALL DONE"
    )
    wlog("=" * 60)
    wlog(f"Phase 2 ALL DONE | 조합: {best_models}")
    wlog("=" * 60)

    # GDrive 업로드 마커
    try:
        sys.path.insert(0, str(ML_DIR))
        from gdrive_upload_helper import mark_upload_ready
        mark_upload_ready()
    except Exception as e:
        wlog(f"  업로드 마커 실패: {e}")


if __name__ == "__main__":
    main()
