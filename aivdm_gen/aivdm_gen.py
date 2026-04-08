#!/usr/bin/env python3
"""
OpenCPN IDS Signal Generator  v3
Generated AIS patterns + normal NMEA file replay GUI.

v3 신규 기능:
  - 선단 이동(Translation): 전체 선단을 실시간으로 특정 방향/속도로 이동
  - 기본 좌표 37N / 21E (서해 해역)
  - 이동 방향(도), 이동 속도(kn), 가속도, 진동(사인파 이동) 제어
  - 패턴별 추가 파라미터: 원형 수렴/발산 모드, 격자 회전, 나선 확장 모드,
    무작위 집단 드리프트, 수렴 포인트
"""

from __future__ import annotations

import math
import queue
import random
import socket
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


MODE_OPTIONS = [
    ("generated", "생성 신호"),
    ("normal_file", "정상 신호 파일"),
]
MODE_LABEL_TO_KEY = {label: key for key, label in MODE_OPTIONS}

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


def queue_state(log_q, state: str) -> None:
    log_q.put({"kind": "state", "state": state})


def queue_channel_state(log_q, channel: str, state: str) -> None:
    log_q.put({"kind": "channel_state", "channel": channel, "state": state})


def sleep_with_stop(seconds: float) -> bool:
    end_time = time.time() + max(0.0, seconds)
    while not stop_event.is_set():
        remaining = end_time - time.time()
        if remaining <= 0:
            return True
        time.sleep(min(0.1, remaining))
    return False


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
# 선단 이동(Translation) 헬퍼
# ──────────────────────────────────────────────────

_KN_TO_DEG_PER_SEC = 1.0 / 3600.0 * 1852.0 / 111320.0  # knot → deg/s (위도 기준)


def translation_offset(cfg: dict, elapsed: float) -> tuple[float, float]:
    """
    선단 전체 이동 오프셋(dlat, dlon) 계산.
    elapsed: 송신 시작 후 경과 시간(초)
    """
    move_speed = float(cfg.get("move_speed", 0.0))   # kn
    move_heading = float(cfg.get("move_heading", 0.0))  # 도 (진북 기준)
    move_accel = float(cfg.get("move_accel", 0.0))   # kn/min 가속

    # 가속도 적용
    effective_speed = move_speed + move_accel * (elapsed / 60.0)
    # 이동 방향(헤딩)에 따른 위경도 변화
    rad = math.radians(move_heading)
    speed_dps = effective_speed * _KN_TO_DEG_PER_SEC  # deg/s
    dlat = math.cos(rad) * speed_dps * elapsed
    dlon = math.sin(rad) * speed_dps * elapsed * 1.2   # 경도 보정
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
        vessel.heading = int((vessel.cog + float(cfg.get("course_mismatch", 150.0)) + float(cfg.get("course_offset", 120.0))) % 360)


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
            jump_lat = random.choice([-1, 1]) * random.uniform(0.08, 0.20)
            jump_lon = random.choice([-1, 1]) * random.uniform(0.08, 0.20)
            vessel.lat += jump_lat
            vessel.lon += jump_lon
            vessel.cog = random.uniform(0, 360)
            vessel.heading = int(vessel.cog)

        step = vessel.sog * _KN_TO_DEG_PER_SEC * dt
        vessel.lat += math.cos(math.radians(vessel.cog)) * step
        vessel.lon += math.sin(math.radians(vessel.cog)) * step
        vessel.heading = int(vessel.cog)


# ──────────────────────────────────────────────────
# JBU 글자 선단
# ──────────────────────────────────────────────────

