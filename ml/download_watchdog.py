#!/usr/bin/env python3
"""download_watchdog.py — 다운로드 자동복구 + Discord 하트비트 + 전처리 재시도.

기능:
  1. download_ais_allmonths.py --stream --workers N 실행 & 모니터링
  2. 프로세스 크래시 시 자동 재시작 (최대 MAX_RESTARTS회)
  3. 30분마다 Discord 하트비트 전송
  4. 완료 후 preprocess_fail 된 파일 재시도 (raw CSV가 남아있는 경우)
  5. 전체 파이프라인 완료 시 최종 Discord 알림

사용:
  python -u download_watchdog.py [--workers 6] [--max-restarts 5]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

# ── 경로 설정 ───────────────────────────────────────────
ML_DIR       = Path(__file__).resolve().parent
RESULT_DIR   = Path(r"D:\JB-Pirate-King-ML-Results")
AIS_DIR      = Path(r"D:\AIS")
PREPROC_ALL  = Path(r"D:\JB-Pirate-King-AIS\preprocessed_all")
DL_LOG       = RESULT_DIR / "download_allmonths.log"
WATCHDOG_LOG = RESULT_DIR / "download_watchdog.log"
CONFIG_FILE  = ML_DIR / "notify_config.json"

# ── Discord 알림 ────────────────────────────────────────
def discord_send(msg: str) -> bool:
    """Discord 웹훅으로 메시지 전송. requests 우선, urllib 폴백."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        url = cfg.get("discord_webhook", "")
        if not url:
            return False
    except Exception as e:
        wlog(f"[Discord 설정 오류] {e}")
        return False

    # 1차: requests (Discord 403 회피)
    try:
        import requests as _req
        r = _req.post(url, json={"content": msg}, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        pass

    # 2차: urllib 폴백
    try:
        data = json.dumps({"content": msg}).encode("utf-8")
        req = Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "JB-Pirate-King-Bot/1.0",
            "Content-Length": str(len(data)),
        })
        resp = urlopen(req, timeout=10)
        return resp.status in (200, 204)
    except Exception as e:
        wlog(f"[Discord 오류] {e}")
        return False

