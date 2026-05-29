#!/usr/bin/env python3
# coding: utf-8
"""
download_ais.py
───────────────
Marine Cadastre AIS 데이터 다운로드 스크립트

사용법:
    python download_ais.py --year 2025 --out D:/ais_data/raw/
    python download_ais.py --year 2025 --months 6 7 8 9 10 11 12 --out D:/ais_data/raw/
    python download_ais.py --year 2025 --max_gb 40 --out D:/ais_data/raw/
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# 2025~: ais-YYYY-MM-DD.csv.zst  (zstd)
# ~2024: AIS_YYYY_MM_DD.zip       (zip 내부 CSV)
BASE_URL_ZST = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{year}/ais-{date}.csv.zst"
BASE_URL_ZIP = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{year}/AIS_{date_us}.zip"

def get_url_and_dest(year: int, d) -> tuple:
    ds_dash  = d.strftime("%Y-%m-%d")
    ds_us    = d.strftime("%Y_%m_%d")
    if year >= 2025:
        url  = BASE_URL_ZST.format(year=year, date=ds_dash)
        name = f"ais-{ds_dash}.csv.zst"
    else:
        url  = BASE_URL_ZIP.format(year=year, date_us=ds_us)
        name = f"AIS_{ds_us}.zip"
    return url, name


def get_dates(year: int, months: list[int] | None) -> list[date]:
    """year 의 해당 월 날짜 목록 (내림차순)"""
    all_dates = []
    start = date(year, 1, 1)
    end   = date(year, 12, 31)
    d = start
    while d <= end:
        if months is None or d.month in months:
            all_dates.append(d)
        d += timedelta(days=1)
    return list(reversed(all_dates))  # 최신부터


def head_size(url: str) -> int | None:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            cl = r.getheader("Content-Length")
            return int(cl) if cl else None
    except Exception:
        return None


def download_file(url: str, dest: Path, expected_bytes: int | None = None) -> bool:
    try:
        print(f"  ↓ {dest.name}", end="", flush=True)
        t0 = time.time()

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, \
             open(dest, "wb") as fout:
            downloaded = 0
            while True:
                chunk = resp.read(1 << 20)  # 1 MB chunks
                if not chunk:
                    break
                fout.write(chunk)
                downloaded += len(chunk)

        elapsed = time.time() - t0
        mb = downloaded / (1 << 20)
        print(f"  {mb:.0f}MB  {elapsed:.0f}s  ({mb/elapsed:.1f} MB/s)")
        return True
    except Exception as e:
        print(f"  [실패] {e}")
        if dest.exists():
            dest.unlink()
        return False


def free_gb(path: str) -> float:
    import shutil
    return shutil.disk_usage(path).free / (1 << 30)


def main():
    parser = argparse.ArgumentParser(description="Marine Cadastre AIS 다운로드")
    parser.add_argument("--year",    type=int, default=2025)
    parser.add_argument("--months",  type=int, nargs="*", default=None,
                        help="다운로드할 월 목록 (기본: 전체). 예: --months 6 7 8")
    parser.add_argument("--out",     type=str, default="D:/ais_data/raw/",
                        help="저장 디렉터리")
    parser.add_argument("--max_gb",  type=float, default=40.0,
                        help="최대 누적 다운로드 GB (기본: 40)")
    parser.add_argument("--reserve_gb", type=float, default=4.0,
                        help="드라이브에 남겨둘 여유 GB (기본: 4)")
    parser.add_argument("--skip_existing", action="store_true", default=True,
                        help="이미 존재하는 파일 스킵 (기본: True)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dates = get_dates(args.year, args.months)
    print(f"\n[AIS 다운로드] {args.year}년 {'전체' if not args.months else str(args.months)}")
    print(f"  저장 위치: {out_dir}")
    print(f"  대상 날짜: {len(dates)}일  (최신부터)")
    print(f"  최대 다운로드: {args.max_gb} GB")

    downloaded_gb = 0.0
    success = skip = fail = 0

    for d in dates:
        url, fname = get_url_and_dest(args.year, d)
        dest = out_dir / fname

        # 이미 존재하면 스킵
        if args.skip_existing and dest.exists() and dest.stat().st_size > 0:
            mb = dest.stat().st_size / (1 << 20)
            print(f"  [스킵] {dest.name}  ({mb:.0f}MB)")
            skip += 1
            downloaded_gb += dest.stat().st_size / (1 << 30)
            continue

        # 파일 크기 예측 (HEAD)
        size_b = head_size(url)
        size_gb = (size_b or 230 << 20) / (1 << 30)

        # 누적 한도 / 드라이브 여유 확인
        if downloaded_gb + size_gb > args.max_gb:
            print(f"\n  [한도] {args.max_gb}GB 누적 한도 도달, 중단")
            break
        df = free_gb(str(out_dir.drive + "\\"))
        if df - size_gb < args.reserve_gb:
            print(f"\n  [공간부족] 드라이브 여유 {df:.1f}GB, 중단")
            break

        ok = download_file(url, dest, size_b)
        if ok:
            success += 1
            downloaded_gb += dest.stat().st_size / (1 << 30)
        else:
            fail += 1

    print(f"\n{'='*50}")
    print(f"  다운로드 완료: {success}개 성공  {skip}개 스킵  {fail}개 실패")
    print(f"  누적: {downloaded_gb:.1f} GB")
    print(f"  저장 위치: {out_dir}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
