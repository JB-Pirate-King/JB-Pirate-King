"""
NOAA Marine Cadastre AIS 데이터 자동 다운로드 + 압축 해제 + 전처리 파이프라인

대상: 2020~2025년 1월 1일~31일 (총 186파일)
URL:  https://noaaocm.blob.core.windows.net/ais/csv2/csv{year}/ais-{year}-01-{dd}.csv.zst

D드라이브 기존 파일 자동 감지:
  - D:\ais-2024-01-*.csv.zst  (정상 확장자)
  - D:\ais-2025-01-*.tgz      (잘못된 확장자지만 실제로는 .zst)

출력 (압축 해제 + 컬럼 정규화):
  D:\AIS\{year}\ais-{year}-01-{dd}.csv

실행 예:
  python download_ais.py                   # 다운로드 + 압축 해제만
  python download_ais.py --preprocess      # + preprocess.py 실행
  python download_ais.py --train           # 전체 파이프라인
  python download_ais.py --workers 6       # 병렬 다운로드 수 조정 (기본 4)
  python download_ais.py --year 2020 2021  # 특정 연도만
  python download_ais.py --decompress-only # 기존 .zst 파일만 압축 해제 (다운로드 없이)

필요 패키지:
  pip install requests zstandard tqdm
"""

import argparse
import csv
import io
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

BASE_URL    = "https://noaaocm.blob.core.windows.net/ais/csv2/csv{year}/ais-{year}-01-{day:02d}.csv.zst"
ZST_DIR     = Path("D:/")          # 기존 .zst 파일들이 있는 위치 (사용자 기준)
OUTPUT_DIR  = Path("D:/AIS")       # 압축 해제된 CSV 저장 위치
YEARS       = list(range(2020, 2026))  # 2020 ~ 2025
ZST_MAGIC   = b"\x28\xb5\x2f\xfd"    # zstandard 매직 바이트

# NOAA CSV2 컬럼명 → preprocess.py 기대 컬럼명 (모두 소문자로 비교)
COLUMN_MAP = {
    "mmsi":           "mmsi",
    "basedatetime":   "base_date_time",
    "base_date_time": "base_date_time",
    "lat":            "latitude",
    "latitude":       "latitude",
    "lon":            "longitude",
    "longitude":      "longitude",
    "sog":            "sog",
    "cog":            "cog",
    "heading":        "heading",
    "status":         "status",
    "navstatus":      "status",
    "vesseltype":     "vessel_type",
    "vessel_type":    "vessel_type",
    "vtype":          "vessel_type",
}


