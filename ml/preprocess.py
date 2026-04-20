"""
AIS 데이터 전처리 스크립트

입력: CSV 파일 여러 개 (세 가지 방법 중 하나로 지정)
출력: ais_preprocessed.csv

──────────────────────────────────────────────────
입력 파일 지정 방법 (우선순위: FILES > DIR > GLOB)

  1) glob 패턴  INPUT_GLOB  = "data/ais-*.csv"
  2) 폴더 전체  INPUT_DIR   = "data/"
  3) 명시적 목록 INPUT_FILES = ["jan.csv", "feb.csv"]

또는 CLI 인수:
  python preprocess.py data/ais-*.csv
  python preprocess.py data/
  python preprocess.py jan.csv feb.csv mar.csv
──────────────────────────────────────────────────

피처 (15개):
    sog, cog, heading, status, dt, dist_km,
    expected_dist_km, bearing_cog_diff, cog_hdg_diff,
    sog_change, cog_change, sog_status_ratio,
    dist_expected_ratio, cog_hdg_change, cog_hdg_std
"""

import csv
import glob
import math
import os
import statistics
import sys
from datetime import datetime
from collections import deque

# ── 입력 설정 (CLI 인수가 없을 때 사용) ──────────────────────────
INPUT_GLOB  = "ais-*.csv"   # 현재 폴더의 ais-*.csv 전부
INPUT_DIR   = ""             # 폴더 지정 시 여기에 경로 입력
INPUT_FILES = []             # 파일 명시적 목록

OUTPUT_FILE = "ais_preprocessed.csv"

MIN_SEQ_LEN  = 10
SEQ_BREAK_DT = 600

USE_COLS = [
    "mmsi", "base_date_time",
    "latitude", "longitude",
    "sog", "cog", "heading",
    "status", "vessel_type",
]

STATUS_MAX_SOG = {
    0: 30.0, 1: 1.0,  2: 5.0,  3: 10.0,
    4: 10.0, 5: 1.0,  6: 5.0,  7: 15.0, 8: 15.0,
}
DEFAULT_MAX_SOG = 30.0


# ── 입력 파일 목록 결정 ───────────────────────────────────────────
def resolve_input_files() -> list:
    # 1) CLI 인수
    if len(sys.argv) > 1:
        args = sys.argv[1:]
        files = []
        for a in args:
            if os.path.isdir(a):
                files += sorted(glob.glob(os.path.join(a, "*.csv")))
            else:
                files += sorted(glob.glob(a))   # glob 패턴도 허용
        files = [f for f in files if os.path.isfile(f)]
        if files:
            return files

    # 2) 스크립트 내 설정
    if INPUT_FILES:
        files = [f for f in INPUT_FILES if os.path.isfile(f)]
    elif INPUT_DIR:
        files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.csv")))
        files = [f for f in files if os.path.isfile(f)]
    else:
        files = sorted(glob.glob(INPUT_GLOB))
        files = [f for f in files if os.path.isfile(f)]

    if not files:
        raise FileNotFoundError(
            "입력 파일 없음. 사용법:\n"
            "  python preprocess.py data/ais-*.csv\n"
            "  python preprocess.py data/\n"
            "  python preprocess.py jan.csv feb.csv\n"
            "또는 스크립트 상단 INPUT_GLOB / INPUT_DIR / INPUT_FILES 설정"
        )
    return files


