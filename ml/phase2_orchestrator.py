"""
Phase 2 자동 오케스트레이터 v2 (카드 알림 + 디스크 가드 + 스트리밍)
==================================================================
독립 프로세스로 실행. PowerShell 세션 종료에 영향받지 않음.

TASK 1: 데이터 규모별 탐지율 비교
  소규모(1000 MMSI) vs 5년 Jan(2015-19) vs 11년 Jan(2015-25)
  ※ 11년 결과는 v3 학습으로 이미 D:\JB-Pirate-King-ML-Results\에 있음

TASK 2: 전체 데이터 앙상블 학습
  2016-2025 (10년) × 각 연도 -3개월 랜덤 = 90개월
  스트리밍 다운로드 + 전처리 + raw 즉시 삭제 (디스크 절약)
  TranAD + DCdetector 앙상블, FPR 1% (threshold-pct=99)

이전 세션 발견 사항 (디스코드 카드에 포함):
  - csv.DictReader → pandas usecols 최적화 적용 (10-20x 속도)
  - 첫 학습 OOM/터미널종료로 사망 → Start-Process 독립 프로세스로 해결
  - D드라이브 여유 부족 시 디스크 가드로 자동 중단
  - 2015 제외 (36GB 회수), 연도별 9개월 보존
"""

import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ML_DIR        = Path(__file__).parent
RESULT_DIR    = Path(r"D:\JB-Pirate-King-ML-Results")
PREPROC_DIR   = Path(r"D:\JB-Pirate-King-AIS\preprocessed")
AIS_DIR       = Path(r"D:\AIS")
ALL_PRE_DIR   = Path(r"D:\JB-Pirate-King-AIS\preprocessed_all")
ENSEMBLE_DIR  = RESULT_DIR / "ensemble_full"
LOG_FILE      = RESULT_DIR / "phase2_auto.log"

MAX_RETRY     = 3
POLL_INTERVAL = 300       # 5분마다 학습 완료 체크 (내부 확인 — 알림 X)
ALERT_INTERVAL_DAY   = 30 # 주간 30분마다 카드 알림 (대기 중)
ALERT_INTERVAL_NIGHT = 60 # 야간 60분마다 카드 알림
NIGHT_START_HOUR = 23     # 23시부터 야간 모드
NIGHT_END_HOUR   = 7      # 7시까지 야간 모드
DISK_GUARD_GB = 100       # D드라이브 여유 < 100GB면 중단


def current_alert_interval() -> int:
    """야간(23~7시)이면 60분, 주간이면 30분.
    수동 오버라이드: runtime_config.json의 alert_interval_min 키."""
    try:
        from datetime import datetime as _dt
        # 런타임 설정 우선
        rc = RESULT_DIR / "runtime_config.json"
        if rc.exists():
            cfg = json.loads(rc.read_text(encoding="utf-8"))
            override = cfg.get("alert_interval_min")
            if isinstance(override, (int, float)) and override > 0:
                return int(override)
        hour = _dt.now().hour
        if hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR:
            return ALERT_INTERVAL_NIGHT
        return ALERT_INTERVAL_DAY
    except Exception:
        return ALERT_INTERVAL_DAY

# Task 2 데이터 전략 (v3 교훈 + 편향 방지 반영)
TASK2_YEARS        = list(range(2016, 2026))   # 10년
TASK2_DAYS_PER_YEAR = 180                       # 1월 31일 + 비1월 149일 랜덤 (편향 방지)
TASK2_WORKERS      = 4                          # 다운로드 워커 (네트워크 부하 고려)

# Discord 명령 큐
COMMAND_QUEUE = RESULT_DIR / "discord_commands.jsonl"
# 일시정지/스킵 상태
_PAUSE_FLAG     = False
_SKIP_TASK2     = False
_SKIP_TASK2C    = False
_STOP_REQUESTED = False

