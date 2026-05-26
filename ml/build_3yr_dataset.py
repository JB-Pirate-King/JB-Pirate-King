#!/usr/bin/env python3
# coding: utf-8
"""
build_3yr_dataset.py
────────────────────
3년치(2023·2024·2025) 저편향 통합 데이터셋 빌더.

배경:
  AIS 1일치 전처리 결과 ≈ 790만 행 / 911MB.  3년 전체(≈900일)를 그대로
  전처리하면 ~800GB + RAM 폭발.  따라서:
    1) 연·월별로 며칠씩만 선택해 전처리 (시간/계절/연도 균형 = 저편향)
    2) 일자별로 MMSI를 일부만 무작위 추출 (volume 통제 + 선박 다양성 유지)
  → 균형 잡히고 RAM에 올라가는 통합 CSV 생성.

학습 측 저편향:
  train_benchmark / feature_engineer 의 연·월(YYYY-MM) 균등 MMSI 샘플링과
  MMSI당 시퀀스 상한이 추가로 작동한다.

사용법:
  python build_3yr_dataset.py \
    --raw_base D:/ais_data/raw \
    --years 2023 2024 2025 \
    --days_per_month 2 --mmsi_per_day 1500 \
    --tmp_dir D:/ais_data/preprocessed/_3yr_daily \
    --out D:/ais_data/preprocessed/ais_preprocessed_3yr.csv
"""
from __future__ import annotations
import argparse
import csv
import glob
import os
import random
import re
import subprocess
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

_ML_DIR = os.path.dirname(os.path.abspath(__file__))
_PREPROCESS = os.path.join(_ML_DIR, "core", "preprocess.py")

# 파일명에서 날짜 추출:  ais-2025-09-14.csv.zst  /  AIS_2024_06_15.zip
_DATE_RE = re.compile(r"(\d{4})[-_](\d{2})[-_](\d{2})")