def wlog(msg: str):
    """워치독 로그 기록 + stdout."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── 상태 집계 ────────────────────────────────────────────
def count_preprocessed() -> int:
    """preprocessed_all 디렉터리의 _preprocessed.csv 수."""
    try:
        return len([f for f in PREPROC_ALL.iterdir()
                    if f.name.endswith("_preprocessed.csv")])
    except Exception:
        return -1

def disk_free_gb() -> float:
    import shutil
    return shutil.disk_usage("D:\\").free / (1024**3)

def collect_failed_raws() -> list:
    """D:\\AIS\\*\\*.csv 중 preprocessed_all에 대응 파일 없는 raw CSV 수집."""
    raws = []
    for yr_dir in sorted(AIS_DIR.iterdir()):
        if not yr_dir.is_dir():
            continue
        for f in yr_dir.glob("ais-*.csv"):
            if f.suffix == ".zst":
                continue
            if f.stat().st_size < 100_000:
                continue
            # 대응 preprocessed 파일 있는지 확인
            stem = f.stem  # ais-2017-07-01
            pre = PREPROC_ALL / f"{stem}_preprocessed.csv"
            if not pre.exists():
                raws.append(f)
    return raws

# ── 전처리 재시도 ────────────────────────────────────────
def preprocess_retry(raw_files: list, timeout_per_mb: float = 3.5,
                     min_timeout: int = 2400) -> tuple:
    """raw CSV 파일들을 순차 전처리. (성공수, 실패수) 반환."""
    ok = fail = 0
    for f in raw_files:
        size_mb = f.stat().st_size / 1e6
        tout = max(min_timeout, int(size_mb * timeout_per_mb))
        wlog(f"  전처리 재시도: {f.name} ({size_mb:.0f}MB, timeout={tout}s)")
        try:
            r = subprocess.run(
                [sys.executable, str(ML_DIR / "preprocess.py"), str(f)],
                cwd=str(PREPROC_ALL), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=tout
            )
            if r.returncode == 0:
                wlog(f"    ✓ 성공: {f.name}")
                ok += 1
                # 스트리밍 모드: raw 삭제
                try:
                    f.unlink()
                except Exception:
                    pass
            else:
                wlog(f"    ✗ 실패 (rc={r.returncode}): {r.stderr[:200] if r.stderr else 'N/A'}")
                fail += 1
        except subprocess.TimeoutExpired:
            wlog(f"    ✗ 타임아웃 ({tout}s)")
            fail += 1
        except Exception as e:
            wlog(f"    ✗ 예외: {e}")
            fail += 1
    return ok, fail

# ── 메인 워치독 ──────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6,
                        help="다운로드 병렬 워커 수 (기본 6)")
    parser.add_argument("--max-restarts", type=int, default=5,
                        help="최대 자동 재시작 횟수 (기본 5)")
    parser.add_argument("--heartbeat-min", type=int, default=30,
                        help="Discord 하트비트 간격(분, 기본 30)")
    args = parser.parse_args()

    wlog(f"=== 워치독 시작 | workers={args.workers} max_restarts={args.max_restarts} ===")
    discord_send(f"📥 **다운로드 워치독 시작**\n워커: {args.workers} | 자동복구: {args.max_restarts}회\n"
                 f"전처리 완료: {count_preprocessed()}개 | D여유: {disk_free_gb():.0f}GB")

    restart_count = 0
    last_heartbeat = time.time()

    while restart_count <= args.max_restarts:
        pre_count = count_preprocessed()
        wlog(f"다운로드 시작 (시도 {restart_count + 1}/{args.max_restarts + 1})")

        cmd = [
            sys.executable, "-u",
            str(ML_DIR / "download_ais_allmonths.py"),
            "--stream",
            "--workers", str(args.workers),
            "--disk-guard-gb", "80",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1  # line-buffered
        )
        wlog(f"PID {proc.pid} 시작: {' '.join(cmd)}")

        # ── 프로세스 모니터링 루프 ──
        while proc.poll() is None:
            # stdout 읽기 (non-blocking 방식)
            try:
                line = proc.stdout.readline()
                if line:
                    line = line.rstrip()
                    if line:
                        print(f"  [DL] {line}", flush=True)
            except Exception:
                pass

            # 하트비트 체크
            now = time.time()
            if now - last_heartbeat >= args.heartbeat_min * 60:
                cur_pre = count_preprocessed()
                delta = cur_pre - pre_count
                msg = (f"💓 **다운로드 하트비트**\n"
                       f"전처리: {cur_pre}개 (+{delta})\n"
                       f"D여유: {disk_free_gb():.0f}GB\n"
                       f"재시작: {restart_count}/{args.max_restarts}")
                discord_send(msg)
                last_heartbeat = now
                wlog(f"하트비트 전송: pre={cur_pre} (+{delta})")

        # ── 프로세스 종료 ──
        exit_code = proc.returncode
        post_count = count_preprocessed()
        wlog(f"다운로드 프로세스 종료 (exit={exit_code}, preprocessed={post_count})")

        if exit_code == 0:
            wlog("정상 종료 — 전처리 재시도 단계로 이동")
            break
        else:
            restart_count += 1
            if restart_count <= args.max_restarts:
                wait = min(60, restart_count * 15)
                wlog(f"비정상 종료! {wait}초 후 재시작 ({restart_count}/{args.max_restarts})")
                discord_send(f"⚠️ **다운로드 크래시 감지**\n"
                             f"exit={exit_code} | {wait}초 후 재시작\n"
                             f"시도 {restart_count}/{args.max_restarts}")
                time.sleep(wait)
            else:
                wlog(f"최대 재시작 횟수 초과! 중단.")
                discord_send(f"🚨 **다운로드 최대 재시작 초과**\n"
                             f"{args.max_restarts}회 실패. 수동 확인 필요.")
                break

    # ── 전처리 재시도 단계 ──
    wlog("=== 전처리 재시도 단계 ===")
    failed_raws = collect_failed_raws()
    if failed_raws:
        wlog(f"전처리 미완료 raw CSV {len(failed_raws)}개 발견 → 순차 재시도")
        discord_send(f"🔄 **전처리 재시도**\n{len(failed_raws)}개 raw CSV 재처리 시작")
        ok, fail = preprocess_retry(failed_raws)
        wlog(f"전처리 재시도 결과: 성공={ok}, 실패={fail}")
    else:
        ok = fail = 0
        wlog("전처리 미완료 raw 없음")

    # ── 최종 보고 ──
    final_count = count_preprocessed()
    wlog(f"=== 워치독 완료 | preprocessed={final_count} ===")
    discord_send(
        f"✅ **다운로드 파이프라인 완료**\n"
        f"전처리 파일: {final_count}개\n"
        f"전처리 재시도: 성공 {ok} / 실패 {fail}\n"
        f"재시작 횟수: {restart_count}\n"
        f"D여유: {disk_free_gb():.0f}GB"
    )


if __name__ == "__main__":
    main()