def _check_deps():
    missing = []
    for pkg in ("requests", "zstandard", "tqdm"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[설치 중] pip install {' '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)


def _normalize_header(header: list) -> list:
    """NOAA 컬럼명 → preprocess.py 호환 컬럼명으로 정규화"""
    return [COLUMN_MAP.get(col.strip().lower(), col.strip().lower()) for col in header]


def _is_zst(data: bytes) -> bool:
    return data[:4] == ZST_MAGIC


def _decompress_zst(zst_bytes: bytes, out_path: Path) -> int:
    """
    zst 바이트를 CSV로 디코딩 + 컬럼명 정규화 후 out_path에 저장.
    반환값: 데이터 행 수 (헤더 제외)
    """
    import zstandard as zstd

    dctx = zstd.ZstdDecompressor()
    raw  = dctx.decompress(zst_bytes, max_output_size=2 * 1024**3)
    text = raw.decode("utf-8", errors="replace")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    header = None

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in csv.reader(io.StringIO(text)):
            if header is None:
                header = _normalize_header(row)
                writer.writerow(header)
            else:
                writer.writerow(row)
                row_count += 1

    return row_count


def _find_existing_zst(year: int, day: int) -> Path | None:
    """D드라이브에서 해당 날짜의 기존 .zst 파일을 찾음 (확장자 불문)"""
    stem = f"ais-{year}-01-{day:02d}"
    for ext in (".csv.zst", ".zst", ".tgz", ".gz"):
        candidate = ZST_DIR / f"{stem}{ext}"
        if candidate.exists() and candidate.stat().st_size > 0:
            # 실제로 zst인지 확인
            with open(candidate, "rb") as f:
                magic = f.read(4)
            if _is_zst(magic):
                return candidate
    return None


def _valid_date(year: int, day: int) -> bool:
    try:
        date(year, 1, day)
        return True
    except ValueError:
        return False


def process_one(year: int, day: int, download: bool = True) -> dict:
    """
    단일 날짜 파일 처리 (기존 파일 재사용 or 다운로드 → 압축 해제).
    반환: {"year": int, "day": int, "path": Path, "rows": int, "status": str}
    """
    import requests

    out_path = OUTPUT_DIR / str(year) / f"ais-{year}-01-{day:02d}.csv"

    # 이미 압축 해제된 CSV가 있으면 건너뜀
    if out_path.exists() and out_path.stat().st_size > 0:
        return {"year": year, "day": day, "path": out_path,
                "rows": -1, "status": "skip"}

    # 기존 .zst 파일 확인
    zst_path = _find_existing_zst(year, day)

    if zst_path:
        try:
            with open(zst_path, "rb") as f:
                data = f.read()
            rows = _decompress_zst(data, out_path)
            return {"year": year, "day": day, "path": out_path,
                    "rows": rows, "status": "decomp"}
        except Exception as e:
            return {"year": year, "day": day, "path": out_path,
                    "rows": 0, "status": f"err: {e}"}

    if not download:
        return {"year": year, "day": day, "path": out_path,
                "rows": 0, "status": "missing"}

    # 다운로드
    url = BASE_URL.format(year=year, day=day)
    try:
        from tqdm import tqdm
        with requests.get(url, stream=True, timeout=300) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            buf   = io.BytesIO()

            desc = f"{year}-01-{day:02d}"
            with tqdm(total=total, unit="B", unit_scale=True,
                      desc=desc, leave=False, position=1) as pbar:
                for chunk in resp.iter_content(65536):
                    buf.write(chunk)
                    pbar.update(len(chunk))

        data = buf.getvalue()
        if not _is_zst(data):
            return {"year": year, "day": day, "path": out_path,
                    "rows": 0, "status": "err: 알 수 없는 파일 포맷"}

        rows = _decompress_zst(data, out_path)
        return {"year": year, "day": day, "path": out_path,
                "rows": rows, "status": "ok"}

    except Exception as e:
        return {"year": year, "day": day, "path": out_path,
                "rows": 0, "status": f"err: {e}"}


def run_all(years=None, max_workers=4, download=True):
    """병렬로 모든 날짜 처리. 결과 목록 반환."""
    from tqdm import tqdm

    if years is None:
        years = YEARS

    tasks = [
        (year, day)
        for year in years
        for day in range(1, 32)
        if _valid_date(year, day)
    ]

    results = []
    counts = {"ok": 0, "decomp": 0, "skip": 0, "missing": 0, "err": 0}

    label = "압축 해제" if not download else "다운로드/해제"
    print(f"\n[{label}] 총 {len(tasks)}개 파일 | 병렬 {max_workers}개\n")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process_one, y, d, download): (y, d) for y, d in tasks}
        with tqdm(total=len(tasks), desc="전체 진행", position=0) as pbar:
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)

                st = r["status"]
                if st == "ok":
                    counts["ok"] += 1
                elif st == "decomp":
                    counts["decomp"] += 1
                elif st == "skip":
                    counts["skip"] += 1
                elif st == "missing":
                    counts["missing"] += 1
                else:
                    counts["err"] += 1
                    tqdm.write(f"  [오류] {r['year']}-01-{r['day']:02d}: {st}")

                pbar.set_postfix(**counts)
                pbar.update(1)

    print(f"\n완료: 신규다운 {counts['ok']}개, 기존파일해제 {counts['decomp']}개, "
          f"건너뜀 {counts['skip']}개, "
          f"미존재 {counts['missing']}개, 오류 {counts['err']}개")

    return results


