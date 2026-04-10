#!/usr/bin/env python3
"""
OpenCPN IDS Signal Generator  v4
Generated AIS patterns + normal NMEA file replay + CSV decoded replay GUI.

v4 변경:
  - CSV 디코딩 데이터 송신 채널 추가 (별도 패널)
  - 미사용 패턴/UI 섹션 제거 (JBU, Pincer, Wave)
  - 생성 신호 주기 스핀박스를 네트워크 섹션에서 생성 신호 패널로 이동
"""

from __future__ import annotations

import csv
import io
import math
import queue
import random
import socket
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


ATTACK_OPTIONS = [
    ("speed_spike",      "속도 이상"),
    ("anchor_move",      "정박 이동 이상"),
    ("course_mismatch",  "COG/HDG 불일치"),
    ("position_jump",    "위치 점프 이상"),
]
ATTACK_LABEL_TO_KEY = {label: key for key, label in ATTACK_OPTIONS}

DEFAULT_SAMPLE_FILE = Path(__file__).with_name("nmea_data_sample.txt")

log_queue: "queue.Queue[dict[str, str]]" = queue.Queue()
stop_event = threading.Event()

# ──────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────

def queue_log(log_q, message: str, level: str = "info") -> None:
    log_q.put({"kind": "log", "message": message, "level": level})


def queue_channel_state(log_q, channel: str, state: str) -> None:
    log_q.put({"kind": "channel_state", "channel": channel, "state": state})


def sleep_with_event(stop_signal: threading.Event, seconds: float) -> bool:
    end_time = time.time() + max(0.0, seconds)
    while not stop_signal.is_set():
        remaining = end_time - time.time()
        if remaining <= 0:
            return True
        time.sleep(min(0.1, remaining))
    return False


def nmea_checksum(sentence: str) -> str:
    checksum = 0
    for ch in sentence:
        checksum ^= ord(ch)
    return f"{checksum:02X}"


def encode_payload(bits: list[int]) -> str:
    while len(bits) % 6:
        bits.append(0)
    payload = []
    for i in range(0, len(bits), 6):
        value = 0
        for bit in bits[i:i + 6]:
            value = (value << 1) | bit
        char_code = value + 48
        if char_code > 87:
            char_code += 8
        payload.append(chr(char_code))
    return "".join(payload)