def make_jbu_fleet(cfg):
    center_lat = float(cfg["center_lat"])
    center_lon = float(cfg["center_lon"])
    scale = float(cfg["jbu_scale"])

    j_points = [
        (0.08, 0.04), (0.05, 0.04), (0.02, 0.04),
        (-0.01, 0.04), (-0.04, 0.03), (-0.06, 0.01), (-0.06, -0.02),
    ]
    b_points = [
        (0.08, 0.0), (0.04, 0.0), (0.0, 0.0), (-0.04, 0.0), (-0.08, 0.0),
        (-0.06, 0.025), (-0.04, 0.04), (-0.02, 0.025), (0.0, 0.0),
        (0.02, 0.025), (0.04, 0.04), (0.06, 0.025), (0.08, 0.0),
    ]
    u_points = [
        (0.08, 0.0), (0.04, 0.0), (0.0, 0.0), (-0.04, 0.005),
        (-0.07, 0.02), (-0.07, 0.05), (-0.04, 0.06),
        (0.0, 0.06), (0.04, 0.05), (0.08, 0.02),
    ]

    j_offset = (-0.12 * scale, -0.28 * scale)
    b_offset = (-0.12 * scale, -0.06 * scale)
    u_offset = (-0.12 * scale,  0.16 * scale)

    fleet = []

    def make_letter(points, offset, prefix, start_mmsi):
        ships = []
        for index, (dlat, dlon) in enumerate(points):
            vessel = Vessel(start_mmsi + index, f"{prefix}{index + 1:02d}")
            vessel.lat = center_lat + offset[0] + dlat * scale
            vessel.lon = center_lon + offset[1] + dlon * scale
            vessel._waypoints = [
                (center_lat + offset[0] + lat * scale, center_lon + offset[1] + lon * scale)
                for lat, lon in points
            ]
            vessel._base_waypoints = list(vessel._waypoints)
            vessel._wp_idx = index % len(points)
            vessel._wp_progress = 0.0
            vessel.sog = 3.0 + random.uniform(-0.5, 0.5)
            ships.append(vessel)
        return ships

    fleet.extend(make_letter(j_points, j_offset, "GHOST-J", 990200000))
    fleet.extend(make_letter(b_points, b_offset, "GHOST-B", 990300000))
    fleet.extend(make_letter(u_points, u_offset, "GHOST-U", 990400000))
    return fleet


def update_jbu_fleet(fleet, dt: float, cfg: dict, elapsed: float) -> None:
    dlat, dlon = translation_offset(cfg, elapsed)

    for vessel in fleet:
        if not hasattr(vessel, "_waypoints") or len(vessel._waypoints) < 2:
            continue

        # 웨이포인트 이동 적용
        vessel._waypoints = [
            (blat + dlat, blon + dlon)
            for blat, blon in vessel._base_waypoints
        ]

        waypoints = vessel._waypoints
        current = vessel._wp_idx % len(waypoints)
        nxt = (current + 1) % len(waypoints)
        clat, clon = waypoints[current]
        nlat, nlon = waypoints[nxt]

        distance = math.sqrt((nlat - clat) ** 2 + (nlon - clon) ** 2)
        if distance < 1e-9:
            vessel._wp_idx = nxt
            continue

        step = vessel.sog * _KN_TO_DEG_PER_SEC * dt
        vessel._wp_progress += step / distance
        if vessel._wp_progress >= 1.0:
            vessel._wp_progress = 0.0
            vessel._wp_idx = nxt
            current, nxt = nxt, (nxt + 1) % len(waypoints)
            clat, clon = waypoints[current]
            nlat, nlon = waypoints[nxt]

        progress = vessel._wp_progress
        vessel.lat = clat + (nlat - clat) * progress
        vessel.lon = clon + (nlon - clon) * progress
        dx = nlon - clon
        dy = nlat - clat
        vessel.cog = math.degrees(math.atan2(dx, dy)) % 360
        vessel.heading = int(vessel.cog)


# ──────────────────────────────────────────────────
# 집게 협공 (Pincer)  ─ 신규 패턴
# ──────────────────────────────────────────────────