def run_preprocess(years=None):
    """압축 해제된 CSV 폴더들을 preprocess.py로 처리"""
    if years is None:
        years = YEARS

    csv_dirs = [str(OUTPUT_DIR / str(y)) for y in years
                if (OUTPUT_DIR / str(y)).exists()]

    if not csv_dirs:
        print("[전처리] CSV 폴더 없음")
        return False

    script = Path(__file__).parent / "preprocess.py"
    if not script.exists():
        print(f"[전처리] preprocess.py 없음: {script}")
        return False

    print(f"\n[전처리] preprocess.py 실행 중...")
    print(f"  입력 폴더 {len(csv_dirs)}개: {', '.join(str(d) for d in csv_dirs)}")

    result = subprocess.run(
        [sys.executable, str(script)] + csv_dirs,
        cwd=str(Path(__file__).parent)
    )
    return result.returncode == 0


def run_train(model: str = "all"):
    """train_benchmark.py + train_supervised.py 실행"""
    ml_dir = Path(__file__).parent

    print("\n[학습] 비지도 학습 (train_benchmark.py) ...")
    bench = ml_dir / "train_benchmark.py"
    if bench.exists():
        subprocess.run([sys.executable, str(bench)], check=True, cwd=str(ml_dir))
    else:
        print(f"  [건너뜀] train_benchmark.py 없음")

    print("\n[학습] 지도 학습 (train_supervised.py) ...")
    sup = ml_dir / "train_supervised.py"
    if sup.exists():
        subprocess.run([sys.executable, str(sup), "--model", model],
                       check=True, cwd=str(ml_dir))
    else:
        print(f"  [건너뜀] train_supervised.py 없음")


def print_status(years=None):
    """현재 다운로드/압축해제 상태 요약 출력"""
    if years is None:
        years = YEARS

    print(f"\n{'연도':>6} {'다운로드(zst)':>14} {'압축해제(csv)':>14} {'미완료':>8}")
    print("-" * 50)
    for year in years:
        zst_count = sum(1 for d in range(1, 32) if _valid_date(year, d)
                        and _find_existing_zst(year, d))
        csv_dir  = OUTPUT_DIR / str(year)
        csv_count = len(list(csv_dir.glob("*.csv"))) if csv_dir.exists() else 0
        total    = sum(1 for d in range(1, 32) if _valid_date(year, d))
        missing  = total - csv_count
        print(f"{year:>6} {zst_count:>14} {csv_count:>14} {missing:>8}")


def main():
    parser = argparse.ArgumentParser(
        description="NOAA AIS 1월 데이터 자동 다운로드 + ML 파이프라인")
    parser.add_argument("--year", type=int, nargs="+",
                        help="처리할 연도 (기본: 2020~2025)")
    parser.add_argument("--workers", type=int, default=4,
                        help="병렬 처리 수 (기본: 4)")
    parser.add_argument("--preprocess", action="store_true",
                        help="압축 해제 후 preprocess.py 자동 실행")
    parser.add_argument("--train", action="store_true",
                        help="다운로드 + 전처리 + 학습 전체 파이프라인")
    parser.add_argument("--decompress-only", action="store_true",
                        help="기존 .zst 파일만 압축 해제 (새 다운로드 없음)")
    parser.add_argument("--status", action="store_true",
                        help="현재 파일 상태만 출력")
    parser.add_argument("--model", default="all",
                        help="지도학습 모델 (기본: all)")
    parser.add_argument("--output-dir", default=None,
                        help=f"CSV 출력 경로 (기본: D:/AIS)")
    args = parser.parse_args()

    global OUTPUT_DIR
    if args.output_dir:
        OUTPUT_DIR = Path(args.output_dir)

    years = args.year or YEARS

    if args.status:
        print_status(years)
        return

    _check_deps()

    if args.status:
        print_status(years)
        return

    t0 = time.time()

    do_download = not args.decompress_only
    run_all(years=years, max_workers=args.workers, download=do_download)

    elapsed = time.time() - t0
    print(f"  소요 시간: {elapsed/60:.1f}분")

    if args.train or args.preprocess:
        ok = run_preprocess(years=years)
        if not ok:
            print("[경고] 전처리 실패. 학습을 건너뜁니다.")
            return

    if args.train:
        run_train(model=args.model)

    print("\n[완료] D:\\AIS\\{year}\\ 폴더에 압축 해제된 CSV 저장됨")
    print_status(years)


if __name__ == "__main__":
    main()
