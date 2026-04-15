"""
AIS 데이터 전처리 스크립트

입력: ais-YYYY-MM-DD.csv
출력: ais_preprocessed.csv

사용 피처:
    mmsi, base_date_time, latitude, longitude,
    sog, cog, heading, status, vessel_type
"""

import csv
import math
import os
import subprocess
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────────────────
INPUT_FILE  = "ais-2025-12-31.csv"
OUTPUT_FILE = "ais_preprocessed.csv"

MIN_SEQ_LEN = 10

USE_COLS = [
    "mmsi", "base_date_time",
    "latitude", "longitude",
    "sog", "cog", "heading",
    "status", "vessel_type",
]

# ── CSV 한 줄씩 읽기 ──────────────────────────────────────────────
def iter_lines_csv(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line.rstrip("\n")

# ── 결측값 처리 ───────────────────────────────────────────────────
def fill_missing(rows: list) -> list:
    defaults = {"sog": 0.0, "cog": 0.0, "heading": 511.0, "status": 15.0, "vessel_type": 0.0}
    prev = dict(defaults)
    for row in rows:
        for col, default in defaults.items():
            val = row.get(col, "")
            if val == "":
                row[col] = prev[col]
            else:
                try:
                    row[col] = float(val)
                    prev[col] = row[col]
                except ValueError:
                    row[col] = prev[col]
    return rows

# ── 파생 피처 추가 ────────────────────────────────────────────────
def add_derived_features(rows: list) -> list:
    for i, row in enumerate(rows):
        if i == 0:
            row["dt"] = 0.0
            row["dist_km"] = 0.0
            row["cog_hdg_diff"] = 0.0
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

        # cog_hdg_diff
        try:
            hdg = float(row["heading"])
            if hdg == 511.0:
                row["cog_hdg_diff"] = -1.0
            else:
                diff = abs(float(row["cog"]) - hdg)
                if diff > 180.0:
                    diff = 360.0 - diff
                row["cog_hdg_diff"] = round(diff, 1)
        except Exception:
            row["cog_hdg_diff"] = -1.0

    return rows

# ── 위치 점프 필터 ────────────────────────────────────────────────
def has_position_jump(rows: list) -> bool:
    for row in rows:
        try:
            dt      = float(row["dt"])
            dist_km = float(row["dist_km"])
            max_dist_km = (dt / 3600) * 92.6 * 1.2
            if dist_km > max_dist_km:
                return True
        except (ValueError, KeyError):
            pass
    return False
def has_invalid(rows: list) -> bool:
    for row in rows:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            sog = float(row["sog"])
        except (ValueError, KeyError):
            return True
        if not (-90 <= lat <= 90):   return True
        if not (-180 <= lon <= 180): return True
        if sog < 0:                  return True
    return False

# ── 메인 ──────────────────────────────────────────────────────────
def main():
    print("[1/5] CSV 파싱 및 임시 파일 저장 중 (스트리밍)...")
    TEMP_FILE = "ais_temp_sorted.csv"
    out_cols = USE_COLS + ["dt", "dist_km", "cog_hdg_diff"]
    header = None
    total = 0

    # 임시 파일에 파싱된 데이터 저장 (mmsi 기준 정렬 위해)
    with open(TEMP_FILE, "w", newline="", encoding="utf-8") as tmp:
        writer = csv.DictWriter(tmp, fieldnames=USE_COLS, extrasaction="ignore")
        writer.writeheader()
        for line in iter_lines_csv(INPUT_FILE):
            line = line.strip()
            if not line:
                continue
            if header is None:
                header = [c.strip() for c in line.split(",")]
                continue
            values = line.split(",")
            if len(values) != len(header):
                continue
            row = {header[i]: values[i].strip() for i in range(len(header))}
            mmsi = row.get("mmsi", "")
            if not mmsi:
                continue
            # 이상값 빠른 필터
            try:
                lat = float(row.get("latitude", ""))
                lon = float(row.get("longitude", ""))
                sog = float(row.get("sog", "0") or "0")
                if not (-90 <= lat <= 90): continue
                if not (-180 <= lon <= 180): continue
                if sog < 0: continue
            except ValueError:
                continue
            writer.writerow({col: row.get(col, "") for col in USE_COLS})
            total += 1
            if total % 500000 == 0:
                print(f"      {total:,} 행 처리 중...")

    print(f"      총 {total:,} 행 저장")

    print("[2/5] MMSI 기준 정렬 중...")
    # 헤더 읽기
    with open(TEMP_FILE, "r", encoding="utf-8") as f:
        header_line = f.readline()
        rows = f.readlines()

    # MMSI(숫자) → base_date_time 순 정렬
    def sort_key(line):
        parts = line.split(",", 2)
        try:
            mmsi = int(parts[0])
        except ValueError:
            mmsi = 0
        dt = parts[1] if len(parts) > 1 else ""
        return (mmsi, dt)

    rows.sort(key=sort_key)

    with open(TEMP_FILE, "w", encoding="utf-8") as f:
        f.write(header_line)
        f.writelines(rows)

    del rows
    print("      정렬 완료")

    print("[3/5] MMSI별 전처리 및 출력 저장 중...")
    skipped = 0
    output_count = 0
    current_mmsi = None
    current_rows = []
    skip_log = []  # (mmsi, 이유) 목록

    def process_and_write(rows, writer, mmsi):
        if len(rows) < MIN_SEQ_LEN:
            skip_log.append((mmsi, f"시퀀스 부족 ({len(rows)}개)"))
            return 0
        if has_invalid(rows):
            skip_log.append((mmsi, "이상값 (위도/경도 범위 초과 또는 SOG 음수)"))
            return 0
        rows = fill_missing(rows)
        rows = add_derived_features(rows)
        if has_position_jump(rows):
            skip_log.append((mmsi, "위치 점프 감지"))
            return 0
        writer.writerows(rows)
        return len(rows)

    with open(TEMP_FILE, "r", encoding="utf-8") as fin, \
         open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as fout:

        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=out_cols, extrasaction="ignore")
        writer.writeheader()

        for row in reader:
            mmsi = row.get("mmsi", "")
            if mmsi != current_mmsi:
                if current_mmsi is not None:
                    n = process_and_write(current_rows, writer, current_mmsi)
                    if n == 0:
                        skipped += 1
                    else:
                        output_count += n
                current_mmsi = mmsi
                current_rows = [row]
            else:
                current_rows.append(row)

        # 마지막 MMSI 처리
        if current_rows:
            n = process_and_write(current_rows, writer, current_mmsi)
            if n == 0:
                skipped += 1
            else:
                output_count += n

    import os
    os.remove(TEMP_FILE)

    # 제거 로그 저장
    SKIP_LOG_FILE = "ais_skip_log.csv"
    with open(SKIP_LOG_FILE, "w", newline="", encoding="utf-8") as f:
        log_writer = csv.writer(f)
        log_writer.writerow(["mmsi", "reason"])
        log_writer.writerows(skip_log)

    print(f"      제거된 MMSI: {skipped:,}")
    print(f"      출력 행 수: {output_count:,}")
    print(f"      제거 로그: {SKIP_LOG_FILE}")
    print(f"[4/5] 저장 완료: {OUTPUT_FILE}")
    print("[5/5] 완료!")

if __name__ == "__main__":
    main()