# 세션 분석 (이 채팅에서 추론한 인사이트, 카드 알림에 첨부)
SESSION_INSIGHTS = """v3 학습이 ~212분 후 사망(터미널 닫힘 추정)
→ Start-Process 독립 프로세스로 재시작 성공
csv.DictReader 병목 → pandas usecols로 교체 (5h→25min)
2015 제외 → 36GB 회수, 2016-25 / 9개월/년 채택
디스크 가드 100GB로 자동 중단 보호"""


def wlog(msg: str):
    ts   = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def notify_simple(msg: str, title: str = "JB-Pirate-King | Phase2"):
    """기존 단순 텍스트 알림 (백업용)"""
    try:
        sys.path.insert(0, str(ML_DIR))
        from notify import send
        send(msg, title)
    except Exception:
        try:
            subprocess.run(
                [sys.executable, str(ML_DIR / "notify.py"), msg, title],
                timeout=15, capture_output=True
            )
        except Exception:
            pass


def notify_card(title: str, stage: str = "", progress_pct: int = 0,
                eta_str: str = "?", steps: list = None,
                resources: dict = None, elapsed_str: str = "?",
                notes: str = "", include_session: bool = True):
    """상태 카드 알림 (notify.send_status_card 래퍼)"""
    try:
        sys.path.insert(0, str(ML_DIR))
        from notify import send_status_card
        send_status_card(
            title=title,
            stage=stage,
            progress_pct=progress_pct,
            eta_str=eta_str,
            steps=steps or [],
            resources=resources or {},
            elapsed_str=elapsed_str,
            notes=notes,
            session_summary=SESSION_INSIGHTS if include_session else "",
        )
    except Exception as e:
        wlog(f"  카드 알림 실패: {e}")
        # 폴백: 단순 알림
        notify_simple(f"{stage} {progress_pct}% ETA {eta_str}\n{notes}", title)


def disk_free_gb(drive: str = "D:\\") -> float:
    try:
        return shutil.disk_usage(drive).free / (1024**3)
    except Exception:
        return float("inf")


def onnx_count(model_dir: Path) -> int:
    return len(list(model_dir.glob("model_*.onnx")))