def build_vdm(mmsi, lat, lon, sog, cog, heading, nav_status=0) -> str:
    bits: list[int] = []

    def push(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(1, 6)
    push(0, 2)
    push(mmsi, 30)
    push(nav_status, 4)
    push(0, 8)
    push(int(round(sog * 10)) & 0x3FF, 10)
    push(1, 1)
    push(int(round(lon * 600000)) & 0xFFFFFFF, 28)
    push(int(round(lat * 600000)) & 0x7FFFFFF, 27)
    push(int(round(cog * 10)) & 0xFFF, 12)
    push(heading % 360, 9)
    push(int(time.time()) % 60, 6)
    push(0, 2)
    push(0, 3)
    push(0, 1)
    push(0, 19)

    payload = encode_payload(bits)
    sentence_body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{sentence_body}*{nmea_checksum(sentence_body)}\r\n"


def build_vsd(mmsi: int, vessel_name: str) -> str:
    name = vessel_name[:20].upper().ljust(20, "@")
    bits: list[int] = []

    def push(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    def push_str(value: str, width: int) -> None:
        for ch in value[:width]:
            code = ord(ch)
            if code >= 64:
                code -= 64
            push(code, 6)

    push(24, 6)
    push(0, 2)
    push(mmsi, 30)
    push(0, 2)
    push_str(name, 20)
    push(0, 8)

    payload = encode_payload(bits)
    sentence_body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{sentence_body}*{nmea_checksum(sentence_body)}\r\n"


def load_nmea(file_path) -> list[str]:
    path = Path(file_path)
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        messages = [
            line.strip() + "\r\n"
            for line in handle
            if line.strip().startswith("!AIVDM")
        ]
    if not messages:
        raise ValueError("파일에서 !AIVDM 문장을 찾지 못했습니다.")
    return messages


# ──────────────────────────────────────────────────
# CSV 디코딩 데이터 파싱 / 인코딩
# ──────────────────────────────────────────────────

CSV_REQUIRED = {"mmsi", "latitude", "longitude", "sog", "cog", "heading"}

def load_csv_decoded(file_path: str) -> list[dict]:
    """
    디코딩된 AIS 데이터 파일을 읽어 행 목록으로 반환.
    - 확장자 무관 (txt / csv / tsv / 확장자 없음 모두 허용)
    - 구분자 자동 감지 (쉼표 / 탭 / 세미콜론)
    - 헤더 대소문자 무관, 앞뒤 공백 제거
    """
    path = Path(file_path)
    raw_text = path.read_text(encoding="utf-8-sig", errors="ignore")

    # 구분자 자동 감지: 첫 줄에서 후보 중 가장 많이 등장하는 것 선택
    first_line = raw_text.splitlines()[0] if raw_text.strip() else ""
    delimiter = ","
    for cand in (",", "\t", ";"):
        if raw_text.count(cand) > raw_text.count(delimiter):
            delimiter = cand

    reader = csv.DictReader(io.StringIO(raw_text), delimiter=delimiter)
    rows = []
    for raw in reader:
        row = {k.strip().lower(): v.strip() for k, v in raw.items() if k is not None}
        rows.append(row)

    if not rows:
        raise ValueError("파일에 데이터 행이 없습니다.")
    missing = CSV_REQUIRED - set(rows[0].keys())
    if missing:
        raise ValueError(
            f"필수 컬럼 누락: {', '.join(sorted(missing))}\n"
            f"감지된 컬럼: {', '.join(rows[0].keys())}"
        )
    return rows


def csv_row_to_nmea(row: dict) -> list[str]:
    """
    CSV 한 행 → AIVDM NMEA 문장 목록 (위치 메시지 + 필요 시 이름 메시지).
    """
    def _int(v, default=0):
        try:
            return int(float(v)) if v not in ("", None) else default
        except Exception:
            return default

    def _float(v, default=0.0):
        try:
            return float(v) if v not in ("", None) else default
        except Exception:
            return default

    mmsi = _int(row.get("mmsi", "0"))
    lat  = _float(row.get("latitude",  "0.0"))
    lon  = _float(row.get("longitude", "0.0"))
    sog  = _float(row.get("sog",  "0.0"))
    cog  = _float(row.get("cog",  "0.0"))
    hdg  = _int(row.get("heading", "0"))
    nav  = _int(row.get("status", "0"))

    messages = []
    vessel_name = row.get("vessel_name", "").strip()
    if vessel_name:
        messages.append(build_vsd(mmsi, vessel_name))
    messages.append(build_vdm(mmsi, lat, lon, sog, cog, hdg, nav))
    return messages


# ──────────────────────────────────────────────────
# 선단 이동(Translation) 헬퍼
# ──────────────────────────────────────────────────

_KN_TO_DEG_PER_SEC = 1.0 / 3600.0 * 1852.0 / 111320.0


def translation_offset(cfg: dict, elapsed: float) -> tuple[float, float]:
    move_speed   = float(cfg.get("move_speed",   0.0))
    move_heading = float(cfg.get("move_heading", 0.0))
    move_accel   = float(cfg.get("move_accel",   0.0))
    effective_speed = move_speed + move_accel * (elapsed / 60.0)
    rad = math.radians(move_heading)
    speed_dps = effective_speed * _KN_TO_DEG_PER_SEC
    dlat = math.cos(rad) * speed_dps * elapsed
    dlon = math.sin(rad) * speed_dps * elapsed * 1.2
    return dlat, dlon


# ──────────────────────────────────────────────────
# Vessel
# ──────────────────────────────────────────────────

class Vessel:
    def __init__(self, mmsi: int, name: str, nav_status: int = 0) -> None:
        self.mmsi = mmsi
        self.name = name
        self.nav_status = nav_status
        self.lat = 0.0
        self.lon = 0.0
        self.sog = 0.0
        self.cog = 0.0
        self.heading = 0

    def position_message(self) -> str:
        return build_vdm(
            self.mmsi, self.lat, self.lon,
            self.sog, self.cog, self.heading, self.nav_status,
        )

    def name_message(self) -> str:
        return build_vsd(self.mmsi, self.name)


# ──────────────────────────────────────────────────
# 속도 이상
# ──────────────────────────────────────────────────

def make_speed_spike_fleet(cfg):
    center_lat = float(cfg["center_lat"])
    center_lon = float(cfg["center_lon"])
    count = int(cfg["speed_count"])
    base_speed = float(cfg["speed_base"])
    spike_speed = float(cfg["speed_spike"])
    mode = str(cfg.get("speed_mode", "intermittent"))
    interval = max(1.0, float(cfg.get("speed_interval", 10.0)))

    fleet = []
    for index in range(count):
        vessel = Vessel(990100000 + index, f"GHOST-S{index + 1:03d}")
        vessel.lat = center_lat + random.uniform(-0.03, 0.03)
        vessel.lon = center_lon + random.uniform(-0.03 * 1.2, 0.03 * 1.2)
        vessel.sog = base_speed
        vessel.cog = random.uniform(0, 360)
        vessel.heading = int(vessel.cog)
        vessel.nav_status = 0
        vessel._base_speed = base_speed
        vessel._spike_speed = spike_speed
        vessel._speed_mode = mode
        vessel._spike_interval = interval
        vessel._last_spike = 0.0
        vessel._spike_state = False
        fleet.append(vessel)
    return fleet


def update_speed_spike_fleet(fleet, elapsed: float, dt: float, cfg: dict) -> None:
    for vessel in fleet:
        if not hasattr(vessel, "_spike_interval"):
            continue
        if elapsed - vessel._last_spike >= vessel._spike_interval:
            vessel._last_spike = elapsed
            if vessel._speed_mode == "간헐":
                vessel._spike_state = not vessel._spike_state
            else:
                vessel._spike_state = True

        if vessel._spike_state:
            vessel.sog = vessel._spike_speed
            if vessel._speed_mode == "순간":
                vessel._spike_state = False
        else:
            vessel.sog = vessel._base_speed

        if random.random() < 0.2:
            vessel.cog = (vessel.cog + random.uniform(-30, 30)) % 360

        step = vessel.sog * _KN_TO_DEG_PER_SEC * dt
        vessel.lat += math.cos(math.radians(vessel.cog)) * step
        vessel.lon += math.sin(math.radians(vessel.cog)) * step
        vessel.heading = int(vessel.cog)


# ──────────────────────────────────────────────────
# 정박 이동 이상
# ──────────────────────────────────────────────────

def make_anchor_move_fleet(cfg):
    center_lat = float(cfg["center_lat"])
    center_lon = float(cfg["center_lon"])
    count = int(cfg["anchor_count"])
    radius = float(cfg["anchor_radius"])
    speed = float(cfg["anchor_speed"])
    cog = float(cfg.get("anchor_cog", 90.0))
    drift = float(cfg.get("anchor_drift", 0.0))
    lon_offset = float(cfg.get("anchor_lon_offset", 0.0))

    fleet = []
    for index in range(count):
        vessel = Vessel(990500000 + index, f"GHOST-A{index + 1:03d}", nav_status=1)
        vessel.lat = center_lat + random.uniform(-radius, radius)
        vessel.lon = center_lon + random.uniform(-radius * 1.2, radius * 1.2) + lon_offset
        vessel.sog = max(0.2, speed)
        vessel.cog = (cog + random.uniform(-30, 30)) % 360
        vessel.heading = int((vessel.cog + 120) % 360)
        vessel._drift = drift
        fleet.append(vessel)
    return fleet


def update_anchor_move_fleet(fleet, elapsed: float, dt: float, cfg: dict) -> None:
    for vessel in fleet:
        if not hasattr(vessel, "_drift"):
            continue
        step = vessel.sog * _KN_TO_DEG_PER_SEC * dt
        vessel.lat += math.cos(math.radians(vessel.cog)) * step
        vessel.lon += math.sin(math.radians(vessel.cog)) * step
        vessel.lat += vessel._drift * dt * 0.00001
        vessel.lon += vessel._drift * dt * 0.00001
        vessel.heading = int((vessel.cog + 120) % 360)


# ──────────────────────────────────────────────────
# COG/HDG 불일치
# ──────────────────────────────────────────────────

def make_course_mismatch_fleet(cfg):
    center_lat = float(cfg["center_lat"])
    center_lon = float(cfg["center_lon"])
    count = int(cfg["course_count"])
    mismatch = float(cfg["course_mismatch"])
    speed = float(cfg["course_speed"])
    drift = float(cfg.get("course_drift", 5.0))
    offset = float(cfg.get("course_offset", 120.0))

    fleet = []
    for index in range(count):
        vessel = Vessel(990600000 + index, f"GHOST-CM{index + 1:03d}")
        vessel.lat = center_lat + random.uniform(-0.05, 0.05)
        vessel.lon = center_lon + random.uniform(-0.05 * 1.2, 0.05 * 1.2)
        vessel.sog = max(0.5, speed)
        vessel.cog = random.uniform(0, 360)
        vessel.heading = int((vessel.cog + mismatch + offset) % 360)
        vessel._drift = drift
        fleet.append(vessel)
    return fleet


def update_course_mismatch_fleet(fleet, elapsed: float, dt: float, cfg: dict) -> None:
    for vessel in fleet:
        if not hasattr(vessel, "_drift"):
            continue
        if random.random() < 0.15:
            vessel.cog = (vessel.cog + random.uniform(-vessel._drift, vessel._drift)) % 360
        step = vessel.sog * _KN_TO_DEG_PER_SEC * dt
        vessel.lat += math.cos(math.radians(vessel.cog)) * step
        vessel.lon += math.sin(math.radians(vessel.cog)) * step
        vessel.heading = int(
            (vessel.cog + float(cfg.get("course_mismatch", 150.0)) + float(cfg.get("course_offset", 120.0))) % 360
        )


# ──────────────────────────────────────────────────
# 위치 점프 이상
# ──────────────────────────────────────────────────

def make_position_jump_fleet(cfg):
    center_lat = float(cfg.get("jump_center_lat", cfg["center_lat"]))
    center_lon = float(cfg.get("jump_center_lon", cfg["center_lon"]))
    count = int(cfg["jump_count"])
    radius = float(cfg["jump_radius"])
    interval = max(1.0, float(cfg.get("jump_interval", 10.0)))

    fleet = []
    for index in range(count):
        vessel = Vessel(990700000 + index, f"GHOST-P{index + 1:03d}")
        vessel.lat = center_lat + random.uniform(-radius, radius)
        vessel.lon = center_lon + random.uniform(-radius * 1.2, radius * 1.2)
        vessel.sog = random.uniform(2.0, 10.0)
        vessel.cog = random.uniform(0, 360)
        vessel.heading = int(vessel.cog)
        vessel._jump_radius = radius
        vessel._jump_interval = interval
        vessel._last_jump = 0.0
        fleet.append(vessel)
    return fleet


def update_position_jump_fleet(fleet, elapsed: float, dt: float, cfg: dict) -> None:
    for vessel in fleet:
        if not hasattr(vessel, "_jump_interval"):
            continue
        if elapsed - vessel._last_jump >= vessel._jump_interval:
            vessel._last_jump = elapsed
            vessel.lat += random.choice([-1, 1]) * random.uniform(0.08, 0.20)
            vessel.lon += random.choice([-1, 1]) * random.uniform(0.08, 0.20)
            vessel.cog = random.uniform(0, 360)
            vessel.heading = int(vessel.cog)

        step = vessel.sog * _KN_TO_DEG_PER_SEC * dt
        vessel.lat += math.cos(math.radians(vessel.cog)) * step
        vessel.lon += math.sin(math.radians(vessel.cog)) * step
        vessel.heading = int(vessel.cog)


# ──────────────────────────────────────────────────
# 앵커 선박
# ──────────────────────────────────────────────────

def make_anchor_vessel(cfg) -> Vessel:
    anchor = Vessel(440123456, "BUSAN ANCHOR", nav_status=1)
    anchor.lat = float(cfg["center_lat"])
    anchor.lon = float(cfg["center_lon"])
    anchor.sog = 0.0
    anchor.cog = 0.0
    anchor.heading = 45
    return anchor


# ──────────────────────────────────────────────────
# 선단 빌드 / 업데이트 디스패처
# ──────────────────────────────────────────────────

def build_generated_fleet(cfg) -> list[Vessel]:
    attack_key = str(cfg["attack_key"])
    builders = {
        "speed_spike":     make_speed_spike_fleet,
        "anchor_move":     make_anchor_move_fleet,
        "course_mismatch": make_course_mismatch_fleet,
        "position_jump":   make_position_jump_fleet,
    }
    if attack_key not in builders:
        raise ValueError(f"지원하지 않는 패턴입니다: {attack_key}")
    fleet = builders[attack_key](cfg)
    if cfg.get("add_anchor"):
        fleet.append(make_anchor_vessel(cfg))
    return fleet


def update_generated_fleet(fleet, attack_key: str, tick: float, interval: float, cfg: dict) -> None:
    if attack_key == "speed_spike":
        update_speed_spike_fleet(fleet, tick, interval, cfg)
    elif attack_key == "anchor_move":
        update_anchor_move_fleet(fleet, tick, interval, cfg)
    elif attack_key == "course_mismatch":
        update_course_mismatch_fleet(fleet, tick, interval, cfg)
    elif attack_key == "position_jump":
        update_position_jump_fleet(fleet, tick, interval, cfg)


# ──────────────────────────────────────────────────
# 송신 루프
# ──────────────────────────────────────────────────

def send_generated_loop(cfg, log_q, stop_signal: threading.Event) -> bool:
    host = str(cfg["host"])
    port = int(cfg["port"])
    interval = float(cfg["interval"])
    attack_key = str(cfg["attack_key"])
    attack_label = str(cfg["attack_label"])

    fleet = build_generated_fleet(cfg)
    name_sent: set[int] = set()
    iteration = 0
    start_time = time.time()

    move_speed   = float(cfg.get("move_speed",   0.0))
    move_heading = float(cfg.get("move_heading", 0.0))
    queue_log(log_q, f"[생성 시작] 패턴: {attack_label} | 선박 {len(fleet)}척", "start")
    queue_log(log_q, f"[생성 전송] {host}:{port} | 주기 {interval:.2f}s", "info")
    if move_speed > 0:
        queue_log(log_q, f"[이동] 속도 {move_speed:.1f}kn | 방향 {move_heading:.0f}°", "info")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not stop_signal.is_set():
            iteration += 1
            cycle_start = time.time()
            elapsed = cycle_start - start_time
            tick = elapsed

            update_generated_fleet(fleet, attack_key, tick, interval, cfg)

            sent = 0
            for vessel in fleet:
                if stop_signal.is_set():
                    return False
                if vessel.mmsi not in name_sent:
                    sock.sendto(vessel.name_message().encode("ascii"), (host, port))
                    name_sent.add(vessel.mmsi)
                    if not sleep_with_event(stop_signal, 0.01):
                        return False
                sock.sendto(vessel.position_message().encode("ascii"), (host, port))
                sent += 1
                if not sleep_with_event(stop_signal, 0.005):
                    return False

            elapsed2 = time.time() - cycle_start
            if iteration == 1 or iteration % 5 == 0:
                dlat, dlon = translation_offset(cfg, tick)
                queue_log(
                    log_q,
                    f"[생성] {iteration}회차 | {sent}건 | {elapsed2:.2f}s"
                    f" | 오프셋 Δlat={dlat:.4f} Δlon={dlon:.4f}",
                    "info",
                )
            if not sleep_with_event(stop_signal, max(0.0, interval - elapsed2)):
                return False
    return False


def send_file_loop(cfg, log_q, stop_signal: threading.Event) -> bool:
    host = str(cfg["host"])
    port = int(cfg["port"])
    file_path = Path(str(cfg["file_path"]))
    interval = float(cfg["file_interval"])
    repeat = bool(cfg["file_repeat"])
    messages = load_nmea(file_path)

    queue_log(log_q, f"[파일 시작] {file_path.name} | AIVDM {len(messages)}개", "start")
    queue_log(log_q, f"[파일 전송] {host}:{port} | 간격 {interval:.2f}s | 반복 {'켜짐' if repeat else '꺼짐'}", "info")

    sent_count = 0
    cycle = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not stop_signal.is_set():
            cycle += 1
            for index, message in enumerate(messages, start=1):
                if stop_signal.is_set():
                    return False
                sock.sendto(message.encode("ascii"), (host, port))
                sent_count += 1
                queue_log(log_q, f"[파일 {index:04d}] {message.strip()}", "info")
                if not sleep_with_event(stop_signal, interval):
                    return False
            if not repeat:
                queue_log(log_q, f"[파일 완료] 1회 송신 완료 | 총 {sent_count}건", "start")
                return True
            queue_log(log_q, f"[파일 반복] {cycle}회차 완료 | 누적 {sent_count}건", "info")
    return False


def send_csv_loop(cfg, log_q, stop_signal: threading.Event) -> bool:
    """
    CSV 디코딩 데이터를 읽어 AIVDM NMEA 문장으로 변환 후 UDP 송신.
    타임스탬프 컬럼(base_date_time)이 있으면 행 간 실제 시간 간격을 재현하고,
    없으면 고정 간격(csv_interval)을 사용한다.
    """
    host = str(cfg["host"])
    port = int(cfg["port"])
    file_path = Path(str(cfg["csv_file_path"]))
    fixed_interval = float(cfg["csv_interval"])
    repeat = bool(cfg["csv_repeat"])
    use_timestamp = bool(cfg.get("csv_use_timestamp", False))

    rows = load_csv_decoded(str(file_path))

    # 타임스탬프가 있을 경우 시간 순서대로 정렬
    def parse_ts(row: dict):
        for key in ("base_date_time", "timestamp", "datetime", "time"):
            val = row.get(key, "").strip()
            if val:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                            "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                    try:
                        import datetime
                        return datetime.datetime.strptime(val, fmt)
                    except ValueError:
                        pass
        return None

    if use_timestamp:
        rows_with_ts = [(parse_ts(row), idx, row) for idx, row in enumerate(rows)]
        if any(ts is not None for ts, _, _ in rows_with_ts):
            rows_with_ts.sort(key=lambda item: (item[0] is None, item[0] or __import__("datetime").datetime.max, item[1]))
            rows = [row for _, _, row in rows_with_ts]
        else:
            use_timestamp = False

    queue_log(log_q, f"[CSV 시작] {file_path.name} | 행 {len(rows)}개", "start")
    queue_log(log_q, f"[CSV 전송] {host}:{port} | "
              f"{'타임스탬프 재현' if use_timestamp else f'고정 {fixed_interval:.2f}s'} "
              f"| 반복 {'켜짐' if repeat else '꺼짐'}", "info")

    sent_count = 0
    cycle = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not stop_signal.is_set():
            cycle += 1
            prev_ts = None

            for idx, row in enumerate(rows):
                if stop_signal.is_set():
                    return False

                # 대기 시간 결정
                if use_timestamp:
                    cur_ts = parse_ts(row)
                    if cur_ts is not None and prev_ts is not None:
                        delta = (cur_ts - prev_ts).total_seconds()
                        wait = max(0.0, min(delta, 60.0))  # 최대 60초 캡
                    else:
                        wait = fixed_interval
                    prev_ts = cur_ts if cur_ts is not None else prev_ts
                else:
                    wait = fixed_interval

                if not sleep_with_event(stop_signal, wait):
                    return False

                try:
                    nmea_list = csv_row_to_nmea(row)
                except Exception as exc:
                    queue_log(log_q, f"[CSV 오류] 행 {idx + 1}: {exc}", "error")
                    continue

                for nmea in nmea_list:
                    sock.sendto(nmea.encode("ascii"), (host, port))

                sent_count += len(nmea_list)
                mmsi = row.get("mmsi", "?")
                name = row.get("vessel_name", "")
                queue_log(
                    log_q,
                    f"[CSV {idx + 1:04d}/{len(rows)}] MMSI={mmsi}"
                    + (f" ({name})" if name else "")
                    + f" | 패킷 누적 {sent_count}건",
                    "info",
                )

            if not repeat:
                queue_log(log_q, f"[CSV 완료] 1회 송신 완료 | 총 {sent_count}건", "start")
                return True
            queue_log(log_q, f"[CSV 반복] {cycle}회차 완료 | 누적 {sent_count}건", "info")
    return False


def sender_worker(channel: str, cfg, log_q, stop_signal: threading.Event) -> None:
    completed = False
    try:
        if channel == "generated":
            completed = send_generated_loop(cfg, log_q, stop_signal)
        elif channel == "file":
            completed = send_file_loop(cfg, log_q, stop_signal)
        elif channel == "csv":
            completed = send_csv_loop(cfg, log_q, stop_signal)
    except Exception as exc:
        labels = {"generated": "생성", "file": "파일", "csv": "CSV"}
        queue_log(log_q, f"[{labels.get(channel, channel)} 오류] {exc}", "error")
    finally:
        labels = {"generated": "생성", "file": "파일", "csv": "CSV"}
        label = labels.get(channel, channel)
        if stop_signal.is_set():
            queue_log(log_q, f"[{label} 종료] 사용자 중단", "start")
        elif completed:
            queue_log(log_q, f"[{label} 종료] 송신 완료", "start")
        else:
            queue_log(log_q, f"[{label} 종료] 스레드 종료", "start")
        queue_channel_state(log_q, channel, "finished")


# ──────────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OpenCPN IDS Signal Generator  v4")
        self.configure(bg="#09111d")
        self.minsize(800, 560)
        self.resizable(True, True)

        self.generated_thread: threading.Thread | None = None
        self.file_thread:      threading.Thread | None = None
        self.csv_thread:       threading.Thread | None = None

        self.generated_stop_event = threading.Event()
        self.file_stop_event      = threading.Event()
        self.csv_stop_event       = threading.Event()

        self._setup_styles()
        self._build_ui()
        self._set_channel_state("generated", False)
        self._set_channel_state("file",      False)
        self._set_channel_state("csv",       False)
        self._on_attack_change()
        self._poll_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 스타일 ─────────────────────────────────────
    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        bg, accent, fg = "#09111d", "#35d0ff", "#edf4ff"
        fg_sub, entry_bg, highlight = "#9db0c7", "#172334", "#24354d"

        style.configure(".", background=bg, foreground=fg, font=("Consolas", 10))
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg, font=("Consolas", 10))
        style.configure("Header.TLabel", background=bg, foreground=accent, font=("Consolas", 13, "bold"))
        style.configure("Sub.TLabel", background=bg, foreground=fg_sub, font=("Consolas", 9))
        style.configure("Accent.TLabel", background=bg, foreground="#ffcc44", font=("Consolas", 10, "bold"))
        style.configure("TEntry", fieldbackground=entry_bg, foreground="#ffffff", insertcolor=accent, borderwidth=0)
        style.configure("TSpinbox", fieldbackground=entry_bg, foreground="#ffffff", background=entry_bg,
                         arrowcolor=accent, borderwidth=0)
        style.configure("TCombobox", fieldbackground=entry_bg, foreground="#ffffff",
                         selectbackground=accent, selectforeground=bg)
        style.map("TCombobox", fieldbackground=[("readonly", entry_bg)], foreground=[("readonly", "#ffffff")])
        style.configure("TCheckbutton", background=bg, foreground=fg, font=("Consolas", 10))
        style.map("TCheckbutton", background=[("active", bg)])
        style.configure("TSeparator", background=highlight)

    # ── 공통 빌더 ───────────────────────────────────
    def _section(self, parent, title: str, color: str = None) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", padx=10, pady=(10, 2))
        style = "Accent.TLabel" if color else "Header.TLabel"
        ttk.Label(frame, text=title, style=style).pack(anchor="w")
        tk.Frame(parent, height=1, bg="#24354d").pack(fill="x", padx=10, pady=(0, 6))

    def _row(self, parent, label: str, widget_factory, **kwargs):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=16, pady=2)
        ttk.Label(row, text=label, width=26, anchor="w", style="Sub.TLabel").pack(side="left")
        widget = widget_factory(row, **kwargs)
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def _entry(self, parent, default: str = "", **kwargs):
        var = tk.StringVar(value=str(default))
        entry = ttk.Entry(parent, textvariable=var, **kwargs)
        entry._var = var
        return entry

    def _spin(self, parent, from_: float, to: float, default: float, step: float = 1.0):
        var = tk.DoubleVar(value=default)
        spin = ttk.Spinbox(parent, from_=from_, to=to, increment=step, textvariable=var,
                            font=("Consolas", 10))
        spin._var = var
        return spin

    def _combo(self, parent, values: list[str], default: str):
        var = tk.StringVar(value=default)
        combo = ttk.Combobox(parent, textvariable=var, values=values, state="readonly",
                              font=("Consolas", 10))
        combo._var = var
        return combo

    # ── UI 빌드 ─────────────────────────────────────
    def _build_ui(self) -> None:
        # ── 타이틀 ──────────────────────────────────
        title_bar = ttk.Frame(self)
        title_bar.pack(fill="x")
        tk.Label(title_bar, text="  OPENCPN IDS SIGNAL GENERATOR  v4",
                 bg="#35d0ff", fg="#08101a", font=("Consolas", 14, "bold"), padx=10, pady=8).pack(fill="x")
        tk.Label(title_bar,
                 text="  AIS NMEA 0183 UDP Sender  |  Ghost Fleet Attack Simulator  |  CSV Decoded Replay",
                 bg="#112033", fg="#8aa1bb", font=("Consolas", 9), padx=10, pady=3).pack(fill="x")

        # ── 하단 고정 컨트롤 바 (스크롤 영역 밖) ────
        self._build_control_bar()

        # ── 좌우 사이즈 조절 가능한 PanedWindow ─────
        paned = tk.PanedWindow(self, orient="horizontal",
                               bg="#24354d", sashwidth=5, sashrelief="flat",
                               handlesize=0)
        paned.pack(fill="both", expand=True)

        # ── 왼쪽: 스크롤 가능한 설정 패널 ──────────
        left_outer = ttk.Frame(paned)
        paned.add(left_outer, minsize=320, width=500, stretch="always")

        canvas = tk.Canvas(left_outer, bg="#09111d", highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_outer, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)
        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _on_canvas_resize(e):
            canvas.itemconfig("all", width=e.width)
        canvas.bind("<Configure>", _on_canvas_resize)

        def _on_mousewheel(e):
            canvas.yview_scroll(-1 * (e.delta // 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        sf = self.scroll_frame
        self._build_network_section(sf)

        self.generated_panel = ttk.Frame(sf)
        self.generated_panel.pack(fill="x")
        self._build_center_section(self.generated_panel)
        self._build_movement_section(self.generated_panel)
        self._build_attack_section(self.generated_panel)
        self._build_circle_section(self.generated_panel)
        self._build_grid_section(self.generated_panel)
        self._build_spiral_section(self.generated_panel)
        self._build_random_section(self.generated_panel)
        self._build_extra_section(self.generated_panel)

        self.file_panel = ttk.Frame(sf)
        self.file_panel.pack(fill="x")
        self._build_file_section(self.file_panel)

        self.csv_panel = ttk.Frame(sf)
        self.csv_panel.pack(fill="x")
        self._build_csv_section(self.csv_panel)

        ttk.Frame(sf).pack(pady=8)

        # ── 오른쪽: 로그 패널 ───────────────────────
        right = ttk.Frame(paned)
        paned.add(right, minsize=260, stretch="always")

        tk.Label(right, text="  LIVE TRANSMISSION LOG",
                 bg="#09111d", fg="#35d0ff", font=("Consolas", 11, "bold"),
                 padx=12, pady=8).pack(fill="x")
        self.log_box = scrolledtext.ScrolledText(
            right, bg="#051019", fg="#dff7ff", font=("Consolas", 9),
            insertbackground="#35d0ff", selectbackground="#1b3555",
            relief="flat", borderwidth=0, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_box.tag_config("info",  foreground="#c6d6ea")
        self.log_box.tag_config("start", foreground="#35d0ff")
        self.log_box.tag_config("error", foreground="#ff6b6b")

    # ── 섹션 빌더 ───────────────────────────────────
    def _build_network_section(self, parent) -> None:
        self._section(parent, "네트워크")
        self.host_entry = self._row(parent, "대상 IP",    self._entry, default="127.0.0.1")
        self.port_entry = self._row(parent, "UDP 포트",   self._entry, default="1111")

    def _build_center_section(self, parent) -> None:
        self._section(parent, "기준 좌표 (기본: 서해 37°N 21°E)")
        self.lat_entry = self._row(parent, "중심 위도 (N)", self._entry, default="37.00")
        self.lon_entry = self._row(parent, "중심 경도 (E)", self._entry, default="21.00")
        self.interval_spin = self._row(parent, "생성 신호 주기(초)", self._spin,
                                        from_=0.2, to=30.0, default=2.0, step=0.1)

    def _build_movement_section(self, parent) -> None:
        self._section(parent, "★ 선단 이동 제어", color="accent")
        self.move_speed   = self._row(parent, "이동 속도 (kn)",        self._spin, from_=0.0, to=30.0, default=0.0, step=0.5)
        self.move_heading = self._row(parent, "이동 방향 (도, 진북=0)", self._spin, from_=0, to=359, default=0, step=5)
        self.move_accel   = self._row(parent, "가속도 (kn/분)",         self._spin, from_=0.0, to=5.0, default=0.0, step=0.1)

    def _build_attack_section(self, parent) -> None:
        self._section(parent, "생성 패턴")
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=16, pady=4)
        self.attack_var = tk.StringVar(value=ATTACK_OPTIONS[0][1])
        combo = ttk.Combobox(row, textvariable=self.attack_var,
                              values=[label for _, label in ATTACK_OPTIONS],
                              state="readonly", font=("Consolas", 11))
        combo.pack(fill="x")
        combo.bind("<<ComboboxSelected>>", self._on_attack_change)

    def _build_circle_section(self, parent) -> None:
        self.circle_frame = ttk.Frame(parent)
        self.circle_frame.pack(fill="x")
        self._section(self.circle_frame, "속도 이상 설정")
        self.circle_count          = self._row(self.circle_frame, "선박 수",            self._spin, from_=1, to=100, default=25, step=1)
        self.circle_radius         = self._row(self.circle_frame, "기본 속도 (kn)",      self._spin, from_=0.0, to=40.0, default=8.0, step=0.5)
        self.circle_speed          = self._row(self.circle_frame, "스파이크 속도 (kn)",  self._spin, from_=0.0, to=60.0, default=30.0, step=1.0)
        self.circle_mode           = self._row(self.circle_frame, "스파이크 방식",       self._combo, values=["간헐", "순간"], default="간헐")
        self.circle_converge_rate  = self._row(self.circle_frame, "스파이크 주기 (초)",  self._spin, from_=1.0, to=120.0, default=10.0, step=1.0)

    def _build_grid_section(self, parent) -> None:
        self.grid_frame = ttk.Frame(parent)
        self.grid_frame.pack(fill="x")
        self._section(self.grid_frame, "정박 이동 이상 설정")
        self.grid_rows    = self._row(self.grid_frame, "선박 수",             self._spin, from_=1, to=100, default=30, step=1)
        self.grid_cols    = self._row(self.grid_frame, "클러스터 반경 (도)",   self._spin, from_=0.01, to=1.0, default=0.10, step=0.01)
        self.grid_spacing = self._row(self.grid_frame, "이상 이동 속도 (kn)", self._spin, from_=0.0, to=10.0, default=3.0, step=0.1)
        self.grid_speed   = self._row(self.grid_frame, "COG 방향 (도)",       self._spin, from_=0, to=359, default=90, step=5)
        self.grid_heading = self._row(self.grid_frame, "경도 오프셋 (도)",     self._spin, from_=-1.0, to=1.0, default=0.0, step=0.01)
        self.grid_rotate  = self._row(self.grid_frame, "드리프트 강도",        self._spin, from_=-1.0, to=1.0, default=0.0, step=0.05)

    def _build_spiral_section(self, parent) -> None:
        self.spiral_frame = ttk.Frame(parent)
        self.spiral_frame.pack(fill="x")
        self._section(self.spiral_frame, "COG/HDG 불일치 설정")
        self.spiral_count  = self._row(self.spiral_frame, "선박 수",              self._spin, from_=3, to=60, default=20, step=1)
        self.spiral_turns  = self._row(self.spiral_frame, "불일치 각도 (도)",      self._spin, from_=90.0, to=180.0, default=150.0, step=5.0)
        self.spiral_max_r  = self._row(self.spiral_frame, "기본 속도 (kn)",        self._spin, from_=0.0, to=30.0, default=10.0, step=0.5)
        self.spiral_speed  = self._row(self.spiral_frame, "COG 변화 속도 (도/초)", self._spin, from_=0.0, to=20.0, default=5.0, step=0.5)
        self.spiral_expand = self._row(self.spiral_frame, "HDG 편차 (도)",         self._spin, from_=0.0, to=180.0, default=120.0, step=5.0)

    def _build_random_section(self, parent) -> None:
        self.random_frame = ttk.Frame(parent)
        self.random_frame.pack(fill="x")
        self._section(self.random_frame, "위치 점프 이상 설정")
        self.random_count              = self._row(self.random_frame, "선박 수",          self._spin, from_=1, to=100, default=30, step=1)
        self.random_spread             = self._row(self.random_frame, "점프 반경 (도)",    self._spin, from_=0.05, to=2.0, default=0.30, step=0.05)
        self.random_converge_strength  = self._row(self.random_frame, "점프 간격 (초)",   self._spin, from_=1.0, to=60.0, default=10.0, step=0.5)
        self.random_converge_lat       = self._row(self.random_frame, "점프 기준 위도",   self._entry, default="37.00")
        self.random_converge_lon       = self._row(self.random_frame, "점프 기준 경도",   self._entry, default="21.00")

    def _build_extra_section(self, parent) -> None:
        self._section(parent, "추가 옵션")
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=16, pady=4)
        self.anchor_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="중앙 정박선 추가 (MMSI: 440123456)",
                         variable=self.anchor_var).pack(anchor="w")

    def _build_file_section(self, parent) -> None:
        self._section(parent, "정상 신호 파일")
        file_row = ttk.Frame(parent)
        file_row.pack(fill="x", padx=16, pady=2)
        ttk.Label(file_row, text="NMEA 파일", width=26, anchor="w",
                   style="Sub.TLabel").pack(side="left")
        default_path = str(DEFAULT_SAMPLE_FILE if DEFAULT_SAMPLE_FILE.exists() else "")
        self.file_path_var = tk.StringVar(value=default_path)
        ttk.Entry(file_row, textvariable=self.file_path_var).pack(side="left", fill="x", expand=True)
        tk.Button(file_row, text="찾기", bg="#172334", fg="#edf4ff", relief="flat",
                   activebackground="#24354d", command=self._browse_file,
                   padx=12, pady=4).pack(side="left", padx=(6, 0))
        self.file_interval_spin = self._row(parent, "문장 간격(초)", self._spin,
                                             from_=0.01, to=5.0, default=0.1, step=0.01)
        repeat_row = ttk.Frame(parent)
        repeat_row.pack(fill="x", padx=16, pady=4)
        self.file_repeat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(repeat_row, text="파일 끝까지 전송 후 반복",
                         variable=self.file_repeat_var).pack(anchor="w")

    def _build_csv_section(self, parent) -> None:
        self._section(parent, "디코딩 데이터 송신  (CSV / TXT / TSV)")

        # 파일 경로
        file_row = ttk.Frame(parent)
        file_row.pack(fill="x", padx=16, pady=2)
        ttk.Label(file_row, text="데이터 파일", width=26, anchor="w",
                   style="Sub.TLabel").pack(side="left")
        self.csv_file_path_var = tk.StringVar(value="")
        ttk.Entry(file_row, textvariable=self.csv_file_path_var).pack(side="left", fill="x", expand=True)
        tk.Button(file_row, text="찾기", bg="#172334", fg="#edf4ff", relief="flat",
                   activebackground="#24354d", command=self._browse_csv,
                   padx=12, pady=4).pack(side="left", padx=(6, 0))

        # 간격
        self.csv_interval_spin = self._row(parent, "행 간격(초)", self._spin,
                                            from_=0.01, to=30.0, default=1.0, step=0.05)

        # 옵션 행
        opts_row = ttk.Frame(parent)
        opts_row.pack(fill="x", padx=16, pady=4)
        self.csv_use_timestamp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts_row, text="타임스탬프 컬럼으로 간격 재현 (base_date_time 등)",
                         variable=self.csv_use_timestamp_var).pack(anchor="w")
        self.csv_repeat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts_row, text="파일 끝까지 전송 후 반복",
                         variable=self.csv_repeat_var).pack(anchor="w")

        # 컬럼 안내
        hint = ttk.Frame(parent)
        hint.pack(fill="x", padx=16, pady=(0, 6))
        ttk.Label(hint,
                  text="필수 컬럼: mmsi, latitude, longitude, sog, cog, heading\n"
                       "선택 컬럼: vessel_name, status, base_date_time  |  구분자 자동 감지 (쉼표/탭/세미콜론)",
                  style="Sub.TLabel", justify="left").pack(anchor="w")

    def _build_control_bar(self) -> None:
        """하단 고정 컨트롤 바 — 스크롤 영역 밖, 항상 보임."""
        bar = tk.Frame(self, bg="#0d1f33", pady=8)
        bar.pack(side="bottom", fill="x")

        tk.Frame(bar, height=1, bg="#24354d").pack(fill="x", padx=0, pady=(0, 8))

        inner = tk.Frame(bar, bg="#0d1f33")
        inner.pack(fill="x", padx=10)
        inner.columnconfigure((1, 2, 3, 4, 5, 6), weight=1)

        LABEL_W = 9  # 라벨 고정 너비(글자)

        def _lbl(text, col):
            tk.Label(inner, text=text, bg="#0d1f33", fg="#6a85a0",
                     font=("Consolas", 9), width=LABEL_W, anchor="e").grid(
                row=0, column=col, sticky="e", padx=(0, 4))

        def _btn(text, col, bg, fg, cmd):
            b = tk.Button(inner, text=text, bg=bg, fg=fg,
                          font=("Consolas", 10, "bold"), activebackground="#ccf8ff",
                          relief="flat", cursor="hand2", padx=0, pady=6,
                          command=cmd)
            b.grid(row=0, column=col, sticky="ew", padx=2)
            return b

        # 생성 신호
        _lbl("생성 신호", 0)
        self.generated_start_btn = _btn("생성 시작", 1, "#35d0ff", "#08101a", self.start_generated_sender)
        self.generated_stop_btn  = _btn("생성 중단", 2, "#172334", "#ff7c7c", self.stop_generated_sender)

        # 정상 파일
        _lbl("정상 파일", 3)
        self.file_start_btn = _btn("파일 시작", 4, "#7ef0c9", "#08101a", self.start_file_sender)
        self.file_stop_btn  = _btn("파일 중단", 5, "#172334", "#ffb36b", self.stop_file_sender)

        # CSV 데이터
        _lbl("CSV 데이터", 6)
        self.csv_start_btn = _btn("CSV 시작", 7, "#c9a0ff", "#08101a", self.start_csv_sender)
        self.csv_stop_btn  = _btn("CSV 중단", 8, "#172334", "#ff9fc4", self.stop_csv_sender)

        inner.columnconfigure((1, 2, 4, 5, 7, 8), weight=1)

        # 전체 중단
        self.stop_all_btn = tk.Button(
            bar, text="⏹  전체 중단", bg="#1e3650", fg="#edf4ff",
            font=("Consolas", 10, "bold"), activebackground="#304663",
            relief="flat", cursor="hand2", pady=5,
            command=self.stop_all_senders)
        self.stop_all_btn.pack(fill="x", padx=10, pady=(6, 0))

    # ── 이벤트 ─────────────────────────────────────
    def _browse_file(self) -> None:
        initial_dir = DEFAULT_SAMPLE_FILE.parent if DEFAULT_SAMPLE_FILE.exists() else Path.cwd()
        selected = filedialog.askopenfilename(
            title="NMEA 파일 선택", initialdir=str(initial_dir),
            filetypes=[("NMEA files", "*.txt *.nmea *.log"), ("All files", "*.*")])
        if selected:
            self.file_path_var.set(selected)

    def _browse_csv(self) -> None:
        selected = filedialog.askopenfilename(
            title="디코딩 데이터 파일 선택", initialdir=str(Path.cwd()),
            filetypes=[
                ("데이터 파일", "*.csv *.txt *.tsv *.log"),
                ("All files", "*.*"),
            ])
        if selected:
            self.csv_file_path_var.set(selected)

    def _on_attack_change(self, event=None) -> None:
        attack_key = ATTACK_LABEL_TO_KEY[self.attack_var.get()]
        frames = {
            "speed_spike":     self.circle_frame,
            "anchor_move":     self.grid_frame,
            "course_mismatch": self.spiral_frame,
            "position_jump":   self.random_frame,
        }
        for key, frame in frames.items():
            if key == attack_key:
                if not frame.winfo_manager():
                    frame.pack(fill="x")
            elif frame.winfo_manager():
                frame.pack_forget()

    # ── 설정 수집 ───────────────────────────────────
    def _get_common_cfg(self):
        host = self.host_entry._var.get().strip()
        if not host:
            raise ValueError("대상 IP를 입력하세요.")
        port = int(self.port_entry._var.get())
        if not 1 <= port <= 65535:
            raise ValueError("UDP 포트는 1~65535 범위여야 합니다.")
        return {"host": host, "port": port}

    def _get_generated_cfg(self):
        cfg = self._get_common_cfg()
        interval = float(self.interval_spin._var.get())
        if interval <= 0:
            raise ValueError("생성 신호 주기는 0보다 커야 합니다.")
        attack_label = self.attack_var.get()

        try:
            rcl = float(self.random_converge_lat._var.get())
            rcn = float(self.random_converge_lon._var.get())
        except Exception:
            rcl = float(self.lat_entry._var.get())
            rcn = float(self.lon_entry._var.get())

        cfg.update({
            "interval":    interval,
            "center_lat":  float(self.lat_entry._var.get()),
            "center_lon":  float(self.lon_entry._var.get()),
            "attack_key":   ATTACK_LABEL_TO_KEY[attack_label],
            "attack_label": attack_label,
            "add_anchor":   self.anchor_var.get(),
            "move_speed":   float(self.move_speed._var.get()),
            "move_heading": float(self.move_heading._var.get()),
            "move_accel":   float(self.move_accel._var.get()),
            # 속도 이상
            "speed_count":    min(200, max(1, int(float(self.circle_count._var.get())))),
            "speed_base":     float(self.circle_radius._var.get()),
            "speed_spike":    float(self.circle_speed._var.get()),
            "speed_mode":     self.circle_mode._var.get(),
            "speed_interval": float(self.circle_converge_rate._var.get()),
            # 정박 이동 이상
            "anchor_count":      min(300, max(1, int(float(self.grid_rows._var.get())))),
            "anchor_radius":     float(self.grid_cols._var.get()),
            "anchor_speed":      float(self.grid_spacing._var.get()),
            "anchor_cog":        float(self.grid_speed._var.get()),
            "anchor_lon_offset": float(self.grid_heading._var.get()),
            "anchor_drift":      float(self.grid_rotate._var.get()),
            # COG/HDG 불일치
            "course_count":    min(200, max(3, int(float(self.spiral_count._var.get())))),
            "course_mismatch": float(self.spiral_turns._var.get()),
            "course_speed":    float(self.spiral_max_r._var.get()),
            "course_drift":    float(self.spiral_speed._var.get()),
            "course_offset":   float(self.spiral_expand._var.get()),
            # 위치 점프 이상
            "jump_count":      min(300, max(1, int(float(self.random_count._var.get())))),
            "jump_radius":     float(self.random_spread._var.get()),
            "jump_interval":   float(self.random_converge_strength._var.get()),
            "jump_center_lat": rcl,
            "jump_center_lon": rcn,
        })
        return cfg

    def _get_file_cfg(self):
        cfg = self._get_common_cfg()
        file_path = Path(self.file_path_var.get().strip())
        if not file_path.exists():
            raise ValueError("정상 신호 파일 경로를 확인하세요.")
        file_interval = float(self.file_interval_spin._var.get())
        if file_interval <= 0:
            raise ValueError("문장 간격은 0보다 커야 합니다.")
        cfg.update({
            "file_path":     str(file_path),
            "file_interval": file_interval,
            "file_repeat":   self.file_repeat_var.get(),
        })
        return cfg

    def _get_csv_cfg(self):
        cfg = self._get_common_cfg()
        csv_path = Path(self.csv_file_path_var.get().strip())
        if not csv_path.exists():
            raise ValueError("데이터 파일 경로를 확인하세요.")
        csv_interval = float(self.csv_interval_spin._var.get())
        if csv_interval < 0:
            raise ValueError("행 간격은 0 이상이어야 합니다.")
        cfg.update({
            "csv_file_path":     str(csv_path),
            "csv_interval":      csv_interval,
            "csv_repeat":        self.csv_repeat_var.get(),
            "csv_use_timestamp": self.csv_use_timestamp_var.get(),
        })
        return cfg

    # ── 채널 상태 ───────────────────────────────────
    def _any_channel_running(self) -> bool:
        return any([
            self.generated_thread is not None and self.generated_thread.is_alive(),
            self.file_thread      is not None and self.file_thread.is_alive(),
            self.csv_thread       is not None and self.csv_thread.is_alive(),
        ])

    def _set_channel_state(self, channel: str, is_running: bool) -> None:
        BTN = {
            "generated": (self.generated_start_btn, self.generated_stop_btn, "#35d0ff"),
            "file":      (self.file_start_btn,      self.file_stop_btn,      "#7ef0c9"),
            "csv":       (self.csv_start_btn,        self.csv_stop_btn,       "#c9a0ff"),
        }
        if channel not in BTN:
            return
        start_btn, stop_btn, start_color = BTN[channel]
        if is_running:
            start_btn.config(state="disabled", bg="#172334", fg="#5f738c")
            stop_btn.config(state="normal")
        else:
            start_btn.config(state="normal", bg=start_color, fg="#08101a")
            stop_btn.config(state="disabled")

        self.stop_all_btn.config(
            state="normal" if (is_running or self._any_channel_running()) else "disabled"
        )

    # ── 송신 제어 ───────────────────────────────────
    def start_generated_sender(self) -> None:
        if self.generated_thread is not None and self.generated_thread.is_alive():
            self.log("[생성] 이미 실행 중입니다.", "error"); return
        try:
            cfg = self._get_generated_cfg()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc)); return
        self.generated_stop_event = threading.Event()
        self._set_channel_state("generated", True)
        self.log("[생성 대기] 송신 스레드 시작", "start")
        self.generated_thread = threading.Thread(
            target=sender_worker, args=("generated", cfg, log_queue, self.generated_stop_event),
            daemon=True)
        self.generated_thread.start()

    def stop_generated_sender(self) -> None:
        if self.generated_thread is not None and self.generated_thread.is_alive():
            self.generated_stop_event.set()
            self.log("[생성 중단] 사용자 중단 요청", "error")

    def start_file_sender(self) -> None:
        if self.file_thread is not None and self.file_thread.is_alive():
            self.log("[파일] 이미 실행 중입니다.", "error"); return
        try:
            cfg = self._get_file_cfg()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc)); return
        self.file_stop_event = threading.Event()
        self._set_channel_state("file", True)
        self.log("[파일 대기] 송신 스레드 시작", "start")
        self.file_thread = threading.Thread(
            target=sender_worker, args=("file", cfg, log_queue, self.file_stop_event),
            daemon=True)
        self.file_thread.start()

    def stop_file_sender(self) -> None:
        if self.file_thread is not None and self.file_thread.is_alive():
            self.file_stop_event.set()
            self.log("[파일 중단] 사용자 중단 요청", "error")

    def start_csv_sender(self) -> None:
        if self.csv_thread is not None and self.csv_thread.is_alive():
            self.log("[CSV] 이미 실행 중입니다.", "error"); return
        try:
            cfg = self._get_csv_cfg()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc)); return
        self.csv_stop_event = threading.Event()
        self._set_channel_state("csv", True)
        self.log("[CSV 대기] 송신 스레드 시작", "start")
        self.csv_thread = threading.Thread(
            target=sender_worker, args=("csv", cfg, log_queue, self.csv_stop_event),
            daemon=True)
        self.csv_thread.start()

    def stop_csv_sender(self) -> None:
        if self.csv_thread is not None and self.csv_thread.is_alive():
            self.csv_stop_event.set()
            self.log("[CSV 중단] 사용자 중단 요청", "error")

    def stop_all_senders(self) -> None:
        self.stop_generated_sender()
        self.stop_file_sender()
        self.stop_csv_sender()

    def log(self, message: str, level: str = "info") -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{timestamp}] {message}\n", level)
        self.log_box.see("end")

    def _poll_log(self) -> None:
        while not log_queue.empty():
            item = log_queue.get_nowait()
            if item.get("kind") == "channel_state" and item.get("state") == "finished":
                self._set_channel_state(item.get("channel", ""), False)
                continue
            if item.get("kind") == "state":
                continue
            self.log(item["message"], item.get("level", "info"))
        self.after(200, self._poll_log)

    def _on_close(self) -> None:
        self.generated_stop_event.set()
        self.file_stop_event.set()
        self.csv_stop_event.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