def make_pincer_fleet(cfg):
    center_lat = float(cfg["center_lat"])
    center_lon = float(cfg["center_lon"])
    count = int(cfg.get("pincer_count", 20))
    width = float(cfg.get("pincer_width", 0.5))
    depth = float(cfg.get("pincer_depth", 0.3))

    fleet = []
    half = count // 2
    for i in range(half):
        # 좌측 날개
        t = i / max(half - 1, 1)
        lat = center_lat + depth * (1 - t)
        lon = center_lon - width * t
        v = Vessel(990800000 + i, f"GHOST-PL{i + 1:02d}")
        v.lat = lat; v.lon = lon
        v._target_lat = center_lat; v._target_lon = center_lon
        v.sog = 8.0 + random.uniform(-1, 1)
        v.cog = math.degrees(math.atan2(
            center_lon - lon, center_lat - lat)) % 360
        v.heading = int(v.cog)
        fleet.append(v)

        # 우측 날개
        v2 = Vessel(990800000 + half + i, f"GHOST-PR{i + 1:02d}")
        v2.lat = lat; v2.lon = center_lon + width * t
        v2._target_lat = center_lat; v2._target_lon = center_lon
        v2.sog = 8.0 + random.uniform(-1, 1)
        v2.cog = math.degrees(math.atan2(
            center_lon - v2.lon, center_lat - v2.lat)) % 360
        v2.heading = int(v2.cog)
        fleet.append(v2)

    return fleet


def update_pincer_fleet(fleet, dt: float, cfg: dict, elapsed: float) -> None:
    dlat, dlon = translation_offset(cfg, elapsed)
    pincer_speed = float(cfg.get("pincer_speed", 8.0))

    for vessel in fleet:
        if not hasattr(vessel, "_target_lat"):
            continue
        target_lat = vessel._target_lat + dlat
        target_lon = vessel._target_lon + dlon
        diff_lat = target_lat - vessel.lat
        diff_lon = target_lon - vessel.lon
        dist = math.sqrt(diff_lat ** 2 + diff_lon ** 2) + 1e-9
        step = pincer_speed * _KN_TO_DEG_PER_SEC * dt
        if dist > 0.001:
            vessel.lat += (diff_lat / dist) * step
            vessel.lon += (diff_lon / dist) * step
        vessel.cog = math.degrees(math.atan2(diff_lon, diff_lat)) % 360
        vessel.heading = int(vessel.cog)
        vessel.sog = pincer_speed


# ──────────────────────────────────────────────────
# 파상 대형 (Wave)  ─ 신규 패턴
# ──────────────────────────────────────────────────

def make_wave_fleet(cfg):
    center_lat = float(cfg["center_lat"])
    center_lon = float(cfg["center_lon"])
    count = int(cfg.get("wave_count", 24))
    width = float(cfg.get("wave_width", 0.6))
    amplitude = float(cfg.get("wave_amplitude", 0.15))
    lanes = int(cfg.get("wave_lanes", 3))

    fleet = []
    per_lane = count // max(lanes, 1)
    for lane in range(lanes):
        lon_offset = center_lon + (lane - lanes / 2) * (width / lanes)
        for i in range(per_lane):
            idx = lane * per_lane + i
            t = i / max(per_lane - 1, 1)
            lat = center_lat - amplitude * 2 * t  # 북에서 남으로 정렬
            v = Vessel(990900000 + idx, f"GHOST-W{idx + 1:02d}")
            v.lat = lat
            v.lon = lon_offset
            v._wave_phase = (i / per_lane) * 2 * math.pi + lane * math.pi / lanes
            v._wave_base_lon = lon_offset
            v._wave_amplitude = amplitude
            v.sog = 10.0
            v.cog = 180.0
            v.heading = 180
            fleet.append(v)
    return fleet


def update_wave_fleet(fleet, t: float, cfg: dict) -> None:
    dlat, dlon = translation_offset(cfg, t)
    wave_speed = float(cfg.get("wave_speed", 10.0))
    wave_freq = float(cfg.get("wave_freq", 0.05))

    for vessel in fleet:
        if not hasattr(vessel, "_wave_phase"):
            continue
        # 남진
        step = wave_speed * _KN_TO_DEG_PER_SEC * 0.1
        vessel.lat -= step
        # 횡방향 사인파
        lon_offset = vessel._wave_amplitude * math.sin(vessel._wave_phase + t * wave_freq)
        vessel.lon = vessel._wave_base_lon + lon_offset + dlon
        vessel.lat += dlat * 0.001

        # 방향 갱신
        vessel.cog = (180 + math.degrees(math.atan2(
            lon_offset - vessel._wave_amplitude * math.sin(vessel._wave_phase + (t - 0.1) * wave_freq),
            -step * 111000
        ))) % 360
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