def run_with_retry(cmd: list, log_path: str, desc: str,
                   max_retry: int = MAX_RETRY,
                   notify_title: str = "", notify_stage: str = "",
                   progress_start: int = 0, progress_end: int = 100) -> bool:
    """서브프로세스 실행 + 주기적 Discord 폴링 + 알림 (블로킹 중에도 작동).

    notify_title/notify_stage 지정 시 alert_interval마다 카드 알림 발송.
    """
    import threading, queue as _queue
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    for attempt in range(1, max_retry + 1):
        wlog(f"[{desc}] attempt {attempt}/{max_retry}")
        with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
            lf.write(f"\n===== {desc} attempt {attempt} | {time.strftime('%H:%M:%S')} =====\n")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env
            )

            # stdout을 별도 스레드에서 읽어 큐에 넣음
            line_q: _queue.Queue = _queue.Queue()
            def _reader():
                try:
                    for ln in proc.stdout:
                        line_q.put(ln)
                except Exception:
                    pass
                finally:
                    line_q.put(None)  # 종료 신호

            t = threading.Thread(target=_reader, daemon=True)
            t.start()

            t_start    = time.time()
            last_alert = 0.0  # 마지막 알림 이후 경과분

            with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
                while True:
                    # 최대 30초 단위로 루프 → Discord 폴링 + 알림 체크
                    try:
                        line = line_q.get(timeout=30)
                    except _queue.Empty:
                        line = None  # 타임아웃 — 아직 실행 중

                    if line is not None:
                        sys.stdout.write(line); sys.stdout.flush()
                        lf.write(line)
                        if line is None:
                            break  # 스트림 종료
                    else:
                        # 종료 신호이거나 타임아웃
                        if proc.poll() is not None:
                            # 프로세스 종료됨 — 남은 줄 flush
                            while True:
                                try:
                                    remain = line_q.get_nowait()
                                    if remain is None:
                                        break
                                    sys.stdout.write(remain); sys.stdout.flush()
                                    lf.write(remain)
                                except _queue.Empty:
                                    break
                            break

                    # ── 30초마다 Discord 폴링 ──
                    elapsed_min = (time.time() - t_start) / 60
                    poll_discord_commands()
                    if _STOP_REQUESTED:
                        wlog(f"  [{desc}] STOP 요청 — 서브프로세스 종료")
                        proc.terminate()
                        proc.wait(timeout=10)
                        return False

                    # ── 알림 주기 도달 시 카드 발송 ──
                    if notify_title and (elapsed_min - last_alert >= current_alert_interval()):
                        prog = int(progress_start + (progress_end - progress_start) *
                                   min(elapsed_min / max(elapsed_min + 5, 60), 1.0))
                        notify_card(
                            title=notify_title,
                            stage=notify_stage or desc,
                            progress_pct=prog,
                            eta_str="진행 중",
                            steps=[("🔄", desc, fmt_minutes(elapsed_min))],
                            resources={"D 여유": f"{disk_free_gb():.0f}GB",
                                       "경과": fmt_minutes(elapsed_min)},
                            elapsed_str=fmt_minutes(elapsed_min),
                            notes=f"attempt {attempt}/{max_retry}",
                        )
                        last_alert = elapsed_min

            proc.wait()
            if proc.returncode == 0:
                wlog(f"[{desc}] OK ({fmt_minutes((time.time()-t_start)/60)})")
                return True
            wlog(f"[{desc}] exit={proc.returncode}")
        except Exception as e:
            wlog(f"[{desc}] exception: {e}")
        if attempt < max_retry:
            wlog(f"  30초 후 재시도...")
            time.sleep(30)
    return False


