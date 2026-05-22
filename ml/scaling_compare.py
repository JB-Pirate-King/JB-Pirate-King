"""
데이터 규모별 탐지율 비교 스크립트
===========================================
3가지 학습 규모를 비교하여 데이터 증가가 탐지율에 유의미한지 분석:
  소규모  : SAMPLE_MMSI=1000, 10 epochs  (이미 완료된 결과 재사용)
  5년치   : 2015-2019 Jan, SAMPLE_MMSI=6000  (신규 학습)
  11년치  : 2015-2025 Jan, SAMPLE_MMSI=6000  (v3 결과 재사용)

자가복구:
  - 각 단계 최대 MAX_RETRY 회 재시도
  - 에러 발생 시 Discord 알림 후 재시도
  - 캐시/ONNX 파일 존재 시 재학습 스킵

사용:
  python scaling_compare.py
  python scaling_compare.py --models lstm,timesnet,dcdetect  # 빠른 비교 (3개 모델)
  python scaling_compare.py --skip-5yr                       # 5년 학습 스킵 (결과가 있을 때)

출력:
  D:\JB-Pirate-King-ML-Results\scaling_compare_result.txt
"""

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import time
import io
from pathlib import Path

# UTF-8 강제
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ML_DIR       = Path(__file__).parent
RESULT_DIR   = Path(r"D:\JB-Pirate-King-ML-Results")
PREPROC_DIR  = Path(r"D:\JB-Pirate-King-AIS\preprocessed")
SCALE_5YR    = RESULT_DIR / "scale_5yr"
MAX_RETRY    = 3
MODELS_7     = "lstm,timesnet,usad,dcdetect,iforest,deepsvdd,dagmm"


# ────────────────────────────────────────────────────────────────────
# 유틸리티
# ────────────────────────────────────────────────────────────────────

def notify(msg: str, title: str = "JB-Pirate-King | Scale Compare"):
    try:
        subprocess.run(
            [sys.executable, str(ML_DIR / "notify.py"), msg, title],
            timeout=10, capture_output=True
        )
    except Exception:
        pass


def run_with_retry(cmd: list, log_path: str, desc: str,
                   max_retry: int = MAX_RETRY, env: dict = None) -> bool:
    """서브프로세스 실행, 실패 시 최대 max_retry 회 재시도."""
    _env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if env:
        _env.update(env)
    for attempt in range(1, max_retry + 1):
        print(f"\n[{desc}] 시도 {attempt}/{max_retry}: {' '.join(str(c) for c in cmd[:6])}...")
        with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
            lf.write(f"\n===== {desc} 시도 {attempt} | {time.strftime('%H:%M:%S')} =====\n")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=_env
            )
            with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
                for line in proc.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    lf.write(line)
            proc.wait()
            if proc.returncode == 0:
                return True
            msg = f"[{desc}] exit={proc.returncode} (시도 {attempt}/{max_retry})"
            print(msg)
            notify(msg, "JB-Pirate-King | 오류")
        except Exception as e:
            msg = f"[{desc}] 예외: {e} (시도 {attempt}/{max_retry})"
            print(msg)
            notify(msg, "JB-Pirate-King | 오류")
        if attempt < max_retry:
            print(f"  30초 후 재시도...")
            time.sleep(30)
    notify(f"[{desc}] {max_retry}회 모두 실패. 다음 단계 진행.", "JB-Pirate-King | 재시도 초과")
    return False


def read_eval_summary(model_dir: Path) -> dict:
    """eval_summary.txt 파싱 → {model_name: {tp_rate, fp_rate, f1}} 반환."""
    path = model_dir / "eval_summary.txt"
    results = {}
    if not path.exists():
        return results
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return results
    for line in lines:
        parts = line.split()
        # 형식: "lstm  100.0%  11.3%  0.876"
        if len(parts) >= 4:
            name = parts[0]
            try:
                tp  = float(parts[1].rstrip("%"))
                fp  = float(parts[2].rstrip("%"))
                f1  = float(parts[3])
                results[name] = {"tp_rate": tp, "fp_rate": fp, "f1": f1}
            except ValueError:
                continue
    return results


def read_eval_summary_from_log(log_path: Path) -> dict:
    """eval_all_11models.log 같은 로그 파일에서 결과 파싱."""
    results = {}
    if not log_path.exists():
        return results
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return results
    for line in lines:
        parts = line.split()
        if len(parts) >= 4:
            name = parts[0]
            try:
                tp  = float(parts[1].rstrip("%"))
                fp  = float(parts[2].rstrip("%"))
                f1  = float(parts[3])
                results[name] = {"tp_rate": tp, "fp_rate": fp, "f1": f1}
            except ValueError:
                continue
    return results


