#!/usr/bin/env python3
# coding: utf-8
"""
compare_models.py
─────────────────
output/{model}/eval_result_{model}.csv 를 읽어
모델별 성능 비교표를 생성합니다.

사용법:
    python compare_models.py                        # output/ 자동 탐색
    python compare_models.py --output_dir output/   # 디렉터리 지정
    python compare_models.py --models usad tranad conv1d
    python compare_models.py --out model_comparison.csv
"""
from __future__ import annotations
import argparse, csv, os, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KNOWN_MODELS = [
    "usad", "tranad", "conv1d", "lstm", "tcn", "anomtrans", "dcdetect",
]


def load_eval_csv(csv_path: str) -> dict:
    """
    eval_result_{model}.csv 파싱
    → { scenario: {is_anomaly, detection_rate, avg_score, p95_score, threshold} }
    """
    result = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            result[row["scenario"]] = {
                "is_anomaly":     row.get("is_anomaly", "").lower() == "true",
                "detection_rate": float(row.get("detection_rate", 0)),
                "avg_score":      float(row.get("avg_score", 0)),
                "p95_score":      float(row.get("p95_score", 0)),
                "threshold":      float(row.get("threshold", 0)),
                "n_seqs":         int(row.get("n_seqs", 0)),
            }
    return result


def find_eval_csvs(output_dir: str, models: list[str] | None) -> dict[str, str]:
    """output_dir 에서 eval CSV 탐색 → {model_name: csv_path}"""
    found = {}
    out = Path(output_dir)

    candidates = models if models else KNOWN_MODELS
    for name in candidates:
        p = out / name / f"eval_result_{name}.csv"
        if p.exists():
            found[name] = str(p)

    # 서브디렉터리 전체 스캔 (models 미지정 시)
    if not models:
        for subdir in out.iterdir():
            if not subdir.is_dir():
                continue
            for f in subdir.glob("eval_result_*.csv"):
                model_name = f.stem.replace("eval_result_", "")
                if model_name not in found:
                    found[model_name] = str(f)

    return found


def build_comparison(model_results: dict[str, dict]) -> tuple[list[str], list[dict]]:
    """
    모델별 결과 → 비교 테이블
    반환: (column_names, rows)
    """
    # 시나리오 이름 수집 (정상 제외, 순서 유지)
    all_scenarios: list[str] = []
    seen: set[str] = set()
    normal_key: str | None = None

    for results in model_results.values():
        for scen, info in results.items():
            if not info["is_anomaly"]:
                normal_key = scen
                continue
            if scen not in seen:
                all_scenarios.append(scen)
                seen.add(scen)

    col_names = (
        ["model", "threshold", "fp_rate(%)"]
        + [s.replace(" ", "_") for s in all_scenarios]
        + ["train_avg(%)", "holdout_avg(%)"]
    )

    rows = []
    for model_name, results in model_results.items():
        # 오탐율 (정상 시나리오)
        fp_rate = 0.0
        threshold = 0.0
        if normal_key and normal_key in results:
            fp_rate   = results[normal_key]["detection_rate"]
            threshold = results[normal_key]["threshold"]

        # 시나리오별 탐지율
        scen_rates = []
        for scen in all_scenarios:
            r = results.get(scen, {}).get("detection_rate", None)
            scen_rates.append(round(r, 1) if r is not None else "")

        # 학습/홀드아웃 구분 (시나리오 이름에 '[H]' 또는 '[홀드]' 포함 여부로 구분)
        # eval_anomaly.py 는 별도 구분 없이 저장하므로 전체 평균으로 대체
        anom_rates = [
            results[s]["detection_rate"]
            for s in all_scenarios
            if s in results
        ]
        train_avg   = round(sum(anom_rates) / len(anom_rates), 1) if anom_rates else ""
        holdout_avg = ""   # eval_anomaly.py CSV에 holdout 구분 없음 → 추후 확장

        row = (
            {"model": model_name, "threshold": round(threshold, 6),
             "fp_rate(%)": round(fp_rate, 2)}
            | {s.replace(" ", "_"): r
               for s, r in zip(all_scenarios, scen_rates)}
            | {"train_avg(%)": train_avg, "holdout_avg(%)": holdout_avg}
        )
        rows.append(row)

    # fp_rate 오름차순 정렬
    rows.sort(key=lambda r: r["fp_rate(%)"] if isinstance(r["fp_rate(%)"], float) else 999)
    return col_names, rows


def print_table(col_names: list[str], rows: list[dict]):
    """터미널 출력용 간단 테이블"""
    # 컬럼 너비
    widths = {c: max(len(c), 6) for c in col_names}
    for row in rows:
        for c in col_names:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))

    sep = "  " + "-" * (sum(widths.values()) + 3 * len(col_names))
    header = "  " + "  ".join(c.rjust(widths[c]) for c in col_names)
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        line = "  " + "  ".join(str(row.get(c, "")).rjust(widths[c]) for c in col_names)
        print(line)
    print(sep)


def main():
    parser = argparse.ArgumentParser(description="모델별 eval CSV → 비교표 생성")
    parser.add_argument("--output_dir", type=str, default="output",
                        help="eval CSV 가 들어있는 output 디렉터리 (기본: output)")
    parser.add_argument("--models", type=str, nargs="*", default=None,
                        help="비교할 모델 목록 (미지정 시 자동 탐색)")
    parser.add_argument("--out", type=str, default=None,
                        help="비교표 저장 경로 (기본: {output_dir}/model_comparison.csv)")
    args = parser.parse_args()

    out_csv = args.out or os.path.join(args.output_dir, "model_comparison.csv")

    # ── CSV 탐색 ───────────────────────────────────────────────
    csv_map = find_eval_csvs(args.output_dir, args.models)
    if not csv_map:
        print(f"[오류] {args.output_dir}/ 에서 eval_result_*.csv 를 찾을 수 없습니다.")
        sys.exit(1)

    print(f"\n[비교 대상 모델 {len(csv_map)}개]")
    for name, path in csv_map.items():
        print(f"  {name:<25s}  {path}")

    # ── 로드 ───────────────────────────────────────────────────
    model_results: dict[str, dict] = {}
    for name, path in csv_map.items():
        try:
            model_results[name] = load_eval_csv(path)
        except Exception as e:
            print(f"  [!] {name} 로드 실패: {e}")

    if not model_results:
        print("[오류] 로드된 결과 없음")
        sys.exit(1)

    # ── 비교표 생성 ────────────────────────────────────────────
    col_names, rows = build_comparison(model_results)

    print(f"\n[모델 성능 비교]")
    print_table(col_names, rows)

    # ── CSV 저장 ───────────────────────────────────────────────
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=col_names, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"\n  [저장] {out_csv}")


if __name__ == "__main__":
    main()