def send_generated_loop_v3(cfg, log_q, stop_signal: threading.Event) -> bool:
    host = str(cfg["host"])
    port = int(cfg["port"])
    interval = float(cfg["interval"])
    attack_key = str(cfg["attack_key"])
    attack_label = str(cfg["attack_label"])

    fleet = build_generated_fleet(cfg)
    name_sent: set[int] = set()
    tick = 0.0
    iteration = 0
    start_time = time.time()

    move_speed = float(cfg.get("move_speed", 0.0))
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
            tick = elapsed  # 경과 시간 기준으로 tick 동기화

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
                    f"[생성] {iteration}회차 | {sent}건 | {elapsed2:.2f}s | 오프셋 Δlat={dlat:.4f} Δlon={dlon:.4f}",
                    "info",
                )
            if not sleep_with_event(stop_signal, max(0.0, interval - elapsed2)):
                return False
    return False


def send_file_loop_v2(cfg, log_q, stop_signal: threading.Event) -> bool:
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


def sender_worker_v3(channel: str, cfg, log_q, stop_signal: threading.Event) -> None:
    completed = False
    try:
        if channel == "generated":
            completed = send_generated_loop_v3(cfg, log_q, stop_signal)
        else:
            completed = send_file_loop_v2(cfg, log_q, stop_signal)
    except Exception as exc:
        prefix = "생성" if channel == "generated" else "파일"
        queue_log(log_q, f"[{prefix} 오류] {exc}", "error")
    finally:
        label = "생성" if channel == "generated" else "파일"
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
        self.title("OpenCPN IDS Signal Generator  v3")
        self.configure(bg="#09111d")
        self.minsize(1020, 760)
        self.resizable(True, True)
        self.generated_thread: threading.Thread | None = None
        self.file_thread: threading.Thread | None = None
        self.generated_stop_event = threading.Event()
        self.file_stop_event = threading.Event()

        self._setup_styles()
        self._build_ui()
        self._set_channel_state("generated", False)
        self._set_channel_state("file", False)
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
        style = "Header.TLabel" if not color else "Accent.TLabel"
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
        title_bar = ttk.Frame(self)
        title_bar.pack(fill="x")
        tk.Label(title_bar, text="  OPENCPN IDS SIGNAL GENERATOR  v3",
                 bg="#35d0ff", fg="#08101a", font=("Consolas", 14, "bold"), padx=10, pady=8).pack(fill="x")
        tk.Label(title_bar,
                 text="  AIS NMEA 0183 UDP Sender  |  Ghost Fleet Attack Simulator  |  실시간 이동·정밀 제어",
                 bg="#112033", fg="#8aa1bb", font=("Consolas", 9), padx=10, pady=3).pack(fill="x")

        main = ttk.Frame(self)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=False)
        tk.Frame(main, width=1, bg="#24354d").pack(side="left", fill="y")
        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True)

        canvas = tk.Canvas(left, bg="#09111d", highlightthickness=0, width=490)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)
        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                         lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        sf = self.scroll_frame
        self._build_network_section(sf)

        self.generated_panel = ttk.Frame(sf)
        self.generated_panel.pack(fill="x")
        self._build_center_section(self.generated_panel)
        self._build_movement_section(self.generated_panel)   # ★ 이동 제어
        self._build_attack_section(self.generated_panel)
        self._build_circle_section(self.generated_panel)
        self._build_grid_section(self.generated_panel)
        self._build_spiral_section(self.generated_panel)
        self._build_random_section(self.generated_panel)
        self._build_extra_section(self.generated_panel)

        self.file_panel = ttk.Frame(sf)
        self.file_panel.pack(fill="x")
        self._build_file_section(self.file_panel)

        self.control_panel = self._build_control_section(sf)

        # 로그 패널
        tk.Label(right, text="  LIVE TRANSMISSION LOG",
                 bg="#09111d", fg="#35d0ff", font=("Consolas", 11, "bold"),
                 padx=12, pady=8).pack(fill="x")
        self.log_box = scrolledtext.ScrolledText(
            right, bg="#051019", fg="#dff7ff", font=("Consolas", 9),
            insertbackground="#35d0ff", selectbackground="#1b3555",
            relief="flat", borderwidth=0, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_box.tag_config("info", foreground="#c6d6ea")
        self.log_box.tag_config("start", foreground="#35d0ff")
        self.log_box.tag_config("error", foreground="#ff6b6b")

    def _build_network_section(self, parent) -> None:
        self._section(parent, "네트워크")
        self.host_entry = self._row(parent, "대상 IP", self._entry, default="127.0.0.1")
        self.port_entry = self._row(parent, "UDP 포트", self._entry, default="1111")
        self.interval_spin = self._row(parent, "생성 신호 주기(초)", self._spin,
                                        from_=0.2, to=30.0, default=2.0, step=0.1)

    def _build_center_section(self, parent) -> None:
        self._section(parent, "기준 좌표 (기본: 서해 37°N 21°E)")
        self.lat_entry = self._row(parent, "중심 위도 (N)", self._entry, default="37.00")
        self.lon_entry = self._row(parent, "중심 경도 (E)", self._entry, default="21.00")

    def _build_movement_section(self, parent) -> None:
        """★ 선단 이동 제어 섹션"""
        self._section(parent, "★ 선단 이동 제어", color="accent")
        self.move_speed = self._row(parent, "이동 속도 (kn)", self._spin,
                                     from_=0.0, to=30.0, default=0.0, step=0.5)
        self.move_heading = self._row(parent, "이동 방향 (도, 진북=0)", self._spin,
                                       from_=0, to=359, default=0, step=5)
        self.move_accel = self._row(parent, "가속도 (kn/분)", self._spin,
                                     from_=0.0, to=5.0, default=0.0, step=0.1)

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
        self.circle_count = self._row(self.circle_frame, "선박 수", self._spin,
                                       from_=1, to=100, default=25, step=1)
        self.circle_radius = self._row(self.circle_frame, "기본 속도 (kn)", self._spin,
                                        from_=0.0, to=40.0, default=8.0, step=0.5)
        self.circle_speed = self._row(self.circle_frame, "스파이크 속도 (kn)", self._spin,
                                       from_=0.0, to=60.0, default=30.0, step=1.0)
        self.circle_mode = self._row(self.circle_frame, "스파이크 방식", self._combo,
                                      values=["간헐", "순간"], default="간헐")
        self.circle_converge_rate = self._row(self.circle_frame, "스파이크 주기 (초)", self._spin,
                                               from_=1.0, to=120.0, default=10.0, step=1.0)

    def _build_grid_section(self, parent) -> None:
        self.grid_frame = ttk.Frame(parent)
        self.grid_frame.pack(fill="x")
        self._section(self.grid_frame, "정박 이동 이상 설정")
        self.grid_rows = self._row(self.grid_frame, "선박 수", self._spin,
                                    from_=1, to=100, default=30, step=1)
        self.grid_cols = self._row(self.grid_frame, "클러스터 반경 (도)", self._spin,
                                    from_=0.01, to=1.0, default=0.10, step=0.01)
        self.grid_spacing = self._row(self.grid_frame, "이상 이동 속도 (kn)", self._spin,
                                       from_=0.0, to=10.0, default=3.0, step=0.1)
        self.grid_speed = self._row(self.grid_frame, "COG 방향 (도)", self._spin,
                                     from_=0, to=359, default=90, step=5)
        self.grid_heading = self._row(self.grid_frame, "경도 오프셋 (도)", self._spin,
                                       from_=-1.0, to=1.0, default=0.0, step=0.01)
        self.grid_rotate = self._row(self.grid_frame, "드리프트 강도", self._spin,
                                      from_=-1.0, to=1.0, default=0.0, step=0.05)

    def _build_spiral_section(self, parent) -> None:
        self.spiral_frame = ttk.Frame(parent)
        self.spiral_frame.pack(fill="x")
        self._section(self.spiral_frame, "COG/HDG 불일치 설정")
        self.spiral_count = self._row(self.spiral_frame, "선박 수", self._spin,
                                       from_=3, to=60, default=20, step=1)
        self.spiral_turns = self._row(self.spiral_frame, "불일치 각도 (도)", self._spin,
                                       from_=90.0, to=180.0, default=150.0, step=5.0)
        self.spiral_max_r = self._row(self.spiral_frame, "기본 속도 (kn)", self._spin,
                                       from_=0.0, to=30.0, default=10.0, step=0.5)
        self.spiral_speed = self._row(self.spiral_frame, "COG 변화 속도 (도/초)", self._spin,
                                       from_=0.0, to=20.0, default=5.0, step=0.5)
        self.spiral_expand = self._row(self.spiral_frame, "HDG 편차 (도)", self._spin,
                                        from_=0.0, to=180.0, default=120.0, step=5.0)

    def _build_random_section(self, parent) -> None:
        self.random_frame = ttk.Frame(parent)
        self.random_frame.pack(fill="x")
        self._section(self.random_frame, "위치 점프 이상 설정")
        self.random_count = self._row(self.random_frame, "선박 수", self._spin,
                                       from_=1, to=100, default=30, step=1)
        self.random_spread = self._row(self.random_frame, "점프 반경 (도)", self._spin,
                                        from_=0.05, to=2.0, default=0.30, step=0.05)
        self.random_converge_strength = self._row(self.random_frame, "점프 간격 (초)", self._spin,
                                                   from_=1.0, to=60.0, default=10.0, step=0.5)
        self.random_converge_lat = self._row(self.random_frame, "점프 기준 위도", self._entry, default="37.00")
        self.random_converge_lon = self._row(self.random_frame, "점프 기준 경도", self._entry, default="21.00")

    def _build_jbu_section(self, parent) -> None:
        self.jbu_frame = ttk.Frame(parent)
        self.jbu_frame.pack(fill="x")
        self._section(self.jbu_frame, "JBU 글자 설정")
        self.jbu_scale = self._row(self.jbu_frame, "글자 크기 배율", self._spin,
                                    from_=0.5, to=5.0, default=1.0, step=0.1)

    def _build_pincer_section(self, parent) -> None:
        self.pincer_frame = ttk.Frame(parent)
        self.pincer_frame.pack(fill="x")
        self._section(self.pincer_frame, "집게 협공 설정")
        self.pincer_count = self._row(self.pincer_frame, "선박 수 (양날)", self._spin,
                                       from_=4, to=80, default=20, step=2)
        self.pincer_width = self._row(self.pincer_frame, "날개 폭 (도)", self._spin,
                                       from_=0.05, to=2.0, default=0.5, step=0.05)
        self.pincer_depth = self._row(self.pincer_frame, "종심 (도)", self._spin,
                                       from_=0.05, to=1.5, default=0.3, step=0.05)
        self.pincer_speed = self._row(self.pincer_frame, "수렴 속도 (kn)", self._spin,
                                       from_=1, to=30, default=8.0, step=0.5)

    def _build_wave_section(self, parent) -> None:
        self.wave_frame = ttk.Frame(parent)
        self.wave_frame.pack(fill="x")
        self._section(self.wave_frame, "파상 대형 설정")
        self.wave_count = self._row(self.wave_frame, "선박 수", self._spin,
                                     from_=3, to=60, default=24, step=3)
        self.wave_lanes = self._row(self.wave_frame, "열 수 (종열)", self._spin,
                                     from_=1, to=6, default=3, step=1)
        self.wave_width = self._row(self.wave_frame, "전체 폭 (도)", self._spin,
                                     from_=0.1, to=2.0, default=0.6, step=0.1)
        self.wave_amplitude = self._row(self.wave_frame, "횡진폭 (도)", self._spin,
                                         from_=0.01, to=0.5, default=0.15, step=0.01)
        self.wave_speed = self._row(self.wave_frame, "전진 속도 (kn)", self._spin,
                                     from_=1, to=30, default=10.0, step=0.5)
        self.wave_freq = self._row(self.wave_frame, "사인파 주파수", self._spin,
                                    from_=0.005, to=0.2, default=0.05, step=0.005)

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

    def _build_control_section(self, parent) -> ttk.Frame:
        wrapper = ttk.Frame(parent)
        wrapper.pack(fill="x")
        ttk.Separator(wrapper, orient="horizontal").pack(fill="x", padx=10, pady=10)
        ctrl = ttk.Frame(wrapper)
        ctrl.pack(fill="x", padx=10, pady=(0, 16))

        # 생성 신호 버튼
        gen_row = ttk.Frame(ctrl)
        gen_row.pack(fill="x", pady=(0, 6))
        ttk.Label(gen_row, text="생성 신호", width=12, anchor="w", style="Sub.TLabel").pack(side="left")
        self.generated_start_btn = tk.Button(
            gen_row, text="생성 시작", bg="#35d0ff", fg="#08101a",
            font=("Consolas", 11, "bold"), activebackground="#67ddff",
            relief="flat", cursor="hand2", padx=16, pady=7,
            command=self.start_generated_sender)
        self.generated_start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.generated_stop_btn = tk.Button(
            gen_row, text="생성 중단", bg="#172334", fg="#ff7c7c",
            font=("Consolas", 11, "bold"), activebackground="#24354d",
            relief="flat", cursor="hand2", padx=16, pady=7,
            command=self.stop_generated_sender)
        self.generated_stop_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # 파일 버튼
        file_row = ttk.Frame(ctrl)
        file_row.pack(fill="x", pady=(0, 6))
        ttk.Label(file_row, text="정상 파일", width=12, anchor="w", style="Sub.TLabel").pack(side="left")
        self.file_start_btn = tk.Button(
            file_row, text="파일 시작", bg="#7ef0c9", fg="#08101a",
            font=("Consolas", 11, "bold"), activebackground="#9af6d8",
            relief="flat", cursor="hand2", padx=16, pady=7,
            command=self.start_file_sender)
        self.file_start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.file_stop_btn = tk.Button(
            file_row, text="파일 중단", bg="#172334", fg="#ffb36b",
            font=("Consolas", 11, "bold"), activebackground="#24354d",
            relief="flat", cursor="hand2", padx=16, pady=7,
            command=self.stop_file_sender)
        self.file_stop_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.stop_all_btn = tk.Button(
            ctrl, text="전체 중단", bg="#24354d", fg="#edf4ff",
            font=("Consolas", 11, "bold"), activebackground="#304663",
            relief="flat", cursor="hand2", padx=16, pady=7,
            command=self.stop_all_senders)
        self.stop_all_btn.pack(fill="x")
        return wrapper

    # ── 이벤트 ─────────────────────────────────────
    def _browse_file(self) -> None:
        initial_dir = DEFAULT_SAMPLE_FILE.parent if DEFAULT_SAMPLE_FILE.exists() else Path.cwd()
        selected = filedialog.askopenfilename(
            title="NMEA 파일 선택", initialdir=str(initial_dir),
            filetypes=[("NMEA files", "*.txt *.nmea *.log"), ("All files", "*.*")])
        if selected:
            self.file_path_var.set(selected)

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

        # 수렴 좌표 파싱
        try:
            rcl = float(self.random_converge_lat._var.get())
            rcn = float(self.random_converge_lon._var.get())
        except Exception:
            rcl = float(self.lat_entry._var.get())
            rcn = float(self.lon_entry._var.get())

        cfg.update({
            "interval": interval,
            "center_lat": float(self.lat_entry._var.get()),
            "center_lon": float(self.lon_entry._var.get()),
            "attack_key": ATTACK_LABEL_TO_KEY[attack_label],
            "attack_label": attack_label,
            "add_anchor": self.anchor_var.get(),
            # 이동 제어
            "move_speed":       float(self.move_speed._var.get()),
            "move_heading":     float(self.move_heading._var.get()),
            "move_accel":       float(self.move_accel._var.get()),
            # 원형
            "speed_count":         min(200, max(1, int(float(self.circle_count._var.get())))),
            "speed_base":          float(self.circle_radius._var.get()),
            "speed_spike":         float(self.circle_speed._var.get()),
            "speed_mode":          self.circle_mode._var.get(),
            "speed_interval":      float(self.circle_converge_rate._var.get()),
            # 정박 이동 이상
            "anchor_count":        min(300, max(1, int(float(self.grid_rows._var.get())))),
            "anchor_radius":       float(self.grid_cols._var.get()),
            "anchor_speed":        float(self.grid_spacing._var.get()),
            "anchor_cog":          float(self.grid_speed._var.get()),
            "anchor_lon_offset":   float(self.grid_heading._var.get()),
            "anchor_drift":        float(self.grid_rotate._var.get()),
            # COG/HDG 불일치
            "course_count":        min(200, max(3, int(float(self.spiral_count._var.get())))),
            "course_mismatch":     float(self.spiral_turns._var.get()),
            "course_speed":        float(self.spiral_max_r._var.get()),
            "course_drift":        float(self.spiral_speed._var.get()),
            "course_offset":       float(self.spiral_expand._var.get()),
            # 위치 점프 이상
            "jump_count":          min(300, max(1, int(float(self.random_count._var.get())))),
            "jump_radius":         float(self.random_spread._var.get()),
            "jump_interval":       float(self.random_converge_strength._var.get()),
            "jump_center_lat":     rcl,
            "jump_center_lon":     rcn,
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
            "file_path": str(file_path),
            "file_interval": file_interval,
            "file_repeat": self.file_repeat_var.get(),
        })
        return cfg

    # ── 채널 상태 ───────────────────────────────────
    def _any_channel_running(self) -> bool:
        return (
            (self.generated_thread is not None and self.generated_thread.is_alive()) or
            (self.file_thread is not None and self.file_thread.is_alive())
        )

    def _set_channel_state(self, channel: str, is_running: bool) -> None:
        if channel == "generated":
            if is_running:
                self.generated_start_btn.config(state="disabled", bg="#172334", fg="#5f738c")
                self.generated_stop_btn.config(state="normal")
            else:
                self.generated_start_btn.config(state="normal", bg="#35d0ff", fg="#08101a")
                self.generated_stop_btn.config(state="disabled")
        else:
            if is_running:
                self.file_start_btn.config(state="disabled", bg="#172334", fg="#5f738c")
                self.file_stop_btn.config(state="normal")
            else:
                self.file_start_btn.config(state="normal", bg="#7ef0c9", fg="#08101a")
                self.file_stop_btn.config(state="disabled")
        if is_running:
            self.stop_all_btn.config(state="normal")
        else:
            self.stop_all_btn.config(state="normal" if self._any_channel_running() else "disabled")

    # ── 송신 제어 ───────────────────────────────────
    def start_generated_sender(self) -> None:
        if self.generated_thread is not None and self.generated_thread.is_alive():
            self.log("[생성] 이미 실행 중입니다.", "error")
            return
        try:
            cfg = self._get_generated_cfg()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc))
            return
        self.generated_stop_event = threading.Event()
        self._set_channel_state("generated", True)
        self.log("[생성 대기] 송신 스레드 시작", "start")
        self.generated_thread = threading.Thread(
            target=sender_worker_v3,
            args=("generated", cfg, log_queue, self.generated_stop_event),
            daemon=True,
        )
        self.generated_thread.start()

    def stop_generated_sender(self) -> None:
        if self.generated_thread is not None and self.generated_thread.is_alive():
            self.generated_stop_event.set()
            self.log("[생성 중단] 사용자 중단 요청", "error")

    def start_file_sender(self) -> None:
        if self.file_thread is not None and self.file_thread.is_alive():
            self.log("[파일] 이미 실행 중입니다.", "error")
            return
        try:
            cfg = self._get_file_cfg()
        except ValueError as exc:
            messagebox.showerror("입력 오류", str(exc))
            return
        self.file_stop_event = threading.Event()
        self._set_channel_state("file", True)
        self.log("[파일 대기] 송신 스레드 시작", "start")
        self.file_thread = threading.Thread(
            target=sender_worker_v3,
            args=("file", cfg, log_queue, self.file_stop_event),
            daemon=True,
        )
        self.file_thread.start()

    def stop_file_sender(self) -> None:
        if self.file_thread is not None and self.file_thread.is_alive():
            self.file_stop_event.set()
            self.log("[파일 중단] 사용자 중단 요청", "error")

    def stop_all_senders(self) -> None:
        self.stop_generated_sender()
        self.stop_file_sender()

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
            if item.get("kind") == "state" and item.get("state") == "finished":
                continue
            self.log(item["message"], item.get("level", "info"))
        self.after(200, self._poll_log)

    def _on_close(self) -> None:
        self.generated_stop_event.set()
        self.file_stop_event.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
