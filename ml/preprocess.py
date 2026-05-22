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

피처 (12개):
    sog, cog, heading, status,
    dt, dist_km,
    cog_hdg_diff, sog_change, cog_hdg_change,
    speed_consistency,
    lat_speed, lon_speed
"""

import csv
import glob
import io
import math
import os
import statistics
import sys
from datetime import datetime

# zstandard — .csv.zst 압축 파일 스트리밍 지원 (없으면 .csv 만 처리)
try:
    import zstandard as zstd
    _HAS_ZST = True
except ImportError:
    _HAS_ZST = False

# ── 입력 설정 (CLI 인수가 없을 때 사용) ──────────────────────────
INPUT_GLOB  = "ais-*.csv"   # 현재 폴더의 ais-*.csv 전부
INPUT_DIR   = ""             # 폴더 지정 시 여기에 경로 입력
INPUT_FILES = []             # 파일 명시적 목록

OUTPUT_FILE = "ais_preprocessed.csv"

MIN_SEQ_LEN  = 10
SEQ_BREAK_DT = 3600

USE_COLS = [
    "mmsi", "base_date_time",
    "latitude", "longitude",
    "sog", "cog", "heading",
    "status", "vessel_type",
]

# ── 컬럼명 정규화 (구형/신형 Marine Cadastre 포맷 호환) ──────────────
# 신형(2025+, .zst): mmsi, base_date_time, longitude, latitude, ...
# 구형(~2024, .zip): MMSI, BaseDateTime, LAT, LON, ..., VesselType, Status
# 키 = 소문자·언더스코어제거,  값 = canonical(USE_COLS) 이름
COL_ALIAS = {
    "mmsi":         "mmsi",
    "basedatetime": "base_date_time",
    "lat":          "latitude",
    "latitude":     "latitude",
    "lon":          "longitude",
    "longitude":    "longitude",
    "sog":          "sog",
    "cog":          "cog",
    "heading":      "heading",
    "status":       "status",
    "vesseltype":   "vessel_type",
}


def _norm_col(c: str) -> str:
    """헤더 컬럼명을 canonical 이름으로 정규화 (대소문자/언더스코어 무시)."""
    key = c.strip().lower().replace("_", "")
    return COL_ALIAS.get(key, c.strip().lower())

# 출력 피처 컬럼 (모듈 레벨 공개 — run_pipeline.py 에서 참조)
OUT_COLS = USE_COLS + [
    "dt", "dist_km",
    "cog_hdg_diff",
    "sog_change",
    "cog_hdg_change",
    "speed_consistency",
    "lat_speed", "lon_speed",
]

STATUS_MAX_SOG = {
    0: 30.0, 1: 1.0,  2: 5.0,  3: 10.0,
    4: 10.0, 5: 1.0,  6: 5.0,  7: 15.0, 8: 15.0,
}
DEFAULT_MAX_SOG = 30.0


# ── 파일 stem 추출 (.csv.zst 이중 확장자 대응) ───────────────────
def _file_stem(path: str) -> str:
    name = os.path.basename(path)
    if name.endswith(".csv.zst"):
        return name[:-8]
    if name.endswith(".csv"):
        return name[:-4]
    if name.endswith(".zip"):
        return name[:-4]
    return os.path.splitext(name)[0]


# ── 입력 파일 목록 결정 ───────────────────────────────────────────
def _filter_by_years(files: list, years: int) -> list:
    """파일명에서 날짜 파싱 후 최근 N년치만 필터링. ais-YYYY-MM-DD.csv 형식 가정."""
    import re
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=years * 365)
    filtered = []
    unparsed = []
    for f in files:
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', os.path.basename(f))
        if m:
            try:
                file_date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if file_date >= cutoff:
                    filtered.append(f)
            except ValueError:
                unparsed.append(f)
        else:
            unparsed.append(f)  # 날짜 파싱 불가 → 포함
    if unparsed:
        print(f"  [경고] 날짜 파싱 불가 파일 {len(unparsed)}개 → 전부 포함")
        filtered += unparsed
    return sorted(filtered)


def resolve_input_files(years: int = None) -> list:
    # 1) CLI 인수 (--output_dir / --years / --tag 는 제외하고 파일/폴더만)
    positional = []
    skip_next  = False
    for a in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if a in ("--output_dir", "--years", "--tag"):
            skip_next = True
            continue
        if a.startswith("--"):
            continue
        positional.append(a)

    if positional:
        files = []
        for a in positional:
            if os.path.isdir(a):
                files += sorted(glob.glob(os.path.join(a, "*.csv")))
                files += sorted(glob.glob(os.path.join(a, "*.csv.zst")))
                files += sorted(glob.glob(os.path.join(a, "*.zip")))
            else:
                files += sorted(glob.glob(a))
        files = sorted(set(f for f in files if os.path.isfile(f)))
        if files:
            if years:
                files = _filter_by_years(files, years)
            return files

    # 2) 스크립트 내 설정
    if INPUT_FILES:
        files = [f for f in INPUT_FILES if os.path.isfile(f)]
    elif INPUT_DIR:
        files  = sorted(glob.glob(os.path.join(INPUT_DIR, "*.csv")))
        files += sorted(glob.glob(os.path.join(INPUT_DIR, "*.csv.zst")))
        files += sorted(glob.glob(os.path.join(INPUT_DIR, "*.zip")))
        files  = sorted(set(f for f in files if os.path.isfile(f)))
    else:
        files  = sorted(glob.glob(INPUT_GLOB))
        files += sorted(glob.glob(INPUT_GLOB.replace("*.csv", "*.csv.zst")))
        files  = sorted(set(f for f in files if os.path.isfile(f)))

    if not files:
        raise FileNotFoundError(
            "입력 파일 없음. 사용법:\n"
            "  python preprocess.py D:\\ais_data\\raw\\\n"
            "  python preprocess.py data/ais-*.csv\n"
            "  python preprocess.py file.csv.zst\n"
            "또는 스크립트 상단 INPUT_GLOB / INPUT_DIR / INPUT_FILES 설정"
        )
    if years:
        files = _filter_by_years(files, years)
    return files


# ── CSV 한 줄씩 읽기 (.csv / .csv.zst / .zip 지원) ──────────────
def iter_lines_csv(path: str):
    if path.endswith(".zst"):
        if not _HAS_ZST:
            raise ImportError(
                ".zst 파일을 읽으려면 zstandard 라이브러리가 필요합니다.\n"
                "  pip install zstandard"
            )
        with open(path, "rb") as fh:
            dctx = zstd.ZstdDecompressor()
            with dctx.stream_reader(fh) as reader:
                text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
                for line in text:
                    yield line.rstrip("\n")
    elif path.endswith(".zip"):
        # Marine Cadastre 2024 이전: AIS_YYYY_MM_DD.zip 안에 CSV 1개
        import zipfile
        try:
            zf = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile:
            # 손상/잘린 zip (다운로드 중단, 404 HTML 등) → 경고 후 건너뜀
            print(f"  [경고] 손상 zip 건너뜀: {os.path.basename(path)}")
            return
        with zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                return
            with zf.open(csv_names[0]) as inner:
                text = io.TextIOWrapper(inner, encoding="utf-8", errors="replace")
                try:
                    for line in text:
                        yield line.rstrip("\n")
                except zipfile.BadZipFile:
                    # 압축 해제 중간에 잘린 경우
                    print(f"  [경고] zip 해제 중 손상: {os.path.basename(path)}")
                    return
    else:
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
                # 구형/신형 컬럼명을 canonical 로 정규화
                header = [_norm_col(c) for c in line.split(",")]
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
# 출력 피처 (12개):
#   dt, dist_km, cog_hdg_diff, sog_change, cog_hdg_change,
#   speed_consistency, lat_speed, lon_speed
def add_derived_features(rows: list) -> list:
    for i, row in enumerate(rows):
        if i == 0:
            row["dt"]                 = 0.0
            row["dist_km"]            = 0.0
            row["cog_hdg_diff"]       = 0.0
            row["sog_change"]         = 0.0
            row["cog_hdg_change"]     = 0.0
            row["speed_consistency"]  = 1.0
            row["lat_speed"]          = 0.0
            row["lon_speed"]          = 0.0
            continue

        prev = rows[i - 1]

        # dt  (fromisoformat: 신형 "YYYY-MM-DD HH:MM:SS" / 구형 "...T..." 모두 처리)
        try:
            t1 = datetime.fromisoformat(prev["base_date_time"].strip())
            t2 = datetime.fromisoformat(row["base_date_time"].strip())
            row["dt"] = max(0.0, (t2 - t1).total_seconds())
        except Exception:
            row["dt"] = 0.0

        # dist_km (Haversine)
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
                row["cog_hdg_diff"] = round(360.0 - diff if diff > 180.0 else diff, 1)
        except Exception:
            row["cog_hdg_diff"] = -1.0

        # sog_change
        try:
            row["sog_change"] = round(abs(float(row["sog"]) - float(prev["sog"])), 4)
        except Exception:
            row["sog_change"] = 0.0

        # cog_hdg_change
        try:
            pc = float(prev.get("cog_hdg_diff", 0.0))
            cc = float(row["cog_hdg_diff"])
            row["cog_hdg_change"] = 0.0 if pc < 0 or cc < 0 else round(abs(cc - pc), 4)
        except Exception:
            row["cog_hdg_change"] = 0.0

        # speed_consistency: 실제 이동거리 / SOG 기반 예상 거리
        # 정상 ≈ 1.0 / 위치 조작이나 SOG 허위 보고 시 크게 벗어남
        # sog=0 또는 dt=0이면 비율 정의 불가 → 1.0 유지
        try:
            sog  = float(row["sog"])
            dt   = float(row["dt"])
            dist = float(row["dist_km"])
            if dt > 0.0 and sog >= 0.1:
                expected = sog * dt / 3600.0 * 1.852
                row["speed_consistency"] = round(dist / (expected + 1e-6), 4)
            else:
                row["speed_consistency"] = 1.0
        except Exception:
            row["speed_consistency"] = 1.0

        # lat_speed: 위도 변화율 (도/초)
        # dt=0이면 정의 불가 → 0.0 처리
        try:
            dlat = float(row["latitude"]) - float(prev["latitude"])
            dt   = float(row["dt"])
            row["lat_speed"] = round(dlat / dt, 6) if dt > 0.0 else 0.0
        except Exception:
            row["lat_speed"] = 0.0

        # lon_speed: 경도 변화율 (도/초)
        try:
            dlon = float(row["longitude"]) - float(prev["longitude"])
            dt   = float(row["dt"])
            row["lon_speed"] = round(dlon / dt, 6) if dt > 0.0 else 0.0
        except Exception:
            row["lon_speed"] = 0.0

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
    input_path  : 원본 CSV (또는 .csv.zst) 1개
    output_path : 전처리 결과 저장 경로
    반환: {"rows": int, "mmsi_ok": int, "mmsi_skip": int, "skip_log": list}
    """
    stem      = _file_stem(input_path)
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
    import argparse
    parser = argparse.ArgumentParser(description="AIS 전처리", add_help=False)
    parser.add_argument("--output",     type=str, default=None,
                        help="단일 파일 출력 경로 (파일 1개 입력 시에만)")
    parser.add_argument("--output_dir", type=str, default=None,
                        help="전처리 결과 저장 디렉터리 (기본: 현재 폴더)")
    parser.add_argument("--years",      type=int, default=None,
                        help="최근 N년치만 처리 (기본: 전체)")
    parser.add_argument("--tag",        type=str, default=None,
                        help="메타 태그 (폴더명에 사용)")
    parser.add_argument("-h", "--help", action="help")
    pargs, _ = parser.parse_known_args()
    output_override = pargs.output

    input_files = resolve_input_files(years=pargs.years)

    print(f"[입력 파일 {len(input_files)}개]" +
          (f"  (최근 {pargs.years}년치)" if pargs.years else ""))
    for f in input_files:
        print(f"  {f}")
    if output_override:
        print(f"[출력 경로] {output_override}")
        os.makedirs(os.path.dirname(output_override) or ".", exist_ok=True)

    out_cols = OUT_COLS   # 모듈 레벨 상수 사용

    # 출력 디렉터리 결정
    out_dir = pargs.output_dir or "."
    os.makedirs(out_dir, exist_ok=True)

    part_outputs  = []
    all_skip_logs = []
    total_rows = total_ok = total_skip = 0

    # ── 파일별 개별 처리 ────────────────────────────────────────────
    for fpath in input_files:
        stem      = _file_stem(fpath)
        out_path  = (output_override
                     if (output_override and len(input_files) == 1)
                     else os.path.join(out_dir, f"{stem}_preprocessed.csv"))
        skip_path = os.path.join(out_dir, f"{stem}_skip_log.csv")

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
    merged_path = output_override or os.path.join(out_dir, OUTPUT_FILE)
    if len(input_files) > 1:
        print(f"\n[합산] {merged_path} 생성 중...")
        merge_outputs(part_outputs, merged_path)

        # 합산 skip 로그
        merged_skip = os.path.join(out_dir, "ais_skip_log.csv")
        with open(merged_skip, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["mmsi", "reason"])
            w.writerows(all_skip_logs)

        seen_all = set()
        with open(merged_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f): seen_all.add(row.get("mmsi", ""))
        print(f"  → {merged_path}  ({len(seen_all):,} MMSI, {total_rows:,} 행)")

    # ── 최종 요약 ───────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"[결과 요약]")
    print(f"  입력 파일:   {len(input_files)}개")
    if len(input_files) > 1:
        print(f"  개별 출력:   {len(part_outputs)}개  (*_preprocessed.csv)")
        print(f"  합산 출력:   {merged_path}")
    else:
        print(f"  출력:        {part_outputs[0]}")
    print(f"  총 출력 행:  {total_rows:,}")
    print(f"  제거 MMSI:   {total_skip:,}")
    print(f"완료!")

    # ── preprocess_meta.json 저장 ────────────────────────────────
    import json
    from datetime import datetime as _dt
    meta_path = os.path.join(
        os.path.dirname(part_outputs[0]) or ".", "preprocess_meta.json"
    )
    meta = {
        "features": out_cols,
        "years": pargs.years,
        "tag": pargs.tag,
        "source_files": [os.path.abspath(f) for f in input_files],
        "output_file": os.path.abspath(
            merged_path if len(input_files) > 1 else part_outputs[0]
        ),
        "total_rows": total_rows,
        "created": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  메타 저장:   {meta_path}")


if __name__ == "__main__":
    main()