# ── CSV 한 줄씩 읽기 ──────────────────────────────────────────────
def iter_lines_csv(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line.rstrip("\n")


# ── 여러 파일을 헤더 통합해서 스트리밍 ───────────────────────────
def iter_all_files(input_files: list, writer):
    """파일별로 파싱해 USE_COLS 행만 writer에 기록, 총 행 수 반환"""
    total = 0
    for fpath in input_files:
        header = None
        file_count = 0
        for line in iter_lines_csv(fpath):
            line = line.strip()
            if not line:
                continue
            if header is None:
                header = [c.strip() for c in line.split(",")]
                continue
            values = line.split(",")
            if len(values) != len(header):
                continue
            row  = {header[i]: values[i].strip() for i in range(len(header))}
            mmsi = row.get("mmsi", "")
            if not mmsi:
                continue
            try:
                lat = float(row.get("latitude", ""))
                lon = float(row.get("longitude", ""))
                sog = float(row.get("sog", "0") or "0")
                if not (-90 <= lat <= 90):   continue
                if not (-180 <= lon <= 180): continue
                if sog < 0:                  continue
            except ValueError:
                continue
            writer.writerow({col: row.get(col, "") for col in USE_COLS})
            total += 1
            file_count += 1
        print(f"      {os.path.basename(fpath)}: {file_count:,} 행")
        if total % 500000 < file_count:
            print(f"      누적 {total:,} 행 처리 중...")
    return total


# ── 결측값 처리 ───────────────────────────────────────────────────
def fill_missing(rows: list) -> list:
    defaults = {"sog": 0.0, "cog": 0.0, "heading": 511.0,
                "status": 15.0, "vessel_type": 0.0}
    prev = dict(defaults)
    for row in rows:
        for col, default in defaults.items():
            val = row.get(col, "")
            if val == "":
                row[col] = prev[col]
            else:
                try:
                    row[col] = float(val); prev[col] = row[col]
                except ValueError:
                    row[col] = prev[col]
    return rows


# ── 파생 피처 추가 ────────────────────────────────────────────────
def add_derived_features(rows: list) -> list:
    cog_hdg_window = deque(maxlen=10)

    for i, row in enumerate(rows):
        if i == 0:
            row["dt"] = row["dist_km"] = row["expected_dist_km"] = 0.0
            row["bearing_cog_diff"] = -1.0
            row["cog_hdg_diff"] = row["sog_change"] = row["cog_change"] = 0.0
            try:
                sog = float(row["sog"]); status = int(float(row["status"]))
                mx  = STATUS_MAX_SOG.get(status, DEFAULT_MAX_SOG)
                row["sog_status_ratio"] = round(sog / mx, 4) if mx > 0 else 0.0
            except Exception:
                row["sog_status_ratio"] = 0.0
            row["dist_expected_ratio"] = 1.0
            row["cog_hdg_change"] = 0.0
            cog_hdg_window.append(0.0)
            row["cog_hdg_std"] = 0.0
            continue

        prev = rows[i - 1]

        # dt
        try:
            t1 = datetime.strptime(prev["base_date_time"], "%Y-%m-%d %H:%M:%S")
            t2 = datetime.strptime(row["base_date_time"],  "%Y-%m-%d %H:%M:%S")
            row["dt"] = max(0.0, (t2 - t1).total_seconds())
        except Exception:
            row["dt"] = 0.0

        # dist_km
        try:
            lat1 = math.radians(float(prev["latitude"]))
            lat2 = math.radians(float(row["latitude"]))
            dlat = lat2 - lat1
            dlon = math.radians(float(row["longitude"]) - float(prev["longitude"]))
            a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
            row["dist_km"] = round(6371.0 * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1-a)), 4)
        except Exception:
            row["dist_km"] = 0.0

        # expected_dist_km
        try:
            row["expected_dist_km"] = round(float(row["sog"]) * float(row["dt"]) / 3600.0 * 1.852, 4)
        except Exception:
            row["expected_dist_km"] = 0.0

        # bearing_cog_diff
        try:
            if float(row["sog"]) < 0.5:
                row["bearing_cog_diff"] = -1.0
            else:
                la1 = math.radians(float(prev["latitude"]))
                la2 = math.radians(float(row["latitude"]))
                dlo = math.radians(float(row["longitude"]) - float(prev["longitude"]))
                bearing = math.degrees(math.atan2(
                    math.sin(dlo) * math.cos(la2),
                    math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
                )) % 360.0
                diff = abs(bearing - float(row["cog"]))
                row["bearing_cog_diff"] = round(360.0 - diff if diff > 180.0 else diff, 1)
        except Exception:
            row["bearing_cog_diff"] = -1.0

        # cog_hdg_diff
        try:
            hdg = float(row["heading"])
            if hdg == 511.0:
                row["cog_hdg_diff"] = -1.0
            else:
                diff = abs(float(row["cog"]) - hdg)
                row["cog_hdg_diff"] = round(360.0 - diff if diff > 180.0 else diff, 1)
        except Exception:
            row["cog_hdg_diff"] = -1.0

        # sog_change / cog_change
        try:
            row["sog_change"] = round(abs(float(row["sog"]) - float(prev["sog"])), 4)
        except Exception:
            row["sog_change"] = 0.0
        try:
            diff = abs(float(row["cog"]) - float(prev["cog"]))
            row["cog_change"] = round(360.0 - diff if diff > 180.0 else diff, 4)
        except Exception:
            row["cog_change"] = 0.0

        # sog_status_ratio
        try:
            sog = float(row["sog"]); status = int(float(row["status"]))
            mx  = STATUS_MAX_SOG.get(status, DEFAULT_MAX_SOG)
            row["sog_status_ratio"] = round(sog / mx, 4) if mx > 0 else 0.0
        except Exception:
            row["sog_status_ratio"] = 0.0

        # dist_expected_ratio
        try:
            row["dist_expected_ratio"] = round(
                float(row["dist_km"]) / (float(row["expected_dist_km"]) + 1e-6), 4)
        except Exception:
            row["dist_expected_ratio"] = 1.0

        # cog_hdg_change
        try:
            pc = float(prev.get("cog_hdg_diff", 0.0))
            cc = float(row["cog_hdg_diff"])
            row["cog_hdg_change"] = 0.0 if pc < 0 or cc < 0 else round(abs(cc - pc), 4)
        except Exception:
            row["cog_hdg_change"] = 0.0

        # cog_hdg_std
        try:
            v = float(row["cog_hdg_diff"])
            cog_hdg_window.append(v if v >= 0 else 0.0)
        except Exception:
            cog_hdg_window.append(0.0)
        row["cog_hdg_std"] = round(statistics.stdev(cog_hdg_window), 4) \
                             if len(cog_hdg_window) >= 2 else 0.0

    return rows