def parse_date(path: str):
    """파일명 → (year:int, month:int, day:int) 또는 None"""
    m = _DATE_RE.search(os.path.basename(path))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def select_days(raw_dir: str, days_per_month: int) -> list:
    """연·월별로 days_per_month 일을 균등 간격으로 선택. 선택된 raw 파일 경로 리스트."""
    files = []
    for ext in ("*.zip", "*.csv.zst", "*.csv"):
        files += glob.glob(os.path.join(raw_dir, ext))
    by_month: dict = defaultdict(list)   # (year, month) → [(day, path)]
    for f in files:
        d = parse_date(f)
        if d:
            by_month[(d[0], d[1])].append((d[2], f))

    selected = []
    for (yr, mo), day_files in sorted(by_month.items()):
        day_files.sort()
        n = min(days_per_month, len(day_files))
        if n <= 0:
            continue
        # 균등 간격 인덱스
        if n == 1:
            idxs = [len(day_files) // 2]
        else:
            step = (len(day_files) - 1) / (n - 1)
            idxs = [round(i * step) for i in range(n)]
        for i in sorted(set(idxs)):
            selected.append(day_files[i][1])
    return selected


def _file_stem(path: str) -> str:
    """preprocess.py 의 _file_stem 과 동일한 규칙."""
    name = os.path.basename(path)
    if name.endswith(".csv.zst"):
        return name[:-8]
    if name.endswith(".csv"):
        return name[:-4]
    if name.endswith(".zip"):
        return name[:-4]
    return os.path.splitext(name)[0]


def _subsample_daily(daily_csv: str, mmsi_per_day: int):
    """일자 CSV를 읽어 MMSI 일부만 무작위 추출. (header, [rows]) 반환."""
    with open(daily_csv, encoding="utf-8") as fin:
        reader = csv.reader(fin)
        header = next(reader, None)
        if header is None:
            return None, []
        try:
            mmsi_idx = header.index("mmsi")
        except ValueError:
            return None, []
        rows_by_mmsi: dict = defaultdict(list)
        for row in reader:
            if len(row) == len(header):
                rows_by_mmsi[row[mmsi_idx]].append(row)

    keep = list(rows_by_mmsi.keys())
    if len(keep) > mmsi_per_day:
        keep = random.sample(keep, mmsi_per_day)
    rows = []
    for mmsi in keep:
        rows.extend(rows_by_mmsi[mmsi])
    return header, rows


def process_incremental(raw_files: list, tmp_dir: str, out_path: str,
                        mmsi_per_day: int) -> int:
    """디스크 절약형 증분 처리.

    한 날씩  전처리 → MMSI 부분추출 → 통합본에 append → 일자 CSV 삭제.
    피크 디스크 사용 ≈ 1일치(~1GB).  done 파일로 재실행 시 이어하기 지원.

    반환: 처리된 일자 수.
    """
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    done_path = out_path + ".done"

    # 이어하기: 이미 통합본에 반영된 raw 파일 목록
    done = set()
    if os.path.exists(done_path) and os.path.exists(out_path):
        with open(done_path, encoding="utf-8") as f:
            done = {ln.strip() for ln in f if ln.strip()}
        print(f"  [이어하기] 기존 통합본에 {len(done)}일 반영됨 → 나머지만 처리")

    header_written = os.path.exists(out_path) and bool(done)
    total_rows = 0
    mode = "a" if header_written else "w"

    with open(out_path, mode, newline="", encoding="utf-8") as fout, \
         open(done_path, "a", encoding="utf-8") as fdone:
        writer = csv.writer(fout)
        n_done = 0
        for i, raw in enumerate(raw_files, 1):
            if raw in done:
                continue
            stem  = _file_stem(raw)
            daily = os.path.join(tmp_dir, f"{stem}_preprocessed.csv")
            skip  = os.path.join(tmp_dir, f"{stem}_skip_log.csv")
            print(f"  [{i}/{len(raw_files)}] {os.path.basename(raw)} 전처리 중...",
                  flush=True)
            try:
                subprocess.run([sys.executable, _PREPROCESS, raw,
                                "--output_dir", tmp_dir], check=True)
            except subprocess.CalledProcessError as e:
                print(f"    [경고] 전처리 실패 → 건너뜀: {e}")
                continue

            if not os.path.exists(daily):
                print(f"    [경고] 출력 없음 → 건너뜀: {daily}")
                continue

            header, rows = _subsample_daily(daily, mmsi_per_day)
            if header and not header_written:
                writer.writerow(header)
                header_written = True
            if rows:
                writer.writerows(rows)
                total_rows += len(rows)
            fout.flush()
            fdone.write(raw + "\n"); fdone.flush()
            n_done += 1
            print(f"    → MMSI {len(rows) and len({r[header.index('mmsi')] for r in rows})}  "
                  f"행 {len(rows):,}  누적 {total_rows:,}")

            # 디스크 절약: 일자 CSV 즉시 삭제
            for p in (daily, skip):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

    print(f"\n[완료] {out_path}")
    print(f"  처리 일자: {n_done}개  |  누적 행: {total_rows:,}")
    return n_done


def main():
    ap = argparse.ArgumentParser(description="3년치 저편향 통합 데이터셋 빌더")
    ap.add_argument("--raw_base", default="D:/ais_data/raw",
                    help="연도별 raw 폴더 상위 경로 (raw_base/{year})")
    ap.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--days_per_month", type=int, default=2,
                    help="연·월별 선택 일수 (기본: 2)")
    ap.add_argument("--mmsi_per_day", type=int, default=1500,
                    help="일자별 유지 MMSI 수 (기본: 1500)")
    ap.add_argument("--tmp_dir", default="D:/ais_data/preprocessed/_3yr_daily",
                    help="일자별 전처리 임시 폴더")
    ap.add_argument("--out", default="D:/ais_data/preprocessed/ais_preprocessed_3yr.csv")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)

    print("=" * 60)
    print("  3년치 저편향 통합 데이터셋 빌드")
    print(f"  연도: {args.years}  |  일/월: {args.days_per_month}"
          f"  |  MMSI/일: {args.mmsi_per_day}")
    print("=" * 60)

    # 1) 연·월별 일자 선택
    all_selected = []
    for yr in args.years:
        raw_dir = os.path.join(args.raw_base, str(yr))
        if not os.path.isdir(raw_dir):
            print(f"  [경고] {raw_dir} 없음 → 건너뜀")
            continue
        sel = select_days(raw_dir, args.days_per_month)
        print(f"  {yr}: {len(sel)}개 일자 선택")
        all_selected += sel

    if not all_selected:
        print("선택된 일자 없음. 종료.")
        return

    # 2~3) 증분 전처리+병합 (디스크 절약: 일자별 처리 후 즉시 삭제)
    import time as _time
    t0 = _time.time()
    n_done = process_incremental(all_selected, args.tmp_dir, args.out,
                                 args.mmsi_per_day)
    elapsed = _time.time() - t0



if __name__ == "__main__":
    main()
