"""
NOAA AIS 다년/다월 다운로드 (디스크 가드 + 스트리밍 처리)
============================================================
기본: 2016-2025년 / 각 연도에서 랜덤 3개월 제외 (=9개월 보존)
     1월은 이미 전처리됐다면 스킵 (D:\JB-Pirate-King-AIS\preprocessed\)

URL 패턴: https://noaaocm.blob.core.windows.net/ais/csv2/csv{year}/ais-{year}-{mm}-{dd}.csv.zst

자가복구:
  - 다운로드 실패 → 최대 3회 재시도
  - HTTP 404 (날짜 데이터 없음) → 정상 처리
  - 손상 파일 자동 삭제 후 재다운로드
  - 디스크 여유공간 < DISK_GUARD_GB 미만이면 즉시 중단 + 알림

스트리밍 모드 (--stream):
  - 다운로드 → 압축해제 → 전처리 → raw CSV 즉시 삭제
  - 피크 디스크 사용량 = preprocessed 총합만큼 (~50% 절감)

사용:
  python download_ais_allmonths.py                          # 2016-2025, 랜덤 3개월 드롭
  python download_ais_allmonths.py --drop-months 5         # 5개월 드롭 (보수적)
  python download_ais_allmonths.py --stream                # 스트리밍 모드
  python download_ais_allmonths.py --years 2020 2021       # 특정 연도
  python download_ais_allmonths.py --disk-guard-gb 150     # 디스크 여유 150GB 미만이면 중단
"""

import argparse
import calendar
import io
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# UTF-8 강제
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_URL    = "https://noaaocm.blob.core.windows.net/ais/csv2/csv{year}/ais-{year}-{month:02d}-{day:02d}.csv.zst"
AIS_DIR     = Path("D:/AIS")
PREPROC_DIR = Path("D:/JB-Pirate-King-AIS/preprocessed")
PREPROC_ALL = Path("D:/JB-Pirate-King-AIS/preprocessed_all")
ZST_MAGIC   = b"\x28\xb5\x2f\xfd"
MAX_RETRY   = 3
DEFAULT_DISK_GUARD_GB = 100   # D드라이브 여유 100GB 이하면 중단

# 컬럼명 정규화 맵
COLUMN_MAP = {
    "mmsi": "mmsi", "basedatetime": "base_date_time", "base_date_time": "base_date_time",
    "lat": "latitude", "latitude": "latitude", "lon": "longitude", "longitude": "longitude",
    "sog": "sog", "cog": "cog", "heading": "heading",
    "status": "status", "navstatus": "status",
    "vesseltype": "vessel_type", "vessel_type": "vessel_type", "vtype": "vessel_type",
}

ML_DIR = Path(__file__).parent


def notify(msg: str, title: str = "JB-Pirate-King | Download"):
    try:
        subprocess.run(
            [sys.executable, str(ML_DIR / "notify.py"), msg, title],
            timeout=10, capture_output=True
        )
    except Exception:
        pass


def disk_free_gb(drive: str = "D:\\") -> float:
    """드라이브 여유 공간 GB."""
    try:
        usage = shutil.disk_usage(drive)
        return usage.free / (1024**3)
    except Exception:
        return float("inf")


def is_valid_zst(path: Path) -> bool:
    try:
        if path.stat().st_size < 100:
            return False
        with open(path, "rb") as f:
            return f.read(4) == ZST_MAGIC
    except Exception:
        return False


def decompress_zst(zst_path: Path, out_path: Path) -> bool:
    """스트리밍 압축 해제 — 메모리 ~50MB/워커 (기존 ~3GB/워커)."""
    try:
        import zstandard as zstd
        import csv as csv_mod

        dctx = zstd.ZstdDecompressor()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(zst_path, "rb") as f_in:
            # 스트리밍: 청크 단위로 읽으며 줄 단위 처리
            reader_stream = dctx.stream_reader(f_in)
            text_stream = io.TextIOWrapper(reader_stream, encoding="utf-8", errors="replace")

            csv_reader = csv_mod.DictReader(text_stream)
            orig_fields = csv_reader.fieldnames or []
            mapped = {col: COLUMN_MAP.get(col.lower(), col.lower()) for col in orig_fields}
            new_fields = list(dict.fromkeys(mapped.values()))

            if "mmsi" not in new_fields:
                return False

            with open(out_path, "w", encoding="utf-8", newline="") as f_out:
                writer = csv_mod.DictWriter(f_out, fieldnames=new_fields, extrasaction="ignore")
                writer.writeheader()
                for row in csv_reader:
                    new_row = {mapped[k]: v for k, v in row.items() if k in mapped}
                    writer.writerow(new_row)
        return True
    except Exception as e:
        print(f"  [decompress 오류] {zst_path.name}: {e}")
        return False