# ── 필터 ─────────────────────────────────────────────────────────
def has_position_jump(rows: list) -> bool:
    for row in rows:
        try:
            if float(row["dist_km"]) > (float(row["dt"]) / 3600) * 92.6 * 1.2:
                return True
        except (ValueError, KeyError):
            pass
    return False

def has_invalid(rows: list) -> bool:
    for row in rows:
        try:
            lat = float(row["latitude"]); lon = float(row["longitude"])
            sog = float(row["sog"])
        except (ValueError, KeyError):
            return True
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or sog < 0:
            return True
    return False


# ── 단일 파일 처리 (파싱→정렬→전처리→저장) ──────────────────────
def process_file(input_path: str, output_path: str, out_cols: list) -> dict:
    """
    input_path  : 원본 CSV 1개
    output_path : 전처리 결과 저장 경로
    반환: {"rows": int, "mmsi_ok": int, "mmsi_skip": int, "skip_log": list}
    """
    stem      = os.path.splitext(os.path.basename(input_path))[0]
    TEMP_FILE = f"_tmp_{stem}.csv"
    skip_log  = []

    # 파싱
    with open(TEMP_FILE, "w", newline="", encoding="utf-8") as tmp:
        writer = csv.DictWriter(tmp, fieldnames=USE_COLS, extrasaction="ignore")
        writer.writeheader()
        total = iter_all_files([input_path], writer)

    # 정렬
    with open(TEMP_FILE, "r", encoding="utf-8") as f:
        header_line = f.readline()
        rows = f.readlines()
    rows.sort(key=lambda line: (
        int(line.split(",", 1)[0]) if line.split(",", 1)[0].isdigit() else 0,
        line.split(",", 2)[1] if "," in line else ""
    ))
    with open(TEMP_FILE, "w", encoding="utf-8") as f:
        f.write(header_line); f.writelines(rows)
    del rows

    # 전처리 + 저장
    skipped = output_count = 0
    current_mmsi = None
    current_rows = []

    def _write(rows, writer, mmsi):
        if len(rows) < MIN_SEQ_LEN:
            skip_log.append((mmsi, f"시퀀스 부족 ({len(rows)}개)")); return 0
        if has_invalid(rows):
            skip_log.append((mmsi, "이상값")); return 0
        rows = fill_missing(rows)
        rows = add_derived_features(rows)
        if has_position_jump(rows):
            skip_log.append((mmsi, "위치 점프 감지")); return 0
        writer.writerows(rows)
        return len(rows)

    with open(TEMP_FILE, "r", encoding="utf-8") as fin, \
         open(output_path, "w", newline="", encoding="utf-8") as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=out_cols, extrasaction="ignore")
        writer.writeheader()
        for row in reader:
            mmsi = row.get("mmsi", "")
            if mmsi != current_mmsi:
                if current_mmsi is not None:
                    n = _write(current_rows, writer, current_mmsi)
                    skipped += (n == 0); output_count += n
                current_mmsi = mmsi; current_rows = [row]
            else:
                current_rows.append(row)
        if current_rows:
            n = _write(current_rows, writer, current_mmsi)
            skipped += (n == 0); output_count += n

    os.remove(TEMP_FILE)
    return {"rows": output_count, "mmsi_ok": output_count,
            "mmsi_skip": skipped, "skip_log": skip_log}