# ────────────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models",   type=str, default=MODELS_7,
                        help="비교에 사용할 모델 (기본: 7개 선정 모델)")
    parser.add_argument("--skip-5yr", action="store_true",
                        help="5년치 학습 스킵 (ONNX 결과가 이미 있을 때)")
    args = parser.parse_args()

    SCALE_5YR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    t_start = time.time()
    notify(
        f"스케일링 비교 시작!\n"
        f"비교 설정: 소규모(1000 MMSI) / 5년 Jan / 11년 Jan\n"
        f"모델: {args.models}",
        "JB-Pirate-King | 스케일링 비교 시작"
    )

    # ── STEP A: 소규모 결과 수집 (이미 완료된 것 재사용) ───────────────
    print("\n" + "=" * 65)
    print("STEP A: 소규모 테스트 결과 수집 (1000 MMSI, 10 epochs)")
    print("=" * 65)
    small_results = read_eval_summary_from_log(RESULT_DIR / "eval_all_11models.log")
    if not small_results:
        small_results = read_eval_summary(RESULT_DIR)  # fallback
    print(f"  소규모 결과 로드: {len(small_results)}개 모델")
    for name, r in sorted(small_results.items(), key=lambda x: -x[1]["f1"]):
        print(f"    {name:<12} F1={r['f1']:.3f}  탐지={r['tp_rate']:.1f}%  오탐={r['fp_rate']:.1f}%")

    # ── STEP B: 5년치 Jan 학습 (2015-2019) ────────────────────────────
    print("\n" + "=" * 65)
    print("STEP B: 5년치 Jan 학습 (2015-2019, SAMPLE_MMSI=6000)")
    print("=" * 65)

    # 5년치 ONNX 파일 존재 여부 확인
    model_list = [m.strip() for m in args.models.split(",") if m.strip()]
    onnx_5yr_exist = all(
        (SCALE_5YR / f"model_{m}.onnx").exists()
        for m in model_list
    )

    if args.skip_5yr or onnx_5yr_exist:
        print("  [스킵] 5년치 ONNX 이미 존재 -- 학습 생략")
        notify("5년치 모델 이미 존재 → 학습 스킵, 바로 평가 진행", "JB-Pirate-King | 5년 스킵")
    else:
        # 2015-2019 파일만 glob으로 선택
        glob_5yr = str(PREPROC_DIR / "ais-201[5-9]-*_preprocessed.csv")
        files_5yr = sorted(glob.glob(glob_5yr))
        print(f"  5년치 파일: {len(files_5yr)}개 ({glob_5yr})")

        if not files_5yr:
            msg = f"오류: 5년치 전처리 파일 없음 ({glob_5yr})"
            print(msg)
            notify(msg, "JB-Pirate-King | 오류")
        else:
            cache_5yr = str(SCALE_5YR / "train_data_cache.pt")
            log_5yr   = str(RESULT_DIR / "scale_5yr_train.log")
            notify(
                f"5년치 Jan 학습 시작!\n파일 {len(files_5yr)}개 (2015-2019)\n"
                f"모델: {args.models}",
                "JB-Pirate-King | 5년 학습 시작"
            )
            cmd_5yr = [
                sys.executable, "-u",
                str(ML_DIR / "train_benchmark.py"),
                "--model",  args.models,
                "--input",  glob_5yr,
                "--output", str(SCALE_5YR),
                "--cache",  cache_5yr,
            ]
            ok = run_with_retry(cmd_5yr, log_5yr, "5년치 학습")
            if ok:
                elapsed = round((time.time() - t_start) / 60, 1)
                notify(f"5년치 학습 완료! 경과 {elapsed}분", "JB-Pirate-King | 5년 완료")
            else:
                notify("5년치 학습 실패 (3회 재시도) → 결과 없이 계속", "JB-Pirate-King | 오류")

    # 5년치 eval
    eval_log_5yr = str(RESULT_DIR / "scale_5yr_eval.log")
    onnx_5yr_any = any((SCALE_5YR / f"model_{m}.onnx").exists() for m in model_list)
    five_yr_results = {}
    if onnx_5yr_any:
        print("\n  [평가] 5년치 모델 평가 중...")
        cmd_eval_5yr = [
            sys.executable, "-u",
            str(ML_DIR / "eval_all.py"),
            "--model-dir", str(SCALE_5YR),
            "--data-dir",  str(PREPROC_DIR),
        ]
        run_with_retry(cmd_eval_5yr, eval_log_5yr, "5년 eval")
        five_yr_results = read_eval_summary(SCALE_5YR)
        print(f"  5년치 평가 결과: {len(five_yr_results)}개 모델")

    # ── STEP C: 11년치 결과 수집 (v3 완료 결과) ──────────────────────
    print("\n" + "=" * 65)
    print("STEP C: 11년치 Jan 결과 수집 (2015-2025, v3 완료)")
    print("=" * 65)
    onnx_11yr_any = any(
        (RESULT_DIR / f"model_{m}.onnx").exists() for m in model_list
    )
    eleven_yr_results = {}
    if onnx_11yr_any:
        # v3 eval 실행 (또는 기존 결과 재사용)
        eval_summary_11 = RESULT_DIR / "eval_summary.txt"
        if eval_summary_11.exists():
            eleven_yr_results = read_eval_summary(RESULT_DIR)
            print(f"  11년치 결과 로드 (기존): {len(eleven_yr_results)}개 모델")
        else:
            print("  [평가] 11년치 모델 평가 실행 중...")
            log_11yr = str(RESULT_DIR / "scale_11yr_eval.log")
            cmd_eval_11 = [
                sys.executable, "-u",
                str(ML_DIR / "eval_all.py"),
                "--model-dir", str(RESULT_DIR),
                "--data-dir",  str(PREPROC_DIR),
            ]
            run_with_retry(cmd_eval_11, log_11yr, "11년 eval")
            eleven_yr_results = read_eval_summary(RESULT_DIR)
            print(f"  11년치 평가 결과: {len(eleven_yr_results)}개 모델")
    else:
        print("  [주의] 11년치 ONNX 아직 없음 (v3 학습 미완료?)")

    # ── STEP D: 3-way 비교 표 출력 및 보고 ──────────────────────────
    print("\n" + "=" * 65)
    print("STEP D: 데이터 규모별 탐지율 비교")
    print("=" * 65)

    # 공통 모델만 비교
    all_models_found = set(model_list)
    if small_results:     all_models_found &= set(small_results.keys())
    if five_yr_results:   all_models_found &= set(five_yr_results.keys())
    if eleven_yr_results: all_models_found &= set(eleven_yr_results.keys())
    compare_models = sorted(all_models_found,
                            key=lambda m: -(eleven_yr_results or five_yr_results or small_results).get(m, {}).get("f1", 0))

    header = f"{'모델':<12}  {'소규모(1k)':>10}  {'5년Jan':>10}  {'11년Jan':>10}  {'증가폭':>8}"
    sep    = "-" * 65
    rows   = []
    report_lines = [
        "데이터 규모별 탐지율 비교 (F1 기준)",
        "=" * 65,
        header, sep
    ]

    for m in compare_models:
        s_f1  = small_results.get(m, {}).get("f1", float("nan"))
        f_f1  = five_yr_results.get(m, {}).get("f1", float("nan"))
        e_f1  = eleven_yr_results.get(m, {}).get("f1", float("nan"))
        # 소규모 → 11년 증가폭
        if s_f1 == s_f1 and e_f1 == e_f1:  # nan 체크
            delta = e_f1 - s_f1
            delta_str = f"{delta:+.3f}"
        else:
            delta_str = "N/A"

        def fmt(v): return f"{v:.3f}" if v == v else "  N/A "

        row = f"{m:<12}  {fmt(s_f1):>10}  {fmt(f_f1):>10}  {fmt(e_f1):>10}  {delta_str:>8}"
        rows.append(row)
        print("  " + row)
        report_lines.append(row)

    # F1 평균 증가율 계산
    deltas = []
    for m in compare_models:
        s = small_results.get(m, {}).get("f1")
        e = eleven_yr_results.get(m, {}).get("f1")
        if s is not None and e is not None:
            deltas.append(e - s)

    if deltas:
        avg_delta = sum(deltas) / len(deltas)
        significant = "유의미함 ✓" if avg_delta > 0.02 else "미미함 (≤0.02)"
        conclusion = (
            f"\n평균 F1 증가폭 (소규모→11년): {avg_delta:+.4f}  →  {significant}\n"
            f"모델 수: {len(deltas)}개 기준"
        )
        print("\n" + conclusion)
        report_lines.append("")
        report_lines.append(conclusion)

    report_lines.extend([
        "",
        "오탐율(FP) 비교:",
        f"{'모델':<12}  {'소규모':>8}  {'5년':>8}  {'11년':>8}",
        "-" * 45,
    ])
    for m in compare_models:
        s_fp = small_results.get(m, {}).get("fp_rate")
        f_fp = five_yr_results.get(m, {}).get("fp_rate")
        e_fp = eleven_yr_results.get(m, {}).get("fp_rate")
        def fp_fmt(v): return f"{v:.1f}%" if v is not None else " N/A"
        report_lines.append(f"{m:<12}  {fp_fmt(s_fp):>8}  {fp_fmt(f_fp):>8}  {fp_fmt(e_fp):>8}")

    report_text = "\n".join(report_lines)

    # 결과 파일 저장
    out_path = RESULT_DIR / "scaling_compare_result.txt"
    out_path.write_text(report_text, encoding="utf-8")
    print(f"\n결과 저장: {out_path}")

    elapsed_min = round((time.time() - t_start) / 60, 1)

    # Discord 보고
    discord_msg = (
        f"데이터 스케일링 비교 완료! (소요 {elapsed_min}분)\n\n"
        f"F1 점수 비교 (소규모→5년→11년):\n"
    )
    for m in compare_models[:7]:  # 최대 7개
        s_f1 = small_results.get(m, {}).get("f1")
        f_f1 = five_yr_results.get(m, {}).get("f1")
        e_f1 = eleven_yr_results.get(m, {}).get("f1")
        def fmt2(v): return f"{v:.3f}" if v is not None else " N/A "
        discord_msg += f"  {m:<10} {fmt2(s_f1)} → {fmt2(f_f1)} → {fmt2(e_f1)}\n"

    if deltas:
        discord_msg += f"\n평균 증가폭: {avg_delta:+.4f} → {significant}"

    notify(discord_msg, "JB-Pirate-King | 스케일링 비교 완료")
    print("\n완료!")


if __name__ == "__main__":
    main()