def preprocess_one(csv_path: Path, out_dir: Path, timeout: int = 2400) -> bool:
    """단일 CSV 파일 전처리 → preprocessed 디렉터리에 저장.

    timeout: 기본 2400s (40분). 파일 크기에 따라 호출 측에서 조정 가능.
    900MB 파일도 ~25분 이내에 처리 가능하도록 여유 확보.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ML_DIR / "preprocess.py"), str(csv_path)]
    try:
        result = subprocess.run(
            cmd, cwd=str(out_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  [전처리 타임아웃] {csv_path.name} ({timeout}s)")
        return False
    except Exception:
        return False


def download_one(year: int, month: int, day: int,
                 stream: bool = False, disk_guard: float = 0) -> dict:
    """단일 파일 다운로드 (+ stream 모드: 즉시 전처리)."""
    import requests

    date_str = f"{year}-{month:02d}-{day:02d}"

    # 디스크 가드: 여유 공간 부족하면 즉시 중단
    if disk_guard > 0 and disk_free_gb() < disk_guard:
        return {"date": date_str, "status": "disk_full",
                "msg": f"여유 {disk_free_gb():.1f}GB < {disk_guard}GB"}

    out_dir  = AIS_DIR / str(year)
    csv_path = out_dir / f"ais-{year}-{month:02d}-{day:02d}.csv"
    zst_path = out_dir / f"ais-{year}-{month:02d}-{day:02d}.csv.zst"

    # 이미 완성된 CSV가 있고 (>100KB) 전처리 완료 안 된 경우 → 전처리만 추가
    # 이미 전처리 완료된 경우 스킵
    pre_csv = PREPROC_ALL / f"ais-{year}-{month:02d}-{day:02d}_preprocessed.csv"
    pre_csv_jan = PREPROC_DIR / f"ais-{year}-{month:02d}-{day:02d}_preprocessed.csv"
    if (pre_csv.exists() and pre_csv.stat().st_size > 0) or \
       (pre_csv_jan.exists() and pre_csv_jan.stat().st_size > 0):
        # 스트리밍 모드면 raw CSV도 정리
        if stream and csv_path.exists():
            try: csv_path.unlink()
            except Exception: pass
        return {"date": date_str, "status": "skip_already_preprocessed"}

    # raw CSV가 이미 있으면 재다운로드 불필요 (stream/non-stream 공통)
    if csv_path.exists() and csv_path.stat().st_size > 100_000:
        size_mb = round(csv_path.stat().st_size / 1e6, 1)
        if stream:
            # 전처리만 수행 (재다운로드 없이)
            # 파일 크기 기반 동적 timeout: 1MB당 3.5초, 최소 2400초
            dyn_timeout = max(2400, int(size_mb * 3.5))
            pre_ok = preprocess_one(csv_path, PREPROC_ALL, timeout=dyn_timeout)
            if pre_ok:
                try: csv_path.unlink()
                except Exception: pass
                return {"date": date_str, "status": "ok_reprocess", "size_mb": size_mb}
            else:
                return {"date": date_str, "status": "preprocess_fail", "size_mb": size_mb}
        else:
            return {"date": date_str, "status": "skip_csv_exists", "size_mb": size_mb}

    url = BASE_URL.format(year=year, month=month, day=day)
    out_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.get(url, timeout=180, stream=True)
            if resp.status_code == 404:
                return {"date": date_str, "status": "not_found"}
            if resp.status_code != 200:
                if attempt < MAX_RETRY:
                    time.sleep(5 * attempt); continue
                return {"date": date_str, "status": f"http_{resp.status_code}"}

            with open(zst_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk: f.write(chunk)

            if not is_valid_zst(zst_path):
                zst_path.unlink(missing_ok=True)
                if attempt < MAX_RETRY:
                    time.sleep(5); continue
                return {"date": date_str, "status": "corrupt"}

            if not decompress_zst(zst_path, csv_path):
                csv_path.unlink(missing_ok=True)
                zst_path.unlink(missing_ok=True)
                if attempt < MAX_RETRY:
                    time.sleep(5); continue
                return {"date": date_str, "status": "decompress_fail"}

            zst_path.unlink(missing_ok=True)
            size_mb = round(csv_path.stat().st_size / 1e6, 1)

            # 스트리밍 모드: 즉시 전처리 + raw 삭제
            if stream:
                # 동적 timeout: 1MB당 3.5초, 최소 2400초 (900MB → ~3135s, 약 52분)
                dyn_timeout = max(2400, int(size_mb * 3.5))
                pre_ok = preprocess_one(csv_path, PREPROC_ALL, timeout=dyn_timeout)
                if pre_ok:
                    try: csv_path.unlink()
                    except Exception: pass
                    return {"date": date_str, "status": "ok_streamed", "size_mb": size_mb}
                else:
                    return {"date": date_str, "status": "preprocess_fail", "size_mb": size_mb}

            return {"date": date_str, "status": "ok", "size_mb": size_mb}

        except requests.exceptions.Timeout:
            if attempt < MAX_RETRY:
                time.sleep(10 * attempt); continue
            return {"date": date_str, "status": "timeout"}
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(5); continue
            return {"date": date_str, "status": f"error:{str(e)[:80]}"}

    return {"date": date_str, "status": "max_retry"}


def build_task_list_random_days(years: list, days_per_year: int,
                                 stream: bool, seed: int = 42,
                                 always_keep_january: bool = True) -> list:
    """
    랜덤 날짜 샘플링 (편향 방지).

    한 달을 통으로 자르지 않고, 연중 365일에서 days_per_year 개의 날짜만 균등 랜덤 추출.
    1월은 이미 전처리됐다고 가정하여 기본으로 보존 (always_keep_january=True).

    Args:
        years: 다운로드 연도
        days_per_year: 각 연도에서 유지할 총 일 수 (1월 포함)
        always_keep_january: 1월 31일 전부 보존 여부 (이미 전처리되어 있음)
    """
    rng = random.Random(seed)
    tasks = []
    total_selected = 0

    for year in years:
        # 연도 전체 날짜 생성
        all_days = []
        for month in range(1, 13):
            _, days_in_month = calendar.monthrange(year, month)
            for day in range(1, days_in_month + 1):
                all_days.append((month, day))

        if always_keep_january:
            jan_days  = [d for d in all_days if d[0] == 1]
            rest_days = [d for d in all_days if d[0] != 1]
            need_rest = max(0, days_per_year - len(jan_days))
            rng.shuffle(rest_days)
            selected_days = jan_days + rest_days[:need_rest]
        else:
            shuffled = list(all_days)
            rng.shuffle(shuffled)
            selected_days = shuffled[:days_per_year]

        selected_days.sort()
        total_selected += len(selected_days)

        # 월 분포 통계 (편향 검증용 출력)
        month_dist = {m: 0 for m in range(1, 13)}
        for m, _ in selected_days:
            month_dist[m] += 1
        dist_str = " ".join(f"{m}월:{c}" for m, c in month_dist.items() if c > 0)
        print(f"  {year}: {len(selected_days)}일 선택  [{dist_str}]")

        for month, day in selected_days:
            # 이미 전처리 완료된 파일 제외
            pre_csv = PREPROC_ALL / f"ais-{year}-{month:02d}-{day:02d}_preprocessed.csv"
            pre_csv_jan = PREPROC_DIR / f"ais-{year}-{month:02d}-{day:02d}_preprocessed.csv"
            if (pre_csv.exists() and pre_csv.stat().st_size > 0) or \
               (pre_csv_jan.exists() and pre_csv_jan.stat().st_size > 0):
                continue
            csv_path = AIS_DIR / str(year) / f"ais-{year}-{month:02d}-{day:02d}.csv"
            if not stream and csv_path.exists() and csv_path.stat().st_size > 100_000:
                continue
            tasks.append((year, month, day))

    print(f"\n  총 선택: {total_selected}일 / 다운로드 필요: {len(tasks)}일")
    return tasks


def build_task_list(years: list, drop_months: int, stream: bool, seed: int = 42) -> list:
    """[레거시] 월 단위 드롭. build_task_list_random_days로 대체됨."""
    rng = random.Random(seed)
    tasks = []
    for year in years:
        candidate_months = list(range(2, 13))
        n_keep = max(0, len(candidate_months) - drop_months)
        kept = sorted(rng.sample(candidate_months, n_keep))
        chosen_months = [1] + kept
        print(f"  {year}: {len(chosen_months)}개월 선택 → {chosen_months}")
        for month in chosen_months:
            _, days_in_month = calendar.monthrange(year, month)
            for day in range(1, days_in_month + 1):
                pre_csv = PREPROC_ALL / f"ais-{year}-{month:02d}-{day:02d}_preprocessed.csv"
                pre_csv_jan = PREPROC_DIR / f"ais-{year}-{month:02d}-{day:02d}_preprocessed.csv"
                if (pre_csv.exists() and pre_csv.stat().st_size > 0) or \
                   (pre_csv_jan.exists() and pre_csv_jan.stat().st_size > 0):
                    continue
                csv_path = AIS_DIR / str(year) / f"ais-{year}-{month:02d}-{day:02d}.csv"
                if not stream and csv_path.exists() and csv_path.stat().st_size > 100_000:
                    continue
                tasks.append((year, month, day))
    return tasks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years",       type=int, nargs="+", default=list(range(2016, 2026)),
                        help="다운로드 연도 (기본: 2016-2025)")
    parser.add_argument("--days-per-year", type=int, default=180,
                        help="각 연도에서 랜덤 추출할 총 일 수 (1월 31일 포함, 기본: 180)")
    parser.add_argument("--drop-months", type=int, default=None,
                        help="[레거시] 월 단위 드롭. 지정 시 월 통째 제거 모드로 전환")
    parser.add_argument("--workers",     type=int, default=4)
    parser.add_argument("--stream",      action="store_true",
                        help="스트리밍 모드: 다운로드→전처리→raw 즉시 삭제")
    parser.add_argument("--disk-guard-gb", type=float, default=DEFAULT_DISK_GUARD_GB,
                        help=f"D드라이브 여유 GB 미만이면 즉시 중단 (기본: {DEFAULT_DISK_GUARD_GB})")
    parser.add_argument("--seed",        type=int, default=42, help="랜덤 시드")
    args = parser.parse_args()

    print(f"\n[전체 월 AIS 다운로드 v3 — 랜덤 일자 샘플링]")
    print(f"  연도: {args.years[0]}~{args.years[-1]}  ({len(args.years)}개)")
    if args.drop_months is not None:
        print(f"  [레거시 모드] 월 단위 드롭: -{args.drop_months}개월/년")
    else:
        print(f"  랜덤 일자 샘플링: {args.days_per_year}일/년 (1월 31일 포함, 편향 방지)")
    print(f"  스트리밍 모드: {'ON' if args.stream else 'OFF'}")
    print(f"  디스크 가드: {args.disk_guard_gb}GB")
    print(f"  D드라이브 현재 여유: {disk_free_gb():.1f}GB\n")

    if args.drop_months is not None:
        tasks = build_task_list(args.years, args.drop_months, args.stream, args.seed)
    else:
        tasks = build_task_list_random_days(args.years, args.days_per_year,
                                             args.stream, args.seed)
    total = len(tasks)
    print(f"\n  다운로드 필요: {total}개 파일\n")

    notify(
        f"전체 월 다운로드 시작!\n"
        f"연도: {args.years[0]}~{args.years[-1]}\n"
        f"연도별 -{args.drop_months}월 (랜덤)\n"
        f"필요: {total}개 | 스트리밍 {'ON' if args.stream else 'OFF'}\n"
        f"디스크 여유: {disk_free_gb():.0f}GB",
        "JB | 다운로드 시작"
    )

    if not tasks:
        print("모든 파일 이미 완료!")
        return

    ok = stream_ok = skip = not_found = error = disk_stop = 0
    t_start = time.time()
    done = 0
    log_path = Path(r"D:\JB-Pirate-King-ML-Results\download_allmonths.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, y, m, d,
                               stream=args.stream,
                               disk_guard=args.disk_guard_gb): (y, m, d)
                   for y, m, d in tasks}
        for fut in as_completed(futures):
            r = fut.result()
            done += 1
            status = r.get("status", "?")

            if status == "ok":            ok += 1; tag = "O"
            elif status == "ok_streamed": stream_ok += 1; tag = "S"
            elif "skip" in status:        skip += 1; tag = "-"
            elif status == "not_found":   not_found += 1; tag = "."
            elif status == "disk_full":   disk_stop += 1; tag = "!"
            else:                          error += 1; tag = "X"

            # 디스크 가드 트리거 시 중단
            if status == "disk_full":
                print(f"\n[디스크 가드] {r.get('msg','')} - 다운로드 즉시 중단")
                notify(f"디스크 여유 부족! {r.get('msg','')} 다운로드 중단", "JB | 디스크 가드")
                pool.shutdown(wait=False, cancel_futures=True)
                break

            elapsed = time.time() - t_start
            per_file = elapsed / done if done else 1
            eta_min  = (total - done) * per_file / 60
            pct = done / total * 100
            bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))

            disk_now = disk_free_gb()
            print(f"\r{tag} [{bar}] {done}/{total} ({pct:.0f}%) "
                  f"ETA {eta_min:.0f}min "
                  f"ok={ok}+{stream_ok} 404={not_found} err={error} "
                  f"D={disk_now:.0f}GB",
                  end="", flush=True)

            try:
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"{r.get('date','')}  {status}  {r.get('size_mb','')}\n")
            except Exception:
                pass

            # 20% 단위 카드 알림
            if done % max(1, total // 5) == 0:
                try:
                    from notify import send_status_card
                    send_status_card(
                        title="JB | 전체월 다운로드 진행",
                        stage=f"다운로드 {pct:.0f}%",
                        progress_pct=int(pct),
                        eta_str=f"{eta_min:.0f}min",
                        elapsed_str=f"{elapsed/60:.0f}min",
                        steps=[
                            ("✅" if pct >= 100 else "🔄", "다운로드 + 압축해제",
                             f"{ok+stream_ok}/{total}"),
                            ("📥", "스트리밍 전처리" if args.stream else "raw CSV 보관",
                             f"streamed={stream_ok}" if args.stream else f"raw={ok}"),
                        ],
                        resources={
                            "D 여유": f"{disk_now:.0f}GB",
                            "오류": str(error),
                            "404": str(not_found),
                        },
                        notes=f"연도별 -{args.drop_months}월 랜덤 제외 / 워커 {args.workers}",
                    )
                except Exception:
                    pass

    elapsed_min = (time.time() - t_start) / 60
    print(f"\n\n완료: ok={ok}  스트림={stream_ok}  스킵={skip}  404={not_found}  오류={error}  디스크중단={disk_stop}")
    print(f"소요: {elapsed_min:.1f}분  |  D 여유: {disk_free_gb():.1f}GB")

    try:
        from notify import send_status_card
        send_status_card(
            title="JB | 다운로드 완료",
            stage="다운로드 종료",
            progress_pct=100 if error == 0 and disk_stop == 0 else 95,
            eta_str="-",
            elapsed_str=f"{elapsed_min:.1f}min",
            steps=[
                ("✅", "다운로드", f"ok={ok}"),
                ("✅" if stream_ok > 0 else "⏭️", "스트리밍 전처리", f"{stream_ok}"),
                ("⚠️" if error > 0 else "✅", "오류 처리", f"err={error} 404={not_found}"),
            ],
            resources={
                "D 여유": f"{disk_free_gb():.0f}GB",
                "총 처리": f"{ok + stream_ok + skip}",
            },
            notes=f"디스크가드={disk_stop > 0}  연도-{args.drop_months}월",
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