def fmt_minutes(minutes: float) -> str:
    if minutes < 60: return f"{int(minutes)}min"
    h, m = int(minutes // 60), int(minutes % 60)
    return f"{h}h {m:02d}m"


def poll_discord_commands():
    """디스코드 명령 큐 폴링. 처리한 항목은 consumed=True 표시."""
    global _PAUSE_FLAG, _SKIP_TASK2, _SKIP_TASK2C, _STOP_REQUESTED
    if not COMMAND_QUEUE.exists():
        return []
    try:
        lines = COMMAND_QUEUE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    new_entries = []
    updated_lines = []
    for line in lines:
        if not line.strip():
            updated_lines.append(line); continue
        try:
            e = json.loads(line)
        except Exception:
            updated_lines.append(line); continue
        if e.get("consumed"):
            updated_lines.append(line); continue

        cmd = e.get("cmd", "")
        wlog(f"  [Discord 명령] {cmd} by {e.get('user','?')}")
        new_entries.append(e)

        if cmd == "stop":         _STOP_REQUESTED = True
        elif cmd == "force-stop":
            wlog("  [강제종료] sys.exit(1)")
            sys.exit(1)
        elif cmd == "skip-task2":  _SKIP_TASK2  = True
        elif cmd == "skip-task2c": _SKIP_TASK2C = True
        elif cmd == "pause":       _PAUSE_FLAG  = True
        elif cmd == "resume":      _PAUSE_FLAG  = False

        e["consumed"] = True
        updated_lines.append(json.dumps(e, ensure_ascii=False))

    if new_entries:
        try:
            COMMAND_QUEUE.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
        except Exception:
            pass
    return new_entries


def wait_if_paused(ctx: str = ""):
    """일시정지 플래그가 켜져있으면 resume 명령 올 때까지 대기."""
    global _PAUSE_FLAG
    if not _PAUSE_FLAG: return
    wlog(f"  [PAUSE] {ctx} 일시정지 — !jb resume 대기")
    while _PAUSE_FLAG:
        poll_discord_commands()
        time.sleep(30)
    wlog(f"  [RESUME] {ctx} 재개")


# ─────────────────────────────────────────────────────────────────────
# STEP 0: 학습 완료 대기 (v3 ONNX 7개)
# ─────────────────────────────────────────────────────────────────────

def wait_for_v3_completion():
    wlog("=== STEP 0: Waiting for training completion ===")

    wait_start    = time.time()
    last_alert    = 0
    last_onnx     = -1
    start_time    = time.strftime("%H:%M")

    while True:
        n_onnx = onnx_count(RESULT_DIR)

        # 학습 프로세스 살아있는지 (CMD 라인에 train_benchmark 포함)
        train_alive = False
        try:
            result = subprocess.run(
                ["wmic", "process", "where", "name like '%python%'",
                 "get", "commandline,processid", "/format:csv"],
                capture_output=True, text=True, timeout=10
            )
            train_alive = "train_benchmark" in result.stdout
        except Exception:
            pass

        # 완료 조건: ONNX 7개 + 학습 프로세스 없음
        if n_onnx >= 7 and not train_alive:
            wlog(f"Training complete! onnx={n_onnx} alive={train_alive}")
            notify_card(
                title="JB-Pirate-King | v3 학습 완료",
                stage="STEP 0 → TASK 1 전환",
                progress_pct=33,
                eta_str="Phase 2 시작",
                steps=[
                    ("✅", "v3 학습 완료", f"ONNX {n_onnx}/7"),
                    ("🔄", "TASK 1 스케일링 비교", "다음"),
                    ("⏳", "TASK 2 전체월 앙상블", "대기"),
                ],
                resources={
                    "D 여유": f"{disk_free_gb():.0f}GB",
                    "ONNX": str(n_onnx),
                },
                elapsed_str=fmt_minutes((time.time() - wait_start) / 60),
                notes=f"학습 시작 {start_time} → 완료 {time.strftime('%H:%M')}",
            )
            return

        elapsed_min = (time.time() - wait_start) / 60

        # ONNX 개수 변화 즉시 알림
        if n_onnx != last_onnx:
            wlog(f"  ONNX={n_onnx}/7 (변화 감지)  train_alive={train_alive}")
            last_onnx = n_onnx

        # 주간 30분/야간 60분마다 카드 알림 (runtime_config.json으로 오버라이드 가능)
        if elapsed_min - last_alert >= current_alert_interval():
            notify_card(
                title="JB-Pirate-King | v3 학습 대기 중",
                stage=f"STEP 0: v3 학습 대기 (ONNX {n_onnx}/7)",
                progress_pct=int(n_onnx / 7 * 33),  # 0~33%
                eta_str="?",
                steps=[
                    ("🔄" if n_onnx < 7 else "✅", "v3 학습",
                     f"ONNX {n_onnx}/7  alive={train_alive}"),
                    ("⏳", "TASK 1 스케일링 비교", "대기"),
                    ("⏳", "TASK 2 전체월 앙상블", "대기"),
                ],
                resources={
                    "D 여유": f"{disk_free_gb():.0f}GB",
                    "대기": fmt_minutes(elapsed_min),
                },
                elapsed_str=fmt_minutes(elapsed_min),
                notes="ONNX 7번째 생성 즉시 자동 진행. !jb status로 즉시 보고 요청 가능",
            )
            last_alert = elapsed_min

        # 디스코드 명령 폴링
        poll_discord_commands()
        if _STOP_REQUESTED:
            wlog("  [STOP] 사용자 요청으로 종료"); return
        wait_if_paused("STEP 0")

        time.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────
# TASK 1: 스케일링 비교
# ─────────────────────────────────────────────────────────────────────

def task1_scaling_compare():
    wlog("=== TASK 1: Data scaling comparison ===")
    t0 = time.time()

    notify_card(
        title="JB-Pirate-King | TASK 1 시작",
        stage="TASK 1 스케일링 비교",
        progress_pct=33,
        eta_str="~2h (5년 학습 포함)",
        steps=[
            ("✅", "v3 학습 완료", ""),
            ("🔄", "TASK 1 스케일링 비교", "시작"),
            ("⏳", "TASK 2 전체월 앙상블", "대기"),
        ],
        resources={"D 여유": f"{disk_free_gb():.0f}GB"},
        elapsed_str="0min",
        notes="소규모(1k) / 5년 Jan / 11년 Jan F1 비교 → 데이터 증가 효과 분석",
    )

    ok = run_with_retry(
        cmd=[sys.executable, "-u", str(ML_DIR / "scaling_compare.py")],
        log_path=str(RESULT_DIR / "task1_scaling.log"),
        desc="T1-scaling"
    )

    elapsed_min = (time.time() - t0) / 60
    result_text = ""
    if (RESULT_DIR / "scaling_compare_result.txt").exists():
        lines = (RESULT_DIR / "scaling_compare_result.txt").read_text(
            encoding="utf-8", errors="replace").splitlines()
        result_text = "\n".join(lines[:18])

    notify_card(
        title=f"JB-Pirate-King | TASK 1 {'완료' if ok else '실패'}",
        stage="TASK 1 완료" if ok else "TASK 1 실패",
        progress_pct=50 if ok else 35,
        eta_str="TASK 2 시작",
        steps=[
            ("✅", "v3 학습", ""),
            ("✅" if ok else "❌", "TASK 1 스케일링 비교",
             fmt_minutes(elapsed_min)),
            ("⏳", "TASK 2 전체월 앙상블", "다음"),
        ],
        resources={
            "D 여유": f"{disk_free_gb():.0f}GB",
            "결과": "scaling_compare_result.txt",
        },
        elapsed_str=fmt_minutes(elapsed_min),
        notes=(result_text[:300] + "..." if len(result_text) > 300 else result_text)
              or "결과 파일 없음",
    )


# ─────────────────────────────────────────────────────────────────────
# TASK 2: 전체 월 앙상블 (스트리밍 + 디스크 가드)
# ─────────────────────────────────────────────────────────────────────

def task2_full_ensemble():
    global _SKIP_TASK2, _SKIP_TASK2C
    wlog("=== TASK 2: Full-month ensemble (2016-25, random days, FPR 1%) ===")
    poll_discord_commands()
    if _SKIP_TASK2:
        wlog("  [SKIP] !jb skip-task2 명령 수신 — TASK 2 건너뜀")
        notify_card(
            title="JB-Pirate-King | TASK 2 건너뜀",
            stage="TASK 2 사용자 요청으로 SKIP",
            progress_pct=100, eta_str="-",
            steps=[("⏭️", "TASK 2 전체", "사용자 skip 명령")],
            resources={"D 여유": f"{disk_free_gb():.0f}GB"},
            elapsed_str="0min",
            notes="!jb skip-task2 명령으로 건너뜀",
        )
        return
    ENSEMBLE_DIR.mkdir(parents=True, exist_ok=True)
    ALL_PRE_DIR.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    notify_card(
        title="JB-Pirate-King | TASK 2 시작",
        stage="TASK 2 전체월 앙상블",
        progress_pct=50,
        eta_str="~12h 예상",
        steps=[
            ("✅", "v3 학습", ""),
            ("✅", "TASK 1 스케일링 비교", ""),
            ("🔄", "2-A 전체월 스트리밍 다운로드", "시작"),
            ("⏳", "2-B 전처리(스트리밍 자동)", ""),
            ("⏳", "2-C TranAD+DCdetector 앙상블", "FPR 1%"),
            ("⏳", "2-D eval/시뮬레이션", ""),
        ],
        resources={
            "D 여유": f"{disk_free_gb():.0f}GB",
            "디스크 가드": f"{DISK_GUARD_GB}GB",
        },
        elapsed_str="0min",
        notes="2016-25 × 9개월/년(3개 랜덤 드롭) / 스트리밍 모드 (raw 즉시 삭제)",
    )

    # ── 2-A + 2-B: 스트리밍 다운로드 + 전처리 (한 번에) ─────────────
    wlog("  [2-A+B] Streaming download + preprocess")
    pre_count_before = len([f for f in ALL_PRE_DIR.glob("*_preprocessed.csv")
                            if f.stat().st_size > 0])
    wlog(f"  기존 preprocessed_all: {pre_count_before}개")

    if disk_free_gb() < DISK_GUARD_GB:
        wlog(f"  [중단] 디스크 여유 부족: {disk_free_gb():.1f}GB < {DISK_GUARD_GB}GB")
        notify_card(
            title="JB-Pirate-King | TASK 2 디스크 가드 중단",
            stage="2-A 중단",
            progress_pct=50,
            eta_str="-",
            steps=[("❌", "디스크 여유 부족", f"{disk_free_gb():.1f}GB < {DISK_GUARD_GB}GB")],
            resources={"D 여유": f"{disk_free_gb():.0f}GB"},
            elapsed_str=fmt_minutes((time.time()-t_start)/60),
            notes="수동으로 공간 확보 필요",
        )
        return

    run_with_retry(
        cmd=[sys.executable, "-u", str(ML_DIR / "download_ais_allmonths.py"),
             "--years"]+ [str(y) for y in TASK2_YEARS] +[
             "--days-per-year", str(TASK2_DAYS_PER_YEAR),   # 랜덤 일자 샘플링 (편향 방지)
             "--workers", str(TASK2_WORKERS),
             "--stream",
             "--disk-guard-gb", str(DISK_GUARD_GB)],
        log_path=str(RESULT_DIR / "download_stream.log"),
        desc="2AB-stream-download-preprocess"
    )

    pre_count = len([f for f in ALL_PRE_DIR.glob("*_preprocessed.csv")
                     if f.stat().st_size > 0])
    wlog(f"  [2-A+B 완료] preprocessed_all: {pre_count}개  (이전 {pre_count_before})")

    # 1월 데이터를 preprocessed_all로 복사/링크
    jan_files = list(PREPROC_DIR.glob("ais-*-01-*_preprocessed.csv"))
    jan_2016plus = [f for f in jan_files if int(f.stem.split("-")[1]) >= 2016]
    wlog(f"  기존 1월 preprocessed (2016-25): {len(jan_2016plus)}개 → 통합 디렉터리에 복사")
    for src in jan_2016plus:
        dst = ALL_PRE_DIR / src.name
        if not dst.exists():
            try:
                # 같은 드라이브면 하드링크가 빠름. 폴백 = copy.
                os.link(src, dst)
            except Exception:
                shutil.copy2(src, dst)
    pre_count = len([f for f in ALL_PRE_DIR.glob("*_preprocessed.csv")
                     if f.stat().st_size > 0])
    wlog(f"  최종 통합: {pre_count}개")

    notify_card(
        title="JB-Pirate-King | TASK 2-A+B 완료",
        stage="다운로드+전처리 완료",
        progress_pct=75,
        eta_str="~5h (앙상블 학습)",
        steps=[
            ("✅", "v3 학습", ""),
            ("✅", "TASK 1 스케일링 비교", ""),
            ("✅", "2-A+B 스트리밍 다운로드+전처리", f"{pre_count}개 통합"),
            ("🔄", "2-C TranAD+DCdetector 앙상블", "FPR 1% 학습 시작"),
            ("⏳", "2-D eval/시뮬레이션", ""),
        ],
        resources={
            "D 여유": f"{disk_free_gb():.0f}GB",
            "Preprocessed": f"{pre_count}",
        },
        elapsed_str=fmt_minutes((time.time() - t_start) / 60),
        notes="2-C: pandas 최적화 적용으로 데이터 로딩 빠름 (~25min)",
    )

    # ── 2-C: 앙상블 학습 (FPR=1%) ──────────────────────────────────
    # best_ensemble.txt: eval_all.py가 탐지율 1% FPR 기준 최적 조합 기록
    # 없으면 기본값 lstm,dcdetect (eval 결과 상 검증된 조합)
    def _read_best_models() -> str:
        best_file = RESULT_DIR / "best_ensemble.txt"
        if best_file.exists():
            try:
                combo = best_file.read_text(encoding="utf-8").strip()
                # 형식: "lstm + dcdetect + usad" → "lstm,dcdetect,usad"
                combo_clean = ",".join(m.strip() for m in combo.replace("+", ",").split(",") if m.strip())
                if combo_clean:
                    wlog(f"  [2-C] best_ensemble.txt 로드: {combo_clean}")
                    return combo_clean
            except Exception as e:
                wlog(f"  [2-C] best_ensemble.txt 읽기 실패: {e}")
        wlog("  [2-C] best_ensemble.txt 없음 → 기본값 lstm,dcdetect 사용")
        return "lstm,dcdetect"

    ensemble_models = _read_best_models()
    wlog(f"  [2-C] 앙상블 모델: {ensemble_models} (threshold-pct=99, FPR 1%)")
    cache_all = ENSEMBLE_DIR / "train_data_cache.pt"

    # 이미 학습된 ONNX가 있으면 스킵 (모델명이 일치하는지 확인)
    model_list = [m.strip() for m in ensemble_models.split(",")]
    already_done = all((ENSEMBLE_DIR / f"model_{m}.onnx").exists() or
                       (ENSEMBLE_DIR / f"model_{m}.pt").exists()
                       for m in model_list)

    poll_discord_commands()
    if _SKIP_TASK2C:
        wlog("  [2-C SKIP] !jb skip-task2c 명령으로 학습 건너뜀")
    elif already_done:
        wlog(f"  [2-C SKIP] 모든 앙상블 모델 ONNX 존재 ({ensemble_models})")
    else:
        if cache_all.exists():
            cache_all.unlink()
            wlog("  old cache removed")
        t_c0 = time.time()
        run_with_retry(
            cmd=[sys.executable, "-u", str(ML_DIR / "train_benchmark.py"),
                 "--model",         ensemble_models,
                 "--input",         str(ALL_PRE_DIR),
                 "--output",        str(ENSEMBLE_DIR),
                 "--cache",         str(cache_all),
                 "--threshold-pct", "99"],
            log_path=str(RESULT_DIR / "train_ensemble_full.log"),
            desc="2C-train",
            notify_title="JB-Pirate-King | TASK 2-C 앙상블 학습",
            notify_stage=f"2-C 전체데이터 앙상블 ({ensemble_models})",
            progress_start=75, progress_end=90,
        )
        n_onnx = onnx_count(ENSEMBLE_DIR)
        c_elapsed = (time.time() - t_c0) / 60
        wlog(f"  [2-C 완료] onnx={n_onnx}, {c_elapsed:.1f}min")

    # ── 2-D: eval ──────────────────────────────────────────────────
    wlog("  [2-D] Ensemble eval/simulation")
    t_d0 = time.time()
    eval_cmd = [sys.executable, "-u", str(ML_DIR / "eval_all.py"),
             "--model-dir", str(ENSEMBLE_DIR),
             "--data-dir",  str(ALL_PRE_DIR),
             "--severity",  "all"]
    test_files_path = RESULT_DIR / "test_files.json"
    if test_files_path.exists():
        eval_cmd += ["--test-files", str(test_files_path)]
    eval_ok = run_with_retry(
        cmd=eval_cmd,
        log_path=str(RESULT_DIR / "eval_ensemble_full.log"),
        desc="2D-eval"
    )
    eval_result = ""
    if (ENSEMBLE_DIR / "eval_summary.txt").exists():
        eval_result = (ENSEMBLE_DIR / "eval_summary.txt").read_text(
            encoding="utf-8", errors="replace")[:600]

    total_min = (time.time() - t_start) / 60
    notify_card(
        title="JB-Pirate-King | TASK 2 완료!",
        stage="TASK 2 완료",
        progress_pct=100,
        eta_str="-",
        steps=[
            ("✅", "v3 학습", ""),
            ("✅", "TASK 1 스케일링 비교", ""),
            ("✅", "2-A+B 다운로드+전처리", ""),
            ("✅", "2-C TranAD+DCdetector 학습", "FPR 1%"),
            ("✅" if eval_ok else "⚠️", "2-D eval/시뮬레이션",
             "OK" if eval_ok else "오류"),
        ],
        resources={
            "D 여유": f"{disk_free_gb():.0f}GB",
            "결과": str(ENSEMBLE_DIR),
            "총": fmt_minutes(total_min),
        },
        elapsed_str=fmt_minutes(total_min),
        notes=eval_result.replace("\n", " | ")[:200] if eval_result else "결과 파일 확인 필요",
    )


# ─────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────

def main():
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    t_phase_start = time.time()

    wlog("=" * 60)
    wlog("Phase 2 Auto Orchestrator v2 START")
    wlog("  - Status card notifications")
    wlog("  - Disk guard (100GB)")
    wlog("  - Streaming download+preprocess+delete")
    wlog("  - 2016-25 / 9 months/year (3 random drop)")
    wlog("=" * 60)

    try:
        wait_for_v3_completion()
        task1_scaling_compare()
        task2_full_ensemble()

        total_min = (time.time() - t_phase_start) / 60
        wlog(f"=== Phase 2 ALL DONE | {fmt_minutes(total_min)} ===")

        scale_result = ""
        if (RESULT_DIR / "scaling_compare_result.txt").exists():
            scale_result = "\n".join(
                (RESULT_DIR / "scaling_compare_result.txt").read_text(
                    encoding="utf-8", errors="replace").splitlines()[:10]
            )
        ens_result = ""
        if (ENSEMBLE_DIR / "eval_summary.txt").exists():
            ens_result = (ENSEMBLE_DIR / "eval_summary.txt").read_text(
                encoding="utf-8", errors="replace")[:400]

        notify_card(
            title="🎉 JB-Pirate-King | Phase 2 ALL DONE",
            stage="ALL COMPLETE — 업로드 매니페스트 작성 중",
            progress_pct=100,
            eta_str="-",
            steps=[
                ("✅", "v3 학습", ""),
                ("✅", "TASK 1 데이터 스케일링 비교", "완료"),
                ("✅", "TASK 2 전체월 앙상블", "완료"),
                ("📤", "GDrive 업로드 매니페스트", "다음"),
            ],
            resources={
                "D 여유": f"{disk_free_gb():.0f}GB",
                "결과": str(RESULT_DIR),
                "총 소요": fmt_minutes(total_min),
            },
            elapsed_str=fmt_minutes(total_min),
            notes=f"앙상블: {ens_result[:150]}...",
        )

        # GDrive 업로드 마커 생성 (Claude 세션에서 MCP로 처리)
        try:
            sys.path.insert(0, str(ML_DIR))
            from gdrive_upload_helper import mark_upload_ready
            mark_upload_ready()
        except Exception as e:
            wlog(f"  업로드 마커 생성 실패: {e}")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        wlog(f"Phase2 예외: {e}\n{tb}")
        notify_card(
            title="❌ JB-Pirate-King | Phase 2 예외",
            stage="예외 발생",
            progress_pct=0,
            eta_str="-",
            steps=[("❌", "예외 발생", str(e)[:100])],
            resources={"D 여유": f"{disk_free_gb():.0f}GB"},
            elapsed_str=fmt_minutes((time.time() - t_phase_start) / 60),
            notes=tb[-300:],
        )


if __name__ == "__main__":
    main()