# ── 전처리된 CSV 여러 개 합산 (헤더 한 번만) ─────────────────────
def merge_outputs(part_files: list, merged_path: str):
    with open(merged_path, "w", newline="", encoding="utf-8") as fout:
        header_written = False
        for path in part_files:
            with open(path, "r", encoding="utf-8") as fin:
                for i, line in enumerate(fin):
                    if i == 0:
                        if not header_written:
                            fout.write(line); header_written = True
                    else:
                        fout.write(line)


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    input_files = resolve_input_files()

    print(f"[입력 파일 {len(input_files)}개]")
    for f in input_files:
        print(f"  {f}")

    out_cols = USE_COLS + [
        "dt", "dist_km", "expected_dist_km",
        "bearing_cog_diff", "cog_hdg_diff",
        "sog_change", "cog_change",
        "sog_status_ratio", "dist_expected_ratio",
        "cog_hdg_change", "cog_hdg_std",
    ]

    part_outputs  = []   # 개별 출력 경로 목록
    all_skip_logs = []
    total_rows = total_ok = total_skip = 0

    # ── 파일별 개별 처리 ────────────────────────────────────────────
    for fpath in input_files:
        stem        = os.path.splitext(os.path.basename(fpath))[0]
        out_path    = f"{stem}_preprocessed.csv"
        skip_path   = f"{stem}_skip_log.csv"

        print(f"\n[{stem}] 처리 중...")
        result = process_file(fpath, out_path, out_cols)

        # 개별 skip 로그
        with open(skip_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["mmsi", "reason"])
            w.writerows(result["skip_log"])

        part_outputs.append(out_path)
        all_skip_logs.extend(result["skip_log"])
        total_rows += result["rows"]
        total_ok   += result["mmsi_ok"]    # 행 수 (mmsi 수 아님)
        total_skip += result["mmsi_skip"]

        # 개별 요약
        seen = set()
        with open(out_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f): seen.add(row.get("mmsi", ""))
        print(f"  → {out_path}  ({len(seen):,} MMSI, {result['rows']:,} 행, "
              f"제거 {result['mmsi_skip']:,} MMSI)")

    # ── 전체 합산 출력 (파일 2개 이상일 때만) ──────────────────────
    if len(input_files) > 1:
        print(f"\n[합산] {OUTPUT_FILE} 생성 중...")
        merge_outputs(part_outputs, OUTPUT_FILE)

        # 합산 skip 로그
        merged_skip = "ais_skip_log.csv"
        with open(merged_skip, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["mmsi", "reason"])
            w.writerows(all_skip_logs)

        seen_all = set()
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f): seen_all.add(row.get("mmsi", ""))
        print(f"  → {OUTPUT_FILE}  ({len(seen_all):,} MMSI, {total_rows:,} 행)")

    # ── 최종 요약 ───────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"[결과 요약]")
    print(f"  입력 파일:   {len(input_files)}개")
    if len(input_files) > 1:
        print(f"  개별 출력:   {len(part_outputs)}개  (*_preprocessed.csv)")
        print(f"  합산 출력:   {OUTPUT_FILE}")
    else:
        print(f"  출력:        {part_outputs[0]}")
    print(f"  총 출력 행:  {total_rows:,}")
    print(f"  제거 MMSI:   {total_skip:,}")
    print(f"완료!")


if __name__ == "__main__":
    main()