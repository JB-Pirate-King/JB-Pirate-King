#!/usr/bin/env python3
# coding: utf-8
"""
auto_feat_eng.py
────────────────
다운로드 완료 감지 → 3년치 균형 데이터셋 빌드 → 피처 엔지니어링 자동 반복

사용법:
    python ml/auto_feat_eng.py
    python ml/auto_feat_eng.py --skip_build --no_wait   # 데이터셋 이미 있을 때
    python ml/auto_feat_eng.py --max_iter 10             # 최대 10회 반복
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import subprocess

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_ML = os.path.dirname(os.path.abspath(__file__))
if _ML not in sys.path:
    sys.path.insert(0, _ML)

# ── 기본값 ───────────────────────────────────────────────────────────
RAW_BASE       = r"D:\ais_data\raw"
YEARS          = [2023, 2024, 2025]
IDLE_MINUTES   = 10     # 마지막 파일 쓰기 후 N분 변화 없음 → 완료 판단
POLL_SECONDS   = 60     # 체크 주기 (초)

# build_3yr_dataset 파라미터
DAYS_PER_MONTH = 3
MMSI_PER_DAY   = 2000
TMP_DIR        = r"D:\ais_data\preprocessed\_3yr_daily"
OUT_CSV        = r"D:\ais_data\preprocessed\ais_preprocessed_3yr.csv"

# feature_engineer 파라미터
BASE_DIR       = r"D:\\"
MAX_MMSI       = 3000
EPOCHS         = 5
N_ANOM         = 150
WEAK_FLOOR     = 55.0
WEAK_WEIGHT    = 1.5
MIN_GAIN       = 3.0
MAX_FEAT       = 18
MAX_ITER       = 5      # 피처 엔지니어링 최대 반복 횟수


# ── 알림 헬퍼 ────────────────────────────────────────────────────────
def _notify(title: str, summary: str, color: int = 0x3498DB):
    try:
        from notify import notify_iteration
        notify_iteration(title=title, summary=summary, color=color)
    except Exception as e:
        print(f"  [알림 건너뜀] {e}")


# ── 다운로드 완료 감지 ────────────────────────────────────────────────
def _latest_mtime(directory: str) -> float:
    mt = 0.0
    try:
        for f in os.listdir(directory):
            try:
                mt = max(mt, os.path.getmtime(os.path.join(directory, f)))
            except OSError:
                pass
    except OSError:
        pass
    return mt


def _dir_file_count(directory: str) -> int:
    try:
        return sum(
            1 for f in os.listdir(directory)
            if f.endswith((".zip", ".csv.zst", ".csv"))
        )
    except OSError:
        return 0


def download_complete(raw_base: str, years: list) -> bool:
    """모든 연도 디렉터리가 존재하고 IDLE_MINUTES 이상 변화 없으면 True."""
    now = time.time()
    for year in years:
        d = os.path.join(raw_base, str(year))
        if not os.path.isdir(d):
            return False
        if _dir_file_count(d) == 0:
            return False
        if now - _latest_mtime(d) < IDLE_MINUTES * 60:
            return False
    return True


def wait_for_download(raw_base: str, years: list):
    print(f"\n[대기] 다운로드 완료 감지 중... ({POLL_SECONDS}초 주기, {IDLE_MINUTES}분 idle 기준)")
    _notify(
        "⏳ 파이프라인 대기 중",
        f"다운로드 완료 감지 대기\n경로: {raw_base}\n연도: {years}\n"
        f"완료 기준: {IDLE_MINUTES}분간 변화 없음",
    )
    while not download_complete(raw_base, years):
        parts = []
        for yr in years:
            d = os.path.join(raw_base, str(yr))
            n = _dir_file_count(d)
            parts.append(f"{yr}:{n}개")
        print(f"  [{time.strftime('%H:%M:%S')}] {' | '.join(parts)}")
        time.sleep(POLL_SECONDS)

    print(f"  [{time.strftime('%H:%M:%S')}] ✓ 다운로드 완료 감지!")
    _notify("✅ 다운로드 완료", "데이터셋 빌드를 시작합니다.", color=0x2ECC71)


# ── subprocess 실행 ───────────────────────────────────────────────────
def run_step(cmd: list, step_name: str):
    print(f"\n{'='*60}")
    print(f"  {step_name}")
    print(f"  {' '.join(cmd)}")
    print(f"{'='*60}")
    t0 = time.time()
    ret = subprocess.run(cmd, cwd=_ML)
    elapsed = time.time() - t0
    if ret.returncode != 0:
        _notify(
            f"❌ {step_name} 실패",
            f"exit code: {ret.returncode}\n소요: {elapsed/60:.1f}분",
            color=0xE74C3C,
        )
        print(f"\n  ✗ {step_name} 실패 (exit {ret.returncode})")
        sys.exit(ret.returncode)
    print(f"\n  ✓ {step_name} 완료  ({elapsed/60:.1f}분)")


# ── 피처 엔지니어링 1 이터레이션 ─────────────────────────────────────
def run_feat_eng_iter(
    iter_num: int,
    initial_extra: list | None,
    out_json: str,
    args,
) -> dict:
    """feature_engineer.py 1회 실행 → JSON 파싱 결과 반환."""
    cmd = [
        sys.executable, "feature_engineer.py",
        "--input",       args.out_csv,
        "--base_dir",    args.base_dir,
        "--max_mmsi",    str(args.max_mmsi),
        "--epochs",      str(args.epochs),
        "--n_anom",      str(args.n_anom),
        "--weak_floor",  str(args.weak_floor),
        "--weak_weight", str(args.weak_weight),
        "--min_gain",    str(args.min_gain),
        "--max_feat",    str(args.max_feat),
        "--out_json",    out_json,
    ]
    if initial_extra:
        cmd += ["--initial_extra"] + initial_extra

    run_step(cmd, f"피처 엔지니어링 Iter {iter_num:02d}")

    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


# ── 이터레이션 루프 ───────────────────────────────────────────────────
def feat_eng_loop(args):
    iter_dir = os.path.join(args.base_dir, "ais_output", "feat_eng_iter")
    os.makedirs(iter_dir, exist_ok=True)

    initial_extra: list | None = None   # Iter 1: 코드 내 INITIAL_EXTRA 사용
    prev_best_det = 0.0

    for iter_num in range(1, args.max_iter + 1):
        print(f"\n{'#'*60}")
        print(f"  피처 엔지니어링 루프  Iter {iter_num}/{args.max_iter}")
        if initial_extra is not None:
            print(f"  initial_extra: {initial_extra}")
        print(f"{'#'*60}")

        out_json = os.path.join(iter_dir, f"feat_eng_iter{iter_num:02d}.json")
        result = run_feat_eng_iter(iter_num, initial_extra, out_json, args)

        best_extra   = result.get("best_extra", [])
        best_det     = result.get("best_det", 0.0)
        baseline_det = result.get("baseline_det", best_det)  # 이번 iter 시작점 탐지율
        init_extra   = result.get("initial_extra", [])

        newly_adopted   = [f for f in best_extra if f not in init_extra]
        intra_gain      = best_det - baseline_det   # 이번 iter 내 개선량
        inter_gain      = best_det - prev_best_det  # 직전 iter 대비 개선량

        print(f"\n[Iter {iter_num}] 탐지율: {baseline_det:.1f}% → {best_det:.1f}%  |  신규: {newly_adopted}")

        gain_str = (
            f"이번 개선: {intra_gain:+.1f}pp | 직전 대비: {inter_gain:+.1f}pp"
            if iter_num > 1 else
            f"이번 개선: {intra_gain:+.1f}pp (베이스라인 → 최적)"
        )

        # Discord 알림
        _notify(
            f"📊 Iter {iter_num:02d} 완료  ({best_det:.1f}%)",
            (
                f"탐지율: {baseline_det:.1f}% → {best_det:.1f}%\n"
                f"{gain_str}\n"
                f"채택 피처: {best_extra}\n"
                f"신규: {newly_adopted or '없음'}\n"
                f"JSON: {out_json}"
            ),
            color=0x2ECC71 if newly_adopted else 0xF39C12,
        )

        # Notion 구조화 결과 페이지
        try:
            from notify import send_notion_iter_report
            ts = __import__("time").strftime("%Y-%m-%d %H:%M")
            send_notion_iter_report(
                title=f"Iter {iter_num:02d} — {best_det:.1f}% · {ts}",
                iter_num=iter_num,
                baseline_det=baseline_det,
                best_det=best_det,
                initial_extra=init_extra,
                best_extra=best_extra,
                newly_adopted=newly_adopted,
                history=result.get("history", []),
                permutation_importance=result.get("permutation_importance", []),
                feature_descriptions=result.get("feature_descriptions", {}),
                json_path=out_json,
            )
        except Exception as e:
            print(f"  [Notion 건너뜀] {e}")

        # ── 수렴 판정 ──────────────────────────────────────────────
        if not newly_adopted and iter_num > 1:
            _notify(
                "✅ 피처 수렴",
                f"Iter {iter_num}: 신규 채택 없음 → Greedy 수렴 완료\n"
                f"최종 피처: {best_extra}\n"
                f"최종 탐지율: {best_det:.1f}%",
                color=0x2ECC71,
            )
            print(f"\n  ✓ 수렴 완료 (신규 채택 없음). 루프 종료.")
            break

        prev_best_det = best_det
        initial_extra = best_extra  # 다음 이터레이션으로 전달

    else:
        # max_iter 도달
        _notify(
            f"🏁 루프 완료 (max_iter={args.max_iter})",
            f"최종 피처: {initial_extra}\n최종 탐지율: {prev_best_det:.1f}%",
            color=0x9B59B6,
        )

    print(f"\n=== 피처 엔지니어링 루프 종료 ===")
    print(f"  결과 폴더: {iter_dir}")


# ── 메인 ─────────────────────────────────────────────────────────────
def main():
    global IDLE_MINUTES
    ap = argparse.ArgumentParser(description="다운로드 완료 감지 → 피처 엔지니어링 자동 반복")
    ap.add_argument("--raw_base",       default=RAW_BASE)
    ap.add_argument("--years",          type=int, nargs="+", default=YEARS)
    ap.add_argument("--idle_minutes",   type=int,   default=IDLE_MINUTES)
    ap.add_argument("--days_per_month", type=int,   default=DAYS_PER_MONTH)
    ap.add_argument("--mmsi_per_day",   type=int,   default=MMSI_PER_DAY)
    ap.add_argument("--tmp_dir",        default=TMP_DIR)
    ap.add_argument("--out_csv",        default=OUT_CSV)
    ap.add_argument("--base_dir",       default=BASE_DIR)
    ap.add_argument("--max_mmsi",       type=int,   default=MAX_MMSI)
    ap.add_argument("--epochs",         type=int,   default=EPOCHS)
    ap.add_argument("--n_anom",         type=int,   default=N_ANOM)
    ap.add_argument("--weak_floor",     type=float, default=WEAK_FLOOR)
    ap.add_argument("--weak_weight",    type=float, default=WEAK_WEIGHT)
    ap.add_argument("--min_gain",       type=float, default=MIN_GAIN)
    ap.add_argument("--max_feat",       type=int,   default=MAX_FEAT)
    ap.add_argument("--max_iter",       type=int,   default=MAX_ITER,
                    help=f"피처 엔지니어링 최대 반복 횟수 (기본: {MAX_ITER})")
    ap.add_argument("--skip_build",     action="store_true",
                    help="데이터셋 빌드 건너뜀 (out_csv가 이미 있을 때)")
    ap.add_argument("--no_wait",        action="store_true",
                    help="다운로드 완료 대기 없이 즉시 실행")
    args = ap.parse_args()
    IDLE_MINUTES = args.idle_minutes

    print("=" * 60)
    print("  auto_feat_eng.py — AIS 피처 엔지니어링 자동화")
    print(f"  연도: {args.years}  |  raw: {args.raw_base}")
    print(f"  max_iter: {args.max_iter}")
    print("=" * 60)

    # ── 1. 다운로드 완료 대기 ─────────────────────────────
    if not args.no_wait and not args.skip_build:
        wait_for_download(args.raw_base, args.years)

    # ── 2. 3년치 균형 데이터셋 빌드 ──────────────────────
    if not args.skip_build:
        run_step(
            [
                sys.executable, "build_3yr_dataset.py",
                "--raw_base", args.raw_base,
                "--years", *[str(y) for y in args.years],
                "--days_per_month", str(args.days_per_month),
                "--mmsi_per_day",   str(args.mmsi_per_day),
                "--tmp_dir",        args.tmp_dir,
                "--out",            args.out_csv,
            ],
            "Step 1: 3년치 균형 데이터셋 빌드",
        )
    else:
        print(f"\n[스킵] 데이터셋 빌드 건너뜀 → {args.out_csv}")

    # ── 3. 피처 엔지니어링 반복 루프 ──────────────────────
    feat_eng_loop(args)

    print("\n=== 전체 파이프라인 완료 ===")


if __name__ == "__main__":
    main()
