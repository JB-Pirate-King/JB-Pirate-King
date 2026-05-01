#!/usr/bin/env python3
"""
OpenCPN IDS Signal Generator  v4
AIS NMEA 0183 UDP Sender  |  Ghost Fleet Attack Simulator

v4 변경사항:
  - UI 리사이즈 정상화 (canvas 고정 너비 제거, 좌우 패널 비율 조정)
  - JBU 글자선단 / 집게협공 / 파상대형 드롭다운 복원
  - 신규 패턴: 고스트쉽 원형 정지, 원형 순찰, 직선 왕복, 실제루트+이상속도
  - ML 우회 테스트: Low&Slow, Temporal Camouflage, Gradual Drift, Feature Mimicry
  - 중앙 정박선 기본값 비활성화
  - 실시간 조작 창(RealTimeControlWindow) 추가
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


# ── 공통 상수 ─────────────────────────────────────
_KN_TO_DEG_PER_SEC = 1.0 / 3600.0 * 1852.0 / 111320.0   # knot → deg/s

ATTACK_OPTIONS = [
    # ── A. 규칙 기반 탐지 대상 ────────────────────
    ("speed_spike",          "A1  속도 이상"),
    ("anchor_move",          "A2  정박 이동 이상"),
    ("course_mismatch",      "A3  COG/HDG 불일치"),
    ("position_jump",        "A4  위치 점프 이상"),
    # ── B. 시각적 패턴 ────────────────────────────
    ("ghost_circle_static",  "B1  고스트쉽 원형 정지"),
    ("circle_patrol",        "B2  원형 순찰"),
    ("linear_bounce",        "B3  직선 왕복"),
    ("realistic_speed",      "B4  실제루트 이상속도"),
    ("jbu_fleet",            "B5  JBU 글자 선단"),
    ("pincer",               "B6  집게 협공"),
    ("wave",                 "B7  파상 대형"),
    # ── C. 규칙 IDS False-Negative ────────────────
    ("blind_dt_jump",        "C1  [FN] dt 구간 점프"),
    ("blind_speed_ramp",     "C2  [FN] 속도 단계 상승"),
    ("blind_cog_border",     "C3  [FN] COG/HDG 경계값"),
    ("blind_nav_status",     "C4  [FN] navStatus 회피"),
    # ── D. ML 우회 v1 (피처 단일 조작) ────────────
    ("ml_low_slow",          "D1  [ML] Low & Slow"),
    ("ml_temporal",          "D2  [ML] Temporal Camouflage"),
    ("ml_gradual_drift",     "D3  [ML] Gradual Drift"),
    ("ml_mimicry",           "D4  [ML] Feature Mimicry"),
    # ── E. ML 우회 v2 (구조적 회피) ─────────────
    ("adv_smooth",           "E1  [ADV] Smooth Trajectory"),
    ("adv_desync",           "E2  [ADV] Fleet Desync"),
    ("adv_window_edge",      "E3  [ADV] Window Edge"),
    ("adv_contextual",       "E4  [ADV] Contextual Blend"),
    ("adv_shadow",           "E5  [ADV] Shadow Vessel"),
]
ATTACK_LABEL_TO_KEY = {label: key for key, label in ATTACK_OPTIONS}

DEFAULT_SAMPLE_FILE = Path(__file__).with_name("nmea_data_sample.txt")

log_queue: "queue.Queue[dict[str, str]]" = queue.Queue()
stop_event = threading.Event()

# 실시간 조작 공유 상태 (메인/송신 스레드 공용)
rt_state: dict = {
    "active":          False,   # RT 조작 활성 여부
    "sog_mult":        1.0,     # SOG 배율 (0.0~5.0)
    "cog_offset":      0.0,     # COG 오프셋 (+- 180도)
    "pos_scatter":     0.0,     # 위치 노이즈 반경 (도)
    "nav_override":    -1,      # navStatus 강제값 (-1=비활성)
    "manual_jump":     False,   # True면 다음 틱에 위치 점프
    "jump_dist":       0.05,    # 수동 점프 거리 (도)
    "fleet_ref":       None,    # 실행 중인 fleet 레퍼런스
}


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
    push(1, 6); push(0, 2); push(mmsi, 30); push(nav_status, 4); push(0, 8)
    push(int(round(sog * 10)) & 0x3FF, 10); push(1, 1)
    push(int(round(lon * 600000)) & 0xFFFFFFF, 28)
    push(int(round(lat * 600000)) & 0x7FFFFFF, 27)
    push(int(round(cog * 10)) & 0xFFF, 12)
    push(heading % 360, 9); push(int(time.time()) % 60, 6)
    push(0, 2); push(0, 3); push(0, 1); push(0, 19)
    payload = encode_payload(bits)
    body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{body}*{nmea_checksum(body)}\r\n"

def build_vsd(mmsi: int, vessel_name: str) -> str:
    name = vessel_name[:20].upper().ljust(20, "@")
    bits: list[int] = []
    def push(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)
    def push_str(value: str, width: int) -> None:
        for ch in value[:width]:
            code = ord(ch)
            if code >= 64: code -= 64
            push(code, 6)
    push(24, 6); push(0, 2); push(mmsi, 30); push(0, 2)
    push_str(name, 20); push(0, 8)
    payload = encode_payload(bits)
    body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{body}*{nmea_checksum(body)}\r\n"

def load_nmea(file_path) -> list[str]:
    path = Path(file_path)
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        messages = [
            line.strip() + "\r\n"
            for line in f
            if line.strip().startswith("!AIVDM")
        ]
    if not messages:
        raise ValueError("파일에서 !AIVDM 문장을 찾지 못했습니다.")
    return messages

def translation_offset(cfg: dict, elapsed: float) -> tuple[float, float]:
    move_speed   = float(cfg.get("move_speed", 0.0))
    move_heading = float(cfg.get("move_heading", 0.0))
    move_accel   = float(cfg.get("move_accel", 0.0))
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
        self.lat = 0.0; self.lon = 0.0
        self.sog = 0.0; self.cog = 0.0; self.heading = 0

    def position_message(self) -> str:
        return build_vdm(self.mmsi, self.lat, self.lon,
                         self.sog, self.cog, self.heading, self.nav_status)

    def name_message(self) -> str:
        return build_vsd(self.mmsi, self.name)


# ══════════════════════════════════════════════════
#  패턴 함수들
# ══════════════════════════════════════════════════

# ──────────────────────────────────────────────────
# 기본: 속도 이상
# ──────────────────────────────────────────────────
def make_speed_spike_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg["speed_count"])
    base_speed = float(cfg["speed_base"])
    spike_speed = float(cfg["speed_spike"])
    mode = str(cfg.get("speed_mode", "간헐"))
    interval = max(1.0, float(cfg.get("speed_interval", 10.0)))
    fleet = []
    for i in range(count):
        v = Vessel(990100000 + i, f"GHOST-S{i+1:03d}")
        v.lat = clat + random.uniform(-0.03, 0.03)
        v.lon = clon + random.uniform(-0.036, 0.036)
        v.sog = base_speed; v.cog = random.uniform(0, 360)
        v.heading = int(v.cog)
        v._base_speed = base_speed; v._spike_speed = spike_speed
        v._speed_mode = mode; v._spike_interval = interval
        v._last_spike = 0.0; v._spike_state = False
        fleet.append(v)
    return fleet

def update_speed_spike_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_spike_interval"): continue
        if elapsed - v._last_spike >= v._spike_interval:
            v._last_spike = elapsed
            if v._speed_mode == "간헐":
                v._spike_state = not v._spike_state
            else:
                v._spike_state = True
        v.sog = v._spike_speed if v._spike_state else v._base_speed
        if v._speed_mode == "순간" and v._spike_state:
            v._spike_state = False
        if random.random() < 0.2:
            v.cog = (v.cog + random.uniform(-30, 30)) % 360
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step
        v.heading = int(v.cog)


# ──────────────────────────────────────────────────
# 기본: 정박 이동 이상
# ──────────────────────────────────────────────────
def make_anchor_move_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg["anchor_count"])
    radius = float(cfg["anchor_radius"])
    speed = float(cfg["anchor_speed"])
    cog = float(cfg.get("anchor_cog", 90.0))
    drift = float(cfg.get("anchor_drift", 0.0))
    lon_offset = float(cfg.get("anchor_lon_offset", 0.0))
    fleet = []
    for i in range(count):
        v = Vessel(990500000 + i, f"GHOST-A{i+1:03d}", nav_status=1)
        v.lat = clat + random.uniform(-radius, radius)
        v.lon = clon + random.uniform(-radius*1.2, radius*1.2) + lon_offset
        v.sog = max(0.2, speed)
        v.cog = (cog + random.uniform(-30, 30)) % 360
        v.heading = int((v.cog + 120) % 360)
        v._drift = drift
        fleet.append(v)
    return fleet

def update_anchor_move_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_drift"): continue
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step
        v.lat += v._drift * dt * 0.00001
        v.lon += v._drift * dt * 0.00001
        v.heading = int((v.cog + 120) % 360)


# ──────────────────────────────────────────────────
# 기본: COG/HDG 불일치
# ──────────────────────────────────────────────────
def make_course_mismatch_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg["course_count"])
    mismatch = float(cfg["course_mismatch"])
    speed = float(cfg["course_speed"])
    drift = float(cfg.get("course_drift", 5.0))
    offset = float(cfg.get("course_offset", 120.0))
    fleet = []
    for i in range(count):
        v = Vessel(990600000 + i, f"GHOST-CM{i+1:03d}")
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.06, 0.06)
        v.sog = max(0.5, speed); v.cog = random.uniform(0, 360)
        v.heading = int((v.cog + mismatch + offset) % 360)
        v._drift = drift
        fleet.append(v)
    return fleet

def update_course_mismatch_fleet(fleet, elapsed, dt, cfg):
    mismatch = float(cfg.get("course_mismatch", 150.0))
    offset   = float(cfg.get("course_offset", 120.0))
    for v in fleet:
        if not hasattr(v, "_drift"): continue
        if random.random() < 0.15:
            v.cog = (v.cog + random.uniform(-v._drift, v._drift)) % 360
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step
        v.heading = int((v.cog + mismatch + offset) % 360)


# ──────────────────────────────────────────────────
# 기본: 위치 점프 이상
# ──────────────────────────────────────────────────
def make_position_jump_fleet(cfg):
    clat = float(cfg.get("jump_center_lat", cfg["center_lat"]))
    clon = float(cfg.get("jump_center_lon", cfg["center_lon"]))
    count = int(cfg["jump_count"])
    radius = float(cfg["jump_radius"])
    interval = max(1.0, float(cfg.get("jump_interval", 10.0)))
    fleet = []
    for i in range(count):
        v = Vessel(990700000 + i, f"GHOST-P{i+1:03d}")
        v.lat = clat + random.uniform(-radius, radius)
        v.lon = clon + random.uniform(-radius*1.2, radius*1.2)
        v.sog = random.uniform(2.0, 10.0); v.cog = random.uniform(0, 360)
        v.heading = int(v.cog)
        v._jump_radius = radius; v._jump_interval = interval; v._last_jump = 0.0
        fleet.append(v)
    return fleet

def update_position_jump_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_jump_interval"): continue
        if elapsed - v._last_jump >= v._jump_interval:
            v._last_jump = elapsed
            v.lat += random.choice([-1,1]) * random.uniform(0.08, 0.20)
            v.lon += random.choice([-1,1]) * random.uniform(0.08, 0.20)
            v.cog = random.uniform(0, 360); v.heading = int(v.cog)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step
        v.heading = int(v.cog)


# ──────────────────────────────────────────────────
# 복원: JBU 글자 선단
# ──────────────────────────────────────────────────
def make_jbu_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    scale = float(cfg.get("jbu_scale", 1.0))

    j_pts = [(0.08,0.04),(0.05,0.04),(0.02,0.04),(-0.01,0.04),
             (-0.04,0.03),(-0.06,0.01),(-0.06,-0.02)]
    b_pts = [(0.08,0.0),(0.04,0.0),(0.0,0.0),(-0.04,0.0),(-0.08,0.0),
             (-0.06,0.025),(-0.04,0.04),(-0.02,0.025),(0.0,0.0),
             (0.02,0.025),(0.04,0.04),(0.06,0.025),(0.08,0.0)]
    u_pts = [(0.08,0.0),(0.04,0.0),(0.0,0.0),(-0.04,0.005),
             (-0.07,0.02),(-0.07,0.05),(-0.04,0.06),
             (0.0,0.06),(0.04,0.05),(0.08,0.02)]

    fleet = []
    def make_letter(pts, off, prefix, base_mmsi):
        ships = []
        for idx, (dlat, dlon) in enumerate(pts):
            v = Vessel(base_mmsi + idx, f"{prefix}{idx+1:02d}")
            v.lat = clat + off[0] + dlat*scale
            v.lon = clon + off[1] + dlon*scale
            v._waypoints = [(clat+off[0]+la*scale, clon+off[1]+lo*scale) for la,lo in pts]
            v._base_wp    = list(v._waypoints)
            v._wp_idx = idx % len(pts); v._wp_prog = 0.0
            v.sog = 3.0 + random.uniform(-0.5, 0.5)
            ships.append(v)
        return ships

    fleet += make_letter(j_pts, (-0.12*scale, -0.28*scale), "GHOST-J", 990200000)
    fleet += make_letter(b_pts, (-0.12*scale, -0.06*scale), "GHOST-B", 990300000)
    fleet += make_letter(u_pts, (-0.12*scale,  0.16*scale), "GHOST-U", 990400000)
    return fleet

def update_jbu_fleet(fleet, elapsed, dt, cfg):
    dlat, dlon = translation_offset(cfg, elapsed)
    for v in fleet:
        if not hasattr(v, "_waypoints") or len(v._waypoints) < 2: continue
        v._waypoints = [(bl+dlat, bn+dlon) for bl,bn in v._base_wp]
        wps = v._waypoints
        cur = v._wp_idx % len(wps)
        nxt = (cur+1) % len(wps)
        clat2, clon2 = wps[cur]; nlat, nlon = wps[nxt]
        dist = math.sqrt((nlat-clat2)**2 + (nlon-clon2)**2)
        if dist < 1e-9: v._wp_idx = nxt; continue
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v._wp_prog += step / dist
        if v._wp_prog >= 1.0:
            v._wp_prog = 0.0; v._wp_idx = nxt
            cur, nxt = nxt, (nxt+1)%len(wps)
            clat2, clon2 = wps[cur]; nlat, nlon = wps[nxt]
        p = v._wp_prog
        v.lat = clat2 + (nlat-clat2)*p; v.lon = clon2 + (nlon-clon2)*p
        v.cog = math.degrees(math.atan2(nlon-clon2, nlat-clat2)) % 360
        v.heading = int(v.cog)


# ──────────────────────────────────────────────────
# 복원: 집게 협공 (Pincer)
# ──────────────────────────────────────────────────
def make_pincer_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("pincer_count", 20))
    width = float(cfg.get("pincer_width", 0.5))
    depth = float(cfg.get("pincer_depth", 0.3))
    fleet = []
    half = count // 2
    for i in range(half):
        t = i / max(half-1, 1)
        for side, sign in [("L", -1), ("R", 1)]:
            base_mmsi = 990800000 + (i if side=="L" else half+i)
            v = Vessel(base_mmsi, f"GHOST-P{side}{i+1:02d}")
            v.lat = clat + depth*(1-t); v.lon = clon + sign*width*t
            v._target_lat = clat; v._target_lon = clon
            v.sog = 8.0 + random.uniform(-1, 1)
            v.cog = math.degrees(math.atan2(clon-v.lon, clat-v.lat)) % 360
            v.heading = int(v.cog)
            fleet.append(v)
    return fleet

def update_pincer_fleet(fleet, elapsed, dt, cfg):
    dlat, dlon = translation_offset(cfg, elapsed)
    spd = float(cfg.get("pincer_speed", 8.0))
    for v in fleet:
        if not hasattr(v, "_target_lat"): continue
        tlat = v._target_lat + dlat; tlon = v._target_lon + dlon
        dlat2 = tlat-v.lat; dlon2 = tlon-v.lon
        dist = math.sqrt(dlat2**2 + dlon2**2) + 1e-9
        step = spd * _KN_TO_DEG_PER_SEC * dt
        if dist > 0.001:
            v.lat += (dlat2/dist)*step; v.lon += (dlon2/dist)*step
        v.cog = math.degrees(math.atan2(dlon2, dlat2)) % 360
        v.heading = int(v.cog); v.sog = spd


# ──────────────────────────────────────────────────
# 복원: 파상 대형 (Wave)
# ──────────────────────────────────────────────────
def make_wave_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("wave_count", 24))
    width = float(cfg.get("wave_width", 0.6))
    amplitude = float(cfg.get("wave_amplitude", 0.15))
    lanes = int(cfg.get("wave_lanes", 3))
    fleet = []
    per_lane = count // max(lanes, 1)
    for lane in range(lanes):
        lon_off = clon + (lane - lanes/2) * (width/lanes)
        for i in range(per_lane):
            idx = lane*per_lane + i
            t = i / max(per_lane-1, 1)
            v = Vessel(990900000 + idx, f"GHOST-W{idx+1:02d}")
            v.lat = clat - amplitude*2*t; v.lon = lon_off
            v._wave_phase = (i/per_lane)*2*math.pi + lane*math.pi/lanes
            v._wave_base_lon = lon_off; v._wave_amp = amplitude
            v.sog = 10.0; v.cog = 180.0; v.heading = 180
            fleet.append(v)
    return fleet

def update_wave_fleet(fleet, elapsed, dt, cfg):
    dlat, dlon = translation_offset(cfg, elapsed)
    wave_speed = float(cfg.get("wave_speed", 10.0))
    wave_freq  = float(cfg.get("wave_freq", 0.05))
    for v in fleet:
        if not hasattr(v, "_wave_phase"): continue
        step = wave_speed * _KN_TO_DEG_PER_SEC * 0.1
        v.lat -= step
        lon_off = v._wave_amp * math.sin(v._wave_phase + elapsed*wave_freq)
        v.lon = v._wave_base_lon + lon_off + dlon
        v.lat += dlat * 0.001
        prev_off = v._wave_amp * math.sin(v._wave_phase + (elapsed-0.1)*wave_freq)
        v.cog = (180 + math.degrees(math.atan2(lon_off-prev_off, -step*111000))) % 360
        v.heading = int(v.cog)


# ──────────────────────────────────────────────────
# 신규: 고스트쉽 원형 정지 출현
# 선박들이 원형 배치로 갑자기 나타나서 정지해 있음
# 일정 시간 후 소멸(위치를 유효범위 밖으로 이동)
# ──────────────────────────────────────────────────
def make_ghost_circle_static_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("gc_count", 20))
    radius = float(cfg.get("gc_radius", 0.15))
    appear_interval = float(cfg.get("gc_appear_interval", 30.0))
    vanish_after = float(cfg.get("gc_vanish_after", 20.0))
    fleet = []
    for i in range(count):
        angle = 2*math.pi * i / count
        v = Vessel(991000000 + i, f"GHOST-GC{i+1:02d}", nav_status=1)
        v._circle_lat = clat + math.cos(angle)*radius
        v._circle_lon = clon + math.sin(angle)*radius * 1.2
        # 처음에는 유효범위 밖에 숨겨둠 (위치 점프로 출현)
        v.lat = 0.0; v.lon = 0.0
        v.sog = 0.0; v.cog = 0.0; v.heading = int(angle*180/math.pi) % 360
        v._gc_appear_interval = appear_interval
        v._gc_vanish_after = vanish_after
        v._gc_last_appear = -appear_interval   # 첫 틱에 바로 출현
        v._gc_visible = False
        fleet.append(v)
    return fleet

def update_ghost_circle_static_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_gc_appear_interval"): continue
        since = elapsed - v._gc_last_appear
        if not v._gc_visible:
            if since >= v._gc_appear_interval:
                # 출현
                v.lat = v._circle_lat + random.uniform(-0.002, 0.002)
                v.lon = v._circle_lon + random.uniform(-0.002, 0.002)
                v._gc_last_appear = elapsed
                v._gc_visible = True
        else:
            if since >= v._gc_vanish_after:
                # 소멸: 위치를 유효범위 밖으로 (IDS에는 invalid position 경고만 뜸)
                v.lat = 0.0; v.lon = 0.0
                v._gc_visible = False
                v._gc_last_appear = elapsed


# ──────────────────────────────────────────────────
# 신규: 원형 순찰
# ──────────────────────────────────────────────────
def make_circle_patrol_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("cp_count", 15))
    radius = float(cfg.get("cp_radius", 0.2))
    sog = float(cfg.get("cp_sog", 12.0))
    fleet = []
    for i in range(count):
        init_angle = 2*math.pi * i / count
        v = Vessel(991050000 + i, f"GHOST-CP{i+1:02d}")
        v.lat = clat + math.cos(init_angle)*radius
        v.lon = clon + math.sin(init_angle)*radius * 1.2
        v.sog = sog + random.uniform(-1, 1)
        v.cog = (math.degrees(init_angle) + 90) % 360
        v.heading = int(v.cog)
        v._cp_angle = init_angle
        v._cp_radius = radius
        v._cp_clat = clat; v._cp_clon = clon
        v._cp_speed = v.sog  # 이상속도 주입용
        fleet.append(v)
    return fleet

def update_circle_patrol_fleet(fleet, elapsed, dt, cfg):
    sog_base = float(cfg.get("cp_sog", 12.0))
    spike_sog = float(cfg.get("cp_spike_sog", 0.0))   # 0이면 무이상
    spike_interval = max(1.0, float(cfg.get("cp_spike_interval", 20.0)))
    for v in fleet:
        if not hasattr(v, "_cp_angle"): continue
        # 각속도 계산: speed / circumference * 2pi
        circumference_deg = 2*math.pi * v._cp_radius
        angular_speed = v.sog * _KN_TO_DEG_PER_SEC / (circumference_deg + 1e-9)
        v._cp_angle = (v._cp_angle + angular_speed * dt) % (2*math.pi)
        v.lat = v._cp_clat + math.cos(v._cp_angle) * v._cp_radius
        v.lon = v._cp_clon + math.sin(v._cp_angle) * v._cp_radius * 1.2
        v.cog = (math.degrees(v._cp_angle) + 90) % 360
        v.heading = int(v.cog)
        # 이상속도 주입
        if spike_sog > 0:
            phase = (elapsed % spike_interval) / spike_interval
            v.sog = spike_sog if phase < 0.15 else sog_base


# ──────────────────────────────────────────────────
# 신규: 직선 왕복
# ──────────────────────────────────────────────────
def make_linear_bounce_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("lb_count", 15))
    length = float(cfg.get("lb_length", 0.4))
    heading_deg = float(cfg.get("lb_heading", 0.0))
    sog = float(cfg.get("lb_sog", 10.0))
    fleet = []
    rad = math.radians(heading_deg)
    for i in range(count):
        t = i / max(count-1, 1)
        perp = math.radians(heading_deg + 90)
        spread = (i - count//2) * 0.003
        v = Vessel(991100000 + i, f"GHOST-LB{i+1:02d}")
        v.lat = clat + math.cos(rad)*length*(t-0.5) + math.cos(perp)*spread
        v.lon = clon + math.sin(rad)*length*(t-0.5)*1.2 + math.sin(perp)*spread*1.2
        v.sog = sog + random.uniform(-1, 1)
        v.cog = heading_deg
        v.heading = int(v.cog)
        v._lb_start_lat = clat - math.cos(rad)*length/2
        v._lb_start_lon = clon - math.sin(rad)*length/2 * 1.2
        v._lb_end_lat   = clat + math.cos(rad)*length/2
        v._lb_end_lon   = clon + math.sin(rad)*length/2 * 1.2
        v._lb_forward = (t < 0.5)
        v._lb_sog = v.sog
        fleet.append(v)
    return fleet

def update_linear_bounce_fleet(fleet, elapsed, dt, cfg):
    spike_sog = float(cfg.get("lb_spike_sog", 0.0))
    spike_interval = max(1.0, float(cfg.get("lb_spike_interval", 20.0)))
    for v in fleet:
        if not hasattr(v, "_lb_forward"): continue
        # 이상속도 주입
        if spike_sog > 0:
            phase = (elapsed % spike_interval) / spike_interval
            v.sog = spike_sog if phase < 0.15 else v._lb_sog
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        if v._lb_forward:
            dlat = v._lb_end_lat - v.lat
            dlon = v._lb_end_lon - v.lon
        else:
            dlat = v._lb_start_lat - v.lat
            dlon = v._lb_start_lon - v.lon
        dist = math.sqrt(dlat**2 + dlon**2)
        if dist < step * 2:
            v._lb_forward = not v._lb_forward
        else:
            v.lat += (dlat/dist)*step
            v.lon += (dlon/dist)*step
        v.cog = math.degrees(math.atan2(dlon, dlat)) % 360
        v.heading = int(v.cog)


# ──────────────────────────────────────────────────
# 신규: 실제루트 + 이상속도
# 웨이포인트 체인을 따라 실제 항로처럼 이동하다가
# 특정 구간에서 속도 이상 발생
# ──────────────────────────────────────────────────
_REALISTIC_WAYPOINTS = [
    # (dlat, dlon) — 중심 기준 상대 좌표 (도)
    (-0.20,  0.00),  # 출발 (남서)
    (-0.12,  0.08),  # 항구 출항
    (-0.05,  0.15),  # 협수로
    ( 0.03,  0.20),  # 외항
    ( 0.10,  0.18),  # 해협 통과
    ( 0.15,  0.10),  # 북상
    ( 0.18,  0.00),  # 목적지 접근
    ( 0.15, -0.10),  # 회항 시작
    ( 0.08, -0.18),
    ( 0.00, -0.20),
    (-0.10, -0.15),
    (-0.18, -0.08),
    (-0.20,  0.00),  # 귀항
]

def make_realistic_speed_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("rs_count", 10))
    sog_normal = float(cfg.get("rs_sog_normal", 12.0))
    sog_spike  = float(cfg.get("rs_sog_spike", 35.0))
    spike_wp   = int(cfg.get("rs_spike_wp", 4))    # 이상속도 발생 웨이포인트 인덱스
    fleet = []
    wps = [(clat+dlat, clon+dlon*1.2) for dlat, dlon in _REALISTIC_WAYPOINTS]
    for i in range(count):
        offset = i / max(count, 1)
        # 선박마다 시작 웨이포인트를 조금씩 다르게
        start_wp = int(offset * len(wps)) % len(wps)
        v = Vessel(991200000 + i, f"GHOST-RS{i+1:02d}")
        v.lat, v.lon = wps[start_wp]
        v.lat += random.uniform(-0.005, 0.005)
        v.lon += random.uniform(-0.005, 0.005)
        v.sog = sog_normal; v.cog = 0.0; v.heading = 0
        v._rs_wps = wps
        v._rs_wp_idx = start_wp
        v._rs_prog = 0.0
        v._rs_normal = sog_normal
        v._rs_spike = sog_spike
        v._rs_spike_wp = spike_wp
        fleet.append(v)
    return fleet

def update_realistic_speed_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_rs_wps"): continue
        wps = v._rs_wps
        cur = v._rs_wp_idx % len(wps)
        nxt = (cur+1) % len(wps)
        clat2, clon2 = wps[cur]; nlat, nlon = wps[nxt]
        dist = math.sqrt((nlat-clat2)**2 + (nlon-clon2)**2)
        # 이상속도 구간 체크
        v.sog = v._rs_spike if cur == v._rs_spike_wp else v._rs_normal
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        if dist < 1e-9:
            v._rs_wp_idx = nxt; v._rs_prog = 0.0; continue
        v._rs_prog += step / dist
        if v._rs_prog >= 1.0:
            v._rs_prog = 0.0; v._rs_wp_idx = nxt
            cur = nxt; nxt = (nxt+1)%len(wps)
            clat2, clon2 = wps[cur]; nlat, nlon = wps[nxt]
        p = v._rs_prog
        v.lat = clat2 + (nlat-clat2)*p
        v.lon = clon2 + (nlon-clon2)*p
        v.cog = math.degrees(math.atan2(nlon-clon2, nlat-clat2)) % 360
        v.heading = int(v.cog)


# ══════════════════════════════════════════════════
#  ML 우회 테스트 케이스
# ══════════════════════════════════════════════════

# ──────────────────────────────────────────────────
# [ML-1] Low & Slow
# 모든 탐지 임계값을 동시에 하회하는 "정상처럼 보이는" 이상
# 규칙기반 IDS → 전부 통과
# ML IDS → 동시 하회 자체가 비정상 패턴으로 탐지될 수 있음
# ──────────────────────────────────────────────────
def make_ml_low_slow_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("ml_ls_count", 10))
    fleet = []
    for i in range(count):
        v = Vessel(991300000 + i, f"GHOST-LS{i+1:03d}")
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.05, 0.05)
        v.sog = random.uniform(0.3, 2.0)   # 낮음
        v.cog = random.uniform(0, 360)
        v.heading = int((v.cog + random.uniform(0, 99)) % 360)  # 차이 <100
        v.nav_status = 0
        v._ls_base_sog = v.sog
        v._ls_base_cog = v.cog
        fleet.append(v)
    return fleet

def update_ml_low_slow_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_ls_base_sog"): continue
        # Δsog < 9.9 유지, 위치변화 < 5km/min 유지, COG-HDG < 100 유지
        v.sog = max(0.1, v._ls_base_sog + random.uniform(-0.15, 0.15))
        v.cog = (v._ls_base_cog + random.uniform(-2, 2)) % 360
        v.heading = int((v.cog + random.uniform(50, 95)) % 360)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step


# ──────────────────────────────────────────────────
# [ML-2] Temporal Camouflage
# N개의 정상 메시지 사이에 이상 메시지 1개 삽입
# 규칙기반 IDS → 연속 쌍만 비교하므로 이상 직후 정상이면 탐지 못함
# ML IDS → 윈도우 피처 평균에 묻혀 이상이 희석될 수 있음
# ──────────────────────────────────────────────────
def make_ml_temporal_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("ml_tc_count", 10))
    normal_n = int(cfg.get("ml_tc_normal_n", 8))   # 정상 메시지 수
    fleet = []
    for i in range(count):
        v = Vessel(991400000 + i, f"GHOST-TC{i+1:03d}")
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.05, 0.05)
        v.sog = random.uniform(5.0, 12.0)
        v.cog = random.uniform(0, 360); v.heading = int(v.cog)
        v._tc_counter = 0
        v._tc_normal_n = normal_n
        v._tc_base_sog = v.sog
        v._tc_base_cog = v.cog
        fleet.append(v)
    return fleet

def update_ml_temporal_fleet(fleet, elapsed, dt, cfg):
    anomaly_sog = float(cfg.get("ml_tc_anomaly_sog", 40.0))
    for v in fleet:
        if not hasattr(v, "_tc_counter"): continue
        v._tc_counter += 1
        # 주기적으로 1회 이상 삽입 (COG 급변 + SOG 급증)
        if v._tc_counter % (v._tc_normal_n + 1) == 0:
            v.sog = anomaly_sog
            v.cog = (v._tc_base_cog + 175) % 360   # 거의 반대방향
            v.heading = int((v.cog + 160) % 360)
        else:
            # 정상 거동으로 복귀
            v.sog = v._tc_base_sog + random.uniform(-0.5, 0.5)
            v.cog = (v._tc_base_cog + random.uniform(-5, 5)) % 360
            v.heading = int(v.cog)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step


# ──────────────────────────────────────────────────
# [ML-3] Gradual Drift
# 매 메시지마다 GPS 노이즈 수준(±0.0005도)의 위치 변화를 더함
# 각 스텝은 정상 GPS 오차 범위지만 누적 시 수십km 이동
# 규칙기반: dt≤60에서 dist_km < 5.0 조건 통과
# ML IDS: 누적 드리프트 피처 없으면 탐지 못함
# ──────────────────────────────────────────────────
def make_ml_gradual_drift_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("ml_gd_count", 10))
    fleet = []
    for i in range(count):
        v = Vessel(991500000 + i, f"GHOST-GD{i+1:03d}")
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.05, 0.05)
        v.sog = random.uniform(0.0, 0.5)     # 거의 정지
        v.cog = random.uniform(0, 360); v.heading = int(v.cog)
        v.nav_status = 1  # 정박
        # 드리프트 방향 (고정)
        v._gd_dir = random.uniform(0, 360)
        v._gd_step = float(cfg.get("ml_gd_step", 0.0004))  # 도/업데이트
        fleet.append(v)
    return fleet

def update_ml_gradual_drift_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_gd_dir"): continue
        rad = math.radians(v._gd_dir)
        # 각 스텝은 ~44m (0.0004도), GPS 노이즈 수준
        noise = random.uniform(-v._gd_step*0.3, v._gd_step*0.3)
        v.lat += (math.cos(rad) * v._gd_step + noise)
        v.lon += (math.sin(rad) * v._gd_step * 1.2 + noise)
        # SOG는 거의 0으로 유지 (정박 상태 위장)
        v.sog = random.uniform(0.0, 0.3)


# ──────────────────────────────────────────────────
# [ML-4] Feature Mimicry
# 정상 선박(앵커 선박)의 SOG/COG/navStatus 프로파일을 복사
# 위치만 다른 MMSI로 보냄
# ML이 individual feature만 보면 정상 → fleet-level 분석 필요
# ──────────────────────────────────────────────────
def make_ml_mimicry_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("ml_mm_count", 10))
    fleet = []
    # 모방 대상 프로파일 (실제루트 패턴 흉내)
    profile_sogs = [8.0, 8.2, 8.5, 8.3, 8.1, 7.9, 8.0, 8.2,
                    8.4, 8.3, 8.1, 8.0, 7.8, 8.0]
    for i in range(count):
        v = Vessel(991600000 + i, f"GHOST-MM{i+1:03d}")
        v.lat = clat + random.uniform(-0.1, 0.1)
        v.lon = clon + random.uniform(-0.1, 0.1)
        v.sog = profile_sogs[0]; v.cog = random.uniform(0, 360)
        v.heading = int(v.cog); v.nav_status = 0
        v._mm_profile = profile_sogs
        v._mm_idx = i % len(profile_sogs)
        v._mm_hidden_dir = random.uniform(0, 360)  # 실제 이동 방향 (은닉)
        fleet.append(v)
    return fleet

def update_ml_mimicry_fleet(fleet, elapsed, dt, cfg):
    hidden_sog = float(cfg.get("ml_mm_hidden_sog", 15.0))  # 실제 이동 속도
    for v in fleet:
        if not hasattr(v, "_mm_profile"): continue
        v._mm_idx = (v._mm_idx + 1) % len(v._mm_profile)
        # 보고 SOG = 정상 프로파일
        displayed_sog = v._mm_profile[v._mm_idx]
        v.sog = displayed_sog
        # COG는 천천히 변경 (자연스럽게)
        v.cog = (v.cog + random.uniform(-3, 3)) % 360
        v.heading = int(v.cog)
        # 실제 위치는 hidden_sog와 hidden_dir로 이동
        step = hidden_sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v._mm_hidden_dir)) * step
        v.lon += math.sin(math.radians(v._mm_hidden_dir)) * step


# ══════════════════════════════════════════════════
#  IDS FN 테스트 케이스 (기존 유지)
# ══════════════════════════════════════════════════

def make_blind_dt_jump_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("fn_dt_count", 10))
    jump_dist = float(cfg.get("fn_dt_jump_dist", 0.15))
    fleet = []
    for i in range(count):
        v = Vessel(991700000 + i, f"GHOST-FN1-{i+1:03d}")
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.05, 0.05)
        v.sog = random.uniform(5.0, 12.0); v.cog = random.uniform(0, 360)
        v.heading = int(v.cog); v._fn_jump_dist = jump_dist
        fleet.append(v)
    return fleet

def update_blind_dt_jump_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_fn_jump_dist"): continue
        direction = random.uniform(0, 360); rad = math.radians(direction)
        v.lat += math.cos(rad) * v._fn_jump_dist
        v.lon += math.sin(rad) * v._fn_jump_dist * 1.2
        v.sog = random.choice([2.0, 18.0, 3.0, 20.0])
        v.cog = random.uniform(0, 360); v.heading = int(v.cog)

def make_blind_speed_ramp_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("fn_ramp_count", 10))
    fleet = []
    for i in range(count):
        v = Vessel(991800000 + i, f"GHOST-FN2-{i+1:03d}")
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.05, 0.05)
        v.sog = float(cfg.get("fn_ramp_start", 2.0))
        v.cog = random.uniform(0, 360); v.heading = int(v.cog)
        v._ramp_step  = float(cfg.get("fn_ramp_step", 9.5))
        v._ramp_max   = float(cfg.get("fn_ramp_max", 29.0))
        v._ramp_start = float(cfg.get("fn_ramp_start", 2.0))
        fleet.append(v)
    return fleet

def update_blind_speed_ramp_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_ramp_step"): continue
        v.sog = min(v.sog + v._ramp_step, v._ramp_max)
        if v.sog >= v._ramp_max: v.sog = v._ramp_start
        v.heading = int(v.cog)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step

def make_blind_cog_border_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("fn_cog_count", 10))
    mismatch_deg = float(cfg.get("fn_cog_mismatch", 95.0))
    fleet = []
    for i in range(count):
        v = Vessel(991900000 + i, f"GHOST-FN3-{i+1:03d}")
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.05, 0.05)
        v.sog = random.uniform(3.0, 10.0); v.cog = random.uniform(0, 360)
        v.heading = int((v.cog + mismatch_deg) % 360)
        v._fn_mismatch = mismatch_deg
        fleet.append(v)
    return fleet

def update_blind_cog_border_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_fn_mismatch"): continue
        if random.random() < 0.1: v.cog = (v.cog + random.uniform(-10, 10)) % 360
        v.heading = int((v.cog + v._fn_mismatch) % 360)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step

_UNCHECKED_STATUSES = [2, 3, 7, 8, 11, 12]

def make_blind_nav_status_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count = int(cfg.get("fn_nav_count", 12))
    move_sog = float(cfg.get("fn_nav_sog", 3.0))
    fleet = []
    for i in range(count):
        status = _UNCHECKED_STATUSES[i % len(_UNCHECKED_STATUSES)]
        v = Vessel(992000000 + i, f"GHOST-FN4-{i+1:03d}", nav_status=status)
        v.lat = clat + random.uniform(-0.05, 0.05)
        v.lon = clon + random.uniform(-0.05, 0.05)
        v.sog = move_sog; v.cog = random.uniform(0, 360); v.heading = int(v.cog)
        fleet.append(v)
    return fleet

def update_blind_nav_status_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if random.random() < 0.05: v.cog = (v.cog + random.uniform(-15, 15)) % 360
        v.heading = int(v.cog)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step


# ──────────────────────────────────────────────────
# 앵커 선박
# ──────────────────────────────────────────────────


# ══════════════════════════════════════════════════
#  E. ML 우회 v2 — 구조적 회피 패턴
# ══════════════════════════════════════════════════
#
#  v4 ML 패턴이 여전히 80% 평균 탐지된 원인:
#  ① fleet 전체가 동일 MMSI prefix 블록 (991x00000)
#     → vessel 상관 피처(MMSI clustering)에서 자명
#  ② 모든 선박이 동시에 동일 통계 분포
#     → fleet-level variance/skewness 피처가 이상
#  ③ Gradual Drift: ML window(보통 20-30 샘플) 내
#     누적 변위가 결국 임계 초과
#  ④ Temporal Camouflage: 이상 삽입이 window 중앙
#     → 양쪽 window 중 하나엔 완전 포함
#
#  E 패턴은 위 원인을 구조적으로 해결한다.
# ══════════════════════════════════════════════════


# ──────────────────────────────────────────────────
# E1  Smooth Trajectory  (CTRV 모델 기반)
#
# 탐지 회피 대상:
#   - Kalman 필터 잔차(residual) 기반 이상탐지
#   - trajectory curvature / jerk 피처
#   - position prediction error 피처
#
# 원리:
#   실제 AIS 처리에서 가장 많이 쓰이는 운동 모델은
#   CTRV(Constant Turn Rate & Velocity)다.
#   이 모델을 따르는 궤적은 Kalman 잔차가 0에 가까워
#   residual-based anomaly scorer를 통과한다.
#   단, 선박의 실제 위치를 목표 지점으로 유도하는
#   이상 경로를 CTRV를 유지하면서 구현한다.
# ──────────────────────────────────────────────────
def make_adv_smooth_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count      = int(cfg.get("adv_sm_count", 10))
    sog        = float(cfg.get("adv_sm_sog", 15.0))
    turn_rate  = float(cfg.get("adv_sm_turn_rate", 2.0))  # deg/s
    fleet = []
    for i in range(count):
        v = Vessel(993100000 + i, f"SMTH-{i+1:03d}")
        v.lat = clat + random.uniform(-0.08, 0.08)
        v.lon = clon + random.uniform(-0.08, 0.08)
        v.sog = sog + random.uniform(-1.0, 1.0)
        v.cog = random.uniform(0, 360)
        v.heading = int(v.cog)
        v._sm_omega = turn_rate * random.choice([-1, 1])  # 좌/우 선회
        v._sm_sog   = v.sog
        v._sm_base_lat = v.lat
        v._sm_base_lon = v.lon
        fleet.append(v)
    return fleet

def update_adv_smooth_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_sm_omega"): continue
        # CTRV: COG를 일정 각속도로 변경, SOG 일정 유지
        v.cog = (v.cog + v._sm_omega * dt) % 360
        v.heading = int(v.cog)
        v.sog = v._sm_sog   # 속도 일정 유지
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step * 1.2


# ──────────────────────────────────────────────────
# E2  Fleet Desync  (선단 통계 탈동기화)
#
# 탐지 회피 대상:
#   - fleet-level 상관 피처 (Pearson, Spearman)
#   - MMSI prefix 클러스터링 탐지
#   - 동시 이상 발생 확률 기반 탐지
#
# 원리:
#   현재 모든 ML 패턴은 순차 MMSI 블록을 쓰고,
#   모든 선박이 동시에 동일 통계 분포를 가진다.
#   → fleet-level 피처(공분산, 동시 이상률)가 자명.
#   E2는 선박마다 ① MMSI를 한국/일본/중국 해역의
#   실제 MID 범위에서 분산 선택 ② 개별 SOG 분포
#   ③ 개별 shipType ④ 이상 발생 시각을 각자 다르게
#   설정해 fleet-level 상관을 파괴한다.
# ──────────────────────────────────────────────────
_MID_PREFIXES = [440, 441, 432, 431, 477, 413, 416, 352, 215, 503]
_REAL_SHIP_PROFILES = [
    # (shipType, sog_min, sog_max, nav_status_options)
    (70, 10.0, 16.0, [0]),      # 일반화물
    (71, 10.0, 14.0, [0]),      # 드라이벌크
    (72,  8.0, 12.0, [0]),      # 탱커
    (80, 10.0, 15.0, [0]),      # 탱커(기타)
    (60, 14.0, 22.0, [0]),      # 여객선
    (30,  4.0, 10.0, [0, 7]),   # 어선
    (36,  5.0, 15.0, [0]),      # 요트
    (52,  6.0, 12.0, [0]),      # 예인선
]

def make_adv_desync_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count      = int(cfg.get("adv_ds_count", 12))
    sog_spike  = float(cfg.get("adv_ds_sog_spike", 38.0))
    fleet = []
    used_mmsi = set()
    for i in range(count):
        # 실제 MID 기반 MMSI 생성 (sequential prefix 회피)
        mid = random.choice(_MID_PREFIXES)
        while True:
            suffix = random.randint(100000, 999999)
            mmsi = mid * 1000000 + suffix
            if mmsi not in used_mmsi:
                used_mmsi.add(mmsi)
                break
        profile = random.choice(_REAL_SHIP_PROFILES)
        ship_type, sog_lo, sog_hi, nav_opts = profile
        v = Vessel(mmsi, f"REAL{mmsi % 10000:04d}")
        v.lat = clat + random.uniform(-0.1, 0.1)
        v.lon = clon + random.uniform(-0.1, 0.1)
        v.sog = random.uniform(sog_lo, sog_hi)
        v.cog = random.uniform(0, 360)
        v.heading = int(v.cog + random.uniform(-5, 5)) % 360
        v.nav_status = random.choice(nav_opts)
        # 이상 발생 오프셋: 선박마다 다름 (fleet 동시성 파괴)
        v._ds_anomaly_offset  = random.uniform(0, 45.0)
        v._ds_anomaly_interval= random.uniform(30.0, 90.0)
        v._ds_sog_normal = v.sog
        v._ds_sog_spike  = sog_spike * random.uniform(0.85, 1.15)
        v._ds_profile    = profile
        fleet.append(v)
    return fleet

def update_adv_desync_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_ds_anomaly_offset"): continue
        ship_type, sog_lo, sog_hi, nav_opts = v._ds_profile
        adjusted = elapsed + v._ds_anomaly_offset
        cycle = adjusted % v._ds_anomaly_interval
        anomaly_window = v._ds_anomaly_interval * 0.12
        if cycle < anomaly_window:
            v.sog = v._ds_sog_spike   # 이상 속도
        else:
            v.sog = v._ds_sog_normal + random.uniform(-0.3, 0.3)
        # COG/HDG: 작은 노이즈만 (자연스럽게)
        if random.random() < 0.08:
            v.cog = (v.cog + random.uniform(-8, 8)) % 360
        v.heading = int(v.cog + random.uniform(-4, 4)) % 360
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step * 1.2


# ──────────────────────────────────────────────────
# E3  Window Edge Attack  (슬라이딩 윈도우 경계 공격)
#
# 탐지 회피 대상:
#   - 고정 크기 sliding window 기반 ML 피처 추출기
#   - LSTM / 시계열 이상탐지 (window 내 누적 score)
#
# 원리:
#   ML IDS가 N개 샘플 윈도우로 피처를 추출한다면,
#   이상을 윈도우 경계에 배치하면 각 윈도우에서
#   이상 샘플이 1개씩만 포함되어 anomaly score가
#   낮게 유지된다.
#   일반적 window size: 10~30샘플.
#   → 이상을 window_size-1 간격으로 1회 발생시킨다.
#   직전 ML Temporal(D2)과 차이:
#   D2는 N정상+1이상 고정 사이클,
#   E3은 송신 주기와 window_size로 정확히 타이밍 계산.
# ──────────────────────────────────────────────────
def make_adv_window_edge_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count       = int(cfg.get("adv_we_count", 10))
    window_size = int(cfg.get("adv_we_window", 20))   # 가정 ML window
    anomaly_sog = float(cfg.get("adv_we_sog", 42.0))
    normal_sog  = float(cfg.get("adv_we_normal_sog", 12.0))
    fleet = []
    for i in range(count):
        v = Vessel(993300000 + i, f"WEDGE-{i+1:03d}")
        v.lat = clat + random.uniform(-0.06, 0.06)
        v.lon = clon + random.uniform(-0.06, 0.06)
        v.sog = normal_sog; v.cog = random.uniform(0, 360)
        v.heading = int(v.cog)
        v._we_window   = window_size
        v._we_anom_sog = anomaly_sog
        v._we_norm_sog = normal_sog
        v._we_tick     = 0   # 메시지 카운터
        fleet.append(v)
    return fleet

def update_adv_window_edge_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_we_window"): continue
        v._we_tick += 1
        # window_size-1 마다 딱 1회 이상 (window 경계에 1개)
        cycle = v._we_tick % v._we_window
        if cycle == v._we_window - 1:
            # 이상: SOG 급증 + COG 반전 (한 틱만)
            v.sog = v._we_anom_sog
            v.cog = (v.cog + 175 + random.uniform(-3, 3)) % 360
        else:
            v.sog = v._we_norm_sog + random.uniform(-0.4, 0.4)
            # COG: 서서히 원래 방향으로 복귀
            if cycle == 0:
                v.cog = (v.cog + 175 + random.uniform(-3, 3)) % 360  # 정상 복귀
        v.heading = int(v.cog)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step * 1.2


# ──────────────────────────────────────────────────
# E4  Contextual Blend  (어선 맥락 위장)
#
# 탐지 회피 대상:
#   - ship-type별 SOG 분포 모델 (어선 maxSOG=15kn)
#   - behavioral context classifier (조업 vs 항해)
#   - navStatus 일관성 검사
#
# 원리:
#   shipType=30(어선), navStatus=7(어로작업중)으로
#   선언하면 IDS 규칙은 maxSOG=15kn을 적용하고,
#   ML도 어선 클러스터 모델로 평가한다.
#   실제 조업 패턴은: 저속 전진 → 변침 → 멈춤 →
#   소속 어장 내에서 불규칙 이동이므로,
#   이 패턴을 흉내 내면서 점진적으로 목표 위치로
#   이동하거나 허위 위치를 전송한다.
# ──────────────────────────────────────────────────
_FISHING_PHASES = [
    ("net_out",    6.0,  120, 0.0,   0),    # 그물 투망: 저속 직진, navStatus=0
    ("trawling",   4.0,  180, 15.0,  7),    # 예인: 4kn, 천천히 선회, navStatus=7
    ("hauling",    2.0,   90, 0.0,   7),    # 양망: 거의 정지
    ("transit",   10.0,   60, 0.0,   0),    # 이동: 10kn
    ("drifting",   0.5,  150, 5.0,   7),    # 표류
]

def make_adv_contextual_fleet(cfg):
    clat, clon = float(cfg["center_lat"]), float(cfg["center_lon"])
    count       = int(cfg.get("adv_ct_count", 10))
    drift_dir   = float(cfg.get("adv_ct_drift_dir", 45.0))  # 실제 침투 방향
    drift_sog   = float(cfg.get("adv_ct_drift_sog", 3.0))   # 실제 이동 속도
    fleet = []
    for i in range(count):
        v = Vessel(440300000 + random.randint(10000, 99999), f"KFISH{i+1:03d}")
        v.lat = clat + random.uniform(-0.08, 0.08)
        v.lon = clon + random.uniform(-0.08, 0.08)
        v.sog = 4.0; v.cog = random.uniform(0, 360)
        v.heading = int(v.cog); v.nav_status = 7
        # 각 선박의 조업 위상 개별화 (fleet 동기성 파괴)
        v._ct_phase_idx     = i % len(_FISHING_PHASES)
        v._ct_phase_elapsed = random.uniform(0, _FISHING_PHASES[v._ct_phase_idx][2])
        v._ct_drift_dir     = drift_dir + random.uniform(-10, 10)
        v._ct_drift_sog     = drift_sog
        fleet.append(v)
    return fleet

def update_adv_contextual_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_ct_phase_idx"): continue
        phase_name, sog, duration, turn_dps, nav_st = _FISHING_PHASES[v._ct_phase_idx]
        v._ct_phase_elapsed += dt
        if v._ct_phase_elapsed >= duration:
            v._ct_phase_elapsed = 0.0
            v._ct_phase_idx = (v._ct_phase_idx + 1) % len(_FISHING_PHASES)
            phase_name, sog, duration, turn_dps, nav_st = _FISHING_PHASES[v._ct_phase_idx]
        # 보고 거동: 어선 조업 패턴
        v.nav_status = nav_st
        v.sog = sog + random.uniform(-0.3, 0.3)
        if turn_dps > 0:
            v.cog = (v.cog + turn_dps * dt * 0.5) % 360
        v.heading = int(v.cog + random.uniform(-5, 5)) % 360
        # 실제 위치: drift 방향으로 서서히 이동 (보고 sog와 무관)
        real_step = v._ct_drift_sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v._ct_drift_dir)) * real_step
        v.lon += math.sin(math.radians(v._ct_drift_dir)) * real_step * 1.2


# ──────────────────────────────────────────────────
# E5  Shadow Vessel  (실존 선박 프로파일 위장)
#
# 탐지 회피 대상:
#   - MMSI 유효성 / MID 국가 코드 검사
#   - ship-type별 SOG 분포 모델
#   - trajectory plausibility (route conformance)
#   - MMSI uniqueness 검사 (duplicate AIS)
#
# 원리:
#   한국(440/441) MID + 연안화물선 프로파일로
#   AIS 형식상 완전히 합법적인 신호를 생성.
#   SOG는 해당 선종 정상 분포 내, COG는 실제
#   해상 항로 방향(남북/동서 ±30°)으로 제한,
#   위치는 목표 좌표를 향해 자연스럽게 접근.
#   규칙 IDS: MMSI 유효 + SOG 정상 + navStatus 정상
#            → 전 항목 통과
#   ML IDS:   정상 화물선 클러스터 내 위치
#            → behavioral 피처로만 탐지 가능
# ──────────────────────────────────────────────────
_COASTAL_ROUTES = [0, 180, 90, 270, 45, 135, 225, 315]  # 주요 항로 방향

def make_adv_shadow_fleet(cfg):
    clat, clon   = float(cfg["center_lat"]), float(cfg["center_lon"])
    count        = int(cfg.get("adv_sh_count", 8))
    target_lat   = float(cfg.get("adv_sh_target_lat", cfg["center_lat"]))
    target_lon   = float(cfg.get("adv_sh_target_lon", cfg["center_lon"]))
    approach_sog = float(cfg.get("adv_sh_approach_sog", 12.0))
    fleet = []
    # 목표로부터 바깥쪽 진입 위치 계산
    for i in range(count):
        angle_out = 2 * math.pi * i / count
        start_dist = 0.35  # ~38km 밖에서 출발
        start_lat = target_lat + math.cos(angle_out) * start_dist
        start_lon = target_lon + math.sin(angle_out) * start_dist * 1.2
        # 한국 MID 실제 범위: 440xxxxxx, 441xxxxxx
        mid = random.choice([440, 441])
        mmsi = mid * 1000000 + random.randint(100000, 999999)
        v = Vessel(mmsi, f"KR{mmsi % 100000:05d}")
        v.lat = start_lat; v.lon = start_lon
        # 가장 가까운 정규 항로 방향으로 COG 설정
        to_target = math.degrees(math.atan2(target_lon-start_lon,
                                            target_lat-start_lat)) % 360
        nearest_route = min(_COASTAL_ROUTES,
                            key=lambda r: min(abs(r-to_target), 360-abs(r-to_target)))
        # SOG: 연안화물선 정상 범위 (11~15kn)
        v.sog = approach_sog + random.uniform(-1.5, 1.5)
        v.cog = to_target + random.uniform(-8, 8)  # 항로 ±8°
        v.heading = int(v.cog + random.uniform(-3, 3)) % 360
        v.nav_status = 0
        v._sh_target_lat = target_lat
        v._sh_target_lon = target_lon
        v._sh_approach_sog = approach_sog
        v._sh_route_cog  = to_target
        fleet.append(v)
    return fleet

def update_adv_shadow_fleet(fleet, elapsed, dt, cfg):
    for v in fleet:
        if not hasattr(v, "_sh_target_lat"): continue
        dlat = v._sh_target_lat - v.lat
        dlon = v._sh_target_lon - v.lon
        dist = math.sqrt(dlat**2 + dlon**2)
        if dist < 0.005:
            # 목표 도달: 부근 표류 (anchorage 행동 위장)
            v.sog = random.uniform(0.0, 0.4)
            v.nav_status = random.choice([0, 5])  # 항해 or 계류
            v.lat += random.uniform(-0.001, 0.001)
            v.lon += random.uniform(-0.001, 0.001)
            return
        # 항로 방향 유지하면서 목표 접근
        target_cog = math.degrees(math.atan2(dlon, dlat)) % 360
        # 실제 항로 방향에서 크게 벗어나지 않도록 weighted blend
        v.cog = (0.85 * v.cog + 0.15 * target_cog) % 360
        v.heading = int(v.cog + random.uniform(-3, 3)) % 360
        v.sog = v._sh_approach_sog + random.uniform(-0.5, 0.5)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v.lat += math.cos(math.radians(v.cog)) * step
        v.lon += math.sin(math.radians(v.cog)) * step * 1.2


def make_anchor_vessel(cfg) -> Vessel:
    v = Vessel(440123456, "BUSAN ANCHOR", nav_status=1)
    v.lat = float(cfg["center_lat"]); v.lon = float(cfg["center_lon"])
    v.sog = 0.0; v.cog = 0.0; v.heading = 45
    return v


# ──────────────────────────────────────────────────
# 디스패처
# ──────────────────────────────────────────────────
_BUILDERS = {
    "speed_spike":         make_speed_spike_fleet,
    "anchor_move":         make_anchor_move_fleet,
    "course_mismatch":     make_course_mismatch_fleet,
    "position_jump":       make_position_jump_fleet,
    "jbu_fleet":           make_jbu_fleet,
    "pincer":              make_pincer_fleet,
    "wave":                make_wave_fleet,
    "ghost_circle_static": make_ghost_circle_static_fleet,
    "circle_patrol":       make_circle_patrol_fleet,
    "linear_bounce":       make_linear_bounce_fleet,
    "realistic_speed":     make_realistic_speed_fleet,
    "ml_low_slow":         make_ml_low_slow_fleet,
    "ml_temporal":         make_ml_temporal_fleet,
    "ml_gradual_drift":    make_ml_gradual_drift_fleet,
    "ml_mimicry":          make_ml_mimicry_fleet,
    "blind_dt_jump":       make_blind_dt_jump_fleet,
    "blind_speed_ramp":    make_blind_speed_ramp_fleet,
    "blind_cog_border":    make_blind_cog_border_fleet,
    "blind_nav_status":    make_blind_nav_status_fleet,
    "adv_smooth":          make_adv_smooth_fleet,
    "adv_desync":          make_adv_desync_fleet,
    "adv_window_edge":     make_adv_window_edge_fleet,
    "adv_contextual":      make_adv_contextual_fleet,
    "adv_shadow":          make_adv_shadow_fleet,
}

def build_generated_fleet(cfg) -> list[Vessel]:
    key = str(cfg["attack_key"])
    if key not in _BUILDERS:
        raise ValueError(f"지원하지 않는 패턴: {key}")
    fleet = _BUILDERS[key](cfg)
    if cfg.get("add_anchor"):
        fleet.append(make_anchor_vessel(cfg))
    return fleet

def update_generated_fleet(fleet, attack_key, elapsed, dt, cfg):
    k = attack_key
    if   k == "speed_spike":         update_speed_spike_fleet(fleet, elapsed, dt, cfg)
    elif k == "anchor_move":         update_anchor_move_fleet(fleet, elapsed, dt, cfg)
    elif k == "course_mismatch":     update_course_mismatch_fleet(fleet, elapsed, dt, cfg)
    elif k == "position_jump":       update_position_jump_fleet(fleet, elapsed, dt, cfg)
    elif k == "jbu_fleet":           update_jbu_fleet(fleet, elapsed, dt, cfg)
    elif k == "pincer":              update_pincer_fleet(fleet, elapsed, dt, cfg)
    elif k == "wave":                update_wave_fleet(fleet, elapsed, dt, cfg)
    elif k == "ghost_circle_static": update_ghost_circle_static_fleet(fleet, elapsed, dt, cfg)
    elif k == "circle_patrol":       update_circle_patrol_fleet(fleet, elapsed, dt, cfg)
    elif k == "linear_bounce":       update_linear_bounce_fleet(fleet, elapsed, dt, cfg)
    elif k == "realistic_speed":     update_realistic_speed_fleet(fleet, elapsed, dt, cfg)
    elif k == "ml_low_slow":         update_ml_low_slow_fleet(fleet, elapsed, dt, cfg)
    elif k == "ml_temporal":         update_ml_temporal_fleet(fleet, elapsed, dt, cfg)
    elif k == "ml_gradual_drift":    update_ml_gradual_drift_fleet(fleet, elapsed, dt, cfg)
    elif k == "ml_mimicry":          update_ml_mimicry_fleet(fleet, elapsed, dt, cfg)
    elif k == "blind_dt_jump":       update_blind_dt_jump_fleet(fleet, elapsed, dt, cfg)
    elif k == "blind_speed_ramp":    update_blind_speed_ramp_fleet(fleet, elapsed, dt, cfg)
    elif k == "blind_cog_border":    update_blind_cog_border_fleet(fleet, elapsed, dt, cfg)
    elif k == "blind_nav_status":    update_blind_nav_status_fleet(fleet, elapsed, dt, cfg)
    elif k == "adv_smooth":          update_adv_smooth_fleet(fleet, elapsed, dt, cfg)
    elif k == "adv_desync":          update_adv_desync_fleet(fleet, elapsed, dt, cfg)
    elif k == "adv_window_edge":     update_adv_window_edge_fleet(fleet, elapsed, dt, cfg)
    elif k == "adv_contextual":      update_adv_contextual_fleet(fleet, elapsed, dt, cfg)
    elif k == "adv_shadow":          update_adv_shadow_fleet(fleet, elapsed, dt, cfg)


# ──────────────────────────────────────────────────
# RT 오버라이드 적용 (실시간 조작 반영)
# ──────────────────────────────────────────────────
def apply_rt_overrides(fleet: list[Vessel]) -> None:
    if not rt_state.get("active"):
        return
    sog_mult   = float(rt_state.get("sog_mult", 1.0))
    cog_offset = float(rt_state.get("cog_offset", 0.0))
    scatter    = float(rt_state.get("pos_scatter", 0.0))
    nav_ov     = int(rt_state.get("nav_override", -1))

    # 수동 점프
    do_jump = rt_state.get("manual_jump", False)
    if do_jump:
        rt_state["manual_jump"] = False
        jdist = float(rt_state.get("jump_dist", 0.05))
        for v in fleet:
            direction = random.uniform(0, 360)
            v.lat += math.cos(math.radians(direction)) * jdist
            v.lon += math.sin(math.radians(direction)) * jdist * 1.2

    for v in fleet:
        v.sog = max(0.0, v.sog * sog_mult)
        v.cog = (v.cog + cog_offset) % 360
        v.heading = (v.heading + int(cog_offset)) % 360
        if scatter > 0:
            v.lat += random.uniform(-scatter, scatter)
            v.lon += random.uniform(-scatter, scatter) * 1.2
        if nav_ov >= 0:
            v.nav_status = nav_ov


# ──────────────────────────────────────────────────
# 송신 루프
# ──────────────────────────────────────────────────
def send_generated_loop(cfg, log_q, stop_signal: threading.Event) -> bool:
    host = str(cfg["host"]); port = int(cfg["port"])
    interval = float(cfg["interval"])
    attack_key = str(cfg["attack_key"])
    attack_label = str(cfg["attack_label"])

    fleet = build_generated_fleet(cfg)
    rt_state["fleet_ref"] = fleet
    name_sent: set[int] = set()
    iteration = 0
    start_time = time.time()

    queue_log(log_q, f"[생성 시작] 패턴: {attack_label} | 선박 {len(fleet)}척", "start")
    queue_log(log_q, f"[생성 전송] {host}:{port} | 주기 {interval:.2f}s", "info")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not stop_signal.is_set():
            iteration += 1
            cycle_start = time.time()
            elapsed = cycle_start - start_time

            update_generated_fleet(fleet, attack_key, elapsed, interval, cfg)
            apply_rt_overrides(fleet)

            sent = 0
            for v in fleet:
                if stop_signal.is_set(): return False
                if v.mmsi not in name_sent:
                    sock.sendto(v.name_message().encode("ascii"), (host, port))
                    name_sent.add(v.mmsi)
                    if not sleep_with_event(stop_signal, 0.01): return False
                sock.sendto(v.position_message().encode("ascii"), (host, port))
                sent += 1
                if not sleep_with_event(stop_signal, 0.005): return False

            elapsed2 = time.time() - cycle_start
            if iteration == 1 or iteration % 5 == 0:
                queue_log(log_q,
                    f"[생성] {iteration}회차 | {sent}건 | {elapsed2:.2f}s"
                    + (" | RT활성" if rt_state.get("active") else ""), "info")
            if not sleep_with_event(stop_signal, max(0.0, interval - elapsed2)):
                return False
    rt_state["fleet_ref"] = None
    return False


def send_file_loop(cfg, log_q, stop_signal: threading.Event) -> bool:
    host = str(cfg["host"]); port = int(cfg["port"])
    file_path = Path(str(cfg["file_path"]))
    interval = float(cfg["file_interval"])
    repeat = bool(cfg["file_repeat"])
    messages = load_nmea(file_path)
    queue_log(log_q, f"[파일 시작] {file_path.name} | {len(messages)}개", "start")
    sent_count = 0; cycle = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not stop_signal.is_set():
            cycle += 1
            for idx, msg in enumerate(messages, 1):
                if stop_signal.is_set(): return False
                sock.sendto(msg.encode("ascii"), (host, port))
                sent_count += 1
                queue_log(log_q, f"[파일 {idx:04d}] {msg.strip()}", "info")
                if not sleep_with_event(stop_signal, interval): return False
            if not repeat:
                queue_log(log_q, f"[파일 완료] 총 {sent_count}건", "start")
                return True
            queue_log(log_q, f"[파일 반복] {cycle}회차 완료", "info")
    return False


def sender_worker(channel, cfg, log_q, stop_signal):
    completed = False
    try:
        if channel == "generated":
            completed = send_generated_loop(cfg, log_q, stop_signal)
        else:
            completed = send_file_loop(cfg, log_q, stop_signal)
    except Exception as exc:
        queue_log(log_q, f"[오류] {exc}", "error")
    finally:
        label = "생성" if channel == "generated" else "파일"
        if stop_signal.is_set():
            queue_log(log_q, f"[{label} 종료] 사용자 중단", "start")
        elif completed:
            queue_log(log_q, f"[{label} 종료] 완료", "start")
        else:
            queue_log(log_q, f"[{label} 종료] 스레드 종료", "start")
        queue_channel_state(log_q, channel, "finished")


# ══════════════════════════════════════════════════
#  실시간 조작 창
# ══════════════════════════════════════════════════

class RealTimeControlWindow(tk.Toplevel):
    """
    송신 스레드와 rt_state 딕셔너리를 통해 실시간으로 선단을 조작.
    별도 창으로 열리며 메인 창과 독립적으로 작동.
    """
    BG = "#09111d"; ACCENT = "#ff9f44"; FG = "#edf4ff"; SUB = "#9db0c7"
    ENTRY_BG = "#172334"

    def __init__(self, master):
        super().__init__(master)
        self.title("🎮 실시간 조작 패널  |  RT Control")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(400, 580)

        # 슬라이더 변수
        self.v_active      = tk.BooleanVar(value=False)
        self.v_sog_mult    = tk.DoubleVar(value=1.0)
        self.v_cog_offset  = tk.DoubleVar(value=0.0)
        self.v_pos_scatter = tk.DoubleVar(value=0.0)
        self.v_nav_ov      = tk.IntVar(value=-1)
        self.v_jump_dist   = tk.DoubleVar(value=0.05)

        # 트레이스 등록
        for var in (self.v_active, self.v_sog_mult, self.v_cog_offset,
                    self.v_pos_scatter, self.v_nav_ov, self.v_jump_dist):
            var.trace_add("write", self._sync_rt_state)

        self._build_ui()
        self._sync_rt_state()

    def _lbl(self, parent, text, sub=False):
        style = {"bg": self.BG, "fg": self.SUB if sub else self.FG,
                 "font": ("Consolas", 9 if sub else 10)}
        tk.Label(parent, text=text, **style).pack(anchor="w", padx=16, pady=(4,0))

    def _slider_row(self, parent, label, var, from_, to, resolution=0.01, fmt="{:.2f}"):
        row = tk.Frame(parent, bg=self.BG)
        row.pack(fill="x", padx=14, pady=3)
        tk.Label(row, text=label, bg=self.BG, fg=self.SUB,
                 font=("Consolas", 9), width=22, anchor="w").pack(side="left")
        val_lbl = tk.Label(row, text=fmt.format(var.get()),
                           bg=self.BG, fg=self.ACCENT, font=("Consolas", 10, "bold"), width=7)
        val_lbl.pack(side="right")

        def on_change(*_):
            val_lbl.config(text=fmt.format(var.get()))

        slider = tk.Scale(row, variable=var, from_=from_, to=to,
                          resolution=resolution, orient="horizontal",
                          bg=self.BG, fg=self.FG, activebackground=self.ACCENT,
                          highlightthickness=0, troughcolor=self.ENTRY_BG,
                          sliderlength=18, command=lambda _: on_change())
        slider.pack(side="left", fill="x", expand=True, padx=(6, 4))
        return slider

    def _build_ui(self):
        tk.Label(self, text="  REAL-TIME CONTROL  |  실시간 조작",
                 bg=self.ACCENT, fg="#000000",
                 font=("Consolas", 12, "bold"), padx=10, pady=7).pack(fill="x")
        tk.Label(self, text="  현재 실행 중인 선단에 즉시 반영됩니다",
                 bg="#1a2030", fg=self.SUB, font=("Consolas", 8), pady=3).pack(fill="x")

        # 활성화 토글
        row0 = tk.Frame(self, bg=self.BG)
        row0.pack(fill="x", padx=14, pady=(12, 4))
        self.activate_btn = tk.Button(
            row0, textvariable=tk.StringVar(),
            bg="#24354d", fg=self.FG,
            font=("Consolas", 11, "bold"), relief="flat", cursor="hand2",
            padx=12, pady=6, command=self._toggle_active)
        self.activate_btn.pack(fill="x")

        tk.Frame(self, height=1, bg="#24354d").pack(fill="x", padx=10, pady=8)

        # SOG 배율
        self._lbl(self, "SOG 배율  (1.0 = 원래 속도)")
        self._slider_row(self, "sog_mult", self.v_sog_mult, 0.0, 5.0, 0.05, "{:.2f}×")

        # COG 오프셋
        self._lbl(self, "COG 오프셋  (도, 전체 선단 회전)")
        self._slider_row(self, "cog_offset", self.v_cog_offset, -180.0, 180.0, 1.0, "{:+.0f}°")

        # 위치 노이즈
        self._lbl(self, "위치 노이즈 반경  (도, GPS 스캐터 시뮬)")
        self._slider_row(self, "pos_scatter", self.v_pos_scatter, 0.0, 0.5, 0.001, "{:.3f}°")

        # navStatus 강제
        self._lbl(self, "navStatus 강제  (-1=비활성)")
        self._slider_row(self, "nav_override", self.v_nav_ov, -1, 15, 1, "{:.0f}")

        tk.Frame(self, height=1, bg="#24354d").pack(fill="x", padx=10, pady=8)

        # 수동 점프
        self._lbl(self, "수동 위치 점프")
        self._slider_row(self, "jump_dist (도)", self.v_jump_dist, 0.01, 0.5, 0.01, "{:.2f}°")

        self.jump_btn = tk.Button(
            self, text="⚡ 즉시 위치 점프 (전체 선단)",
            bg="#3d1a1a", fg="#ff6b6b",
            font=("Consolas", 11, "bold"), relief="flat", cursor="hand2",
            padx=12, pady=7, command=self._trigger_jump)
        self.jump_btn.pack(fill="x", padx=14, pady=(4, 6))

        tk.Frame(self, height=1, bg="#24354d").pack(fill="x", padx=10, pady=4)

        # 상태 표시
        self.status_lbl = tk.Label(
            self, text="● 비활성 — 송신 스레드에 영향 없음",
            bg=self.BG, fg="#556677",
            font=("Consolas", 9), pady=6)
        self.status_lbl.pack(fill="x", padx=16)

        # 리셋 버튼
        tk.Button(self, text="초기화 (Reset All)",
                  bg="#172334", fg=self.SUB,
                  font=("Consolas", 10), relief="flat", cursor="hand2",
                  padx=8, pady=4, command=self._reset).pack(fill="x", padx=14, pady=(4, 12))

        self._update_active_btn()

    def _toggle_active(self):
        self.v_active.set(not self.v_active.get())
        self._update_active_btn()
        self._sync_rt_state()

    def _update_active_btn(self):
        if self.v_active.get():
            self.activate_btn.config(
                text="● 활성화됨 — 클릭하여 비활성화",
                bg="#1a3d1a", fg="#44ff88")
            self.status_lbl.config(
                text="● 활성 — 다음 틱부터 선단에 반영",
                fg="#44ff88")
        else:
            self.activate_btn.config(
                text="○ 비활성 — 클릭하여 활성화",
                bg="#24354d", fg=self.FG)
            self.status_lbl.config(
                text="● 비활성 — 송신 스레드에 영향 없음",
                fg="#556677")

    def _sync_rt_state(self, *_):
        rt_state["active"]       = bool(self.v_active.get())
        rt_state["sog_mult"]     = float(self.v_sog_mult.get())
        rt_state["cog_offset"]   = float(self.v_cog_offset.get())
        rt_state["pos_scatter"]  = float(self.v_pos_scatter.get())
        rt_state["nav_override"] = int(self.v_nav_ov.get())
        rt_state["jump_dist"]    = float(self.v_jump_dist.get())

    def _trigger_jump(self):
        rt_state["manual_jump"] = True
        self.jump_btn.config(bg="#6b1a1a")
        self.after(300, lambda: self.jump_btn.config(bg="#3d1a1a"))

    def _reset(self):
        self.v_active.set(False)
        self.v_sog_mult.set(1.0)
        self.v_cog_offset.set(0.0)
        self.v_pos_scatter.set(0.0)
        self.v_nav_ov.set(-1)
        self.v_jump_dist.set(0.05)
        rt_state["manual_jump"] = False
        self._update_active_btn()


# ══════════════════════════════════════════════════
#  GUI 메인
# ══════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OpenCPN IDS Signal Generator  v5")
        self.configure(bg="#09111d")
        self.minsize(1100, 720)
        self.resizable(True, True)

        self.generated_thread: threading.Thread | None = None
        self.file_thread: threading.Thread | None = None
        self.generated_stop_event = threading.Event()
        self.file_stop_event = threading.Event()
        self.rt_window: RealTimeControlWindow | None = None

        self._setup_styles()
        self._build_ui()
        self._set_channel_state("generated", False)
        self._set_channel_state("file", False)
        self._on_attack_change()
        self._poll_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 스타일 ─────────────────────────────────────
    def _setup_styles(self) -> None:
        s = ttk.Style(self); s.theme_use("clam")
        bg, accent, fg = "#09111d", "#35d0ff", "#edf4ff"
        sub, ebg, hi = "#9db0c7", "#172334", "#24354d"
        s.configure(".", background=bg, foreground=fg, font=("Consolas", 10))
        s.configure("TFrame", background=bg)
        s.configure("TLabel", background=bg, foreground=fg, font=("Consolas", 10))
        s.configure("Header.TLabel", background=bg, foreground=accent, font=("Consolas", 12, "bold"))
        s.configure("Sub.TLabel", background=bg, foreground=sub, font=("Consolas", 9))
        s.configure("Accent.TLabel", background=bg, foreground="#ffcc44", font=("Consolas", 10, "bold"))
        s.configure("ML.TLabel", background=bg, foreground="#ff9f44", font=("Consolas", 10, "bold"))
        s.configure("ADV.TLabel", background=bg, foreground="#ff4488", font=("Consolas", 10, "bold"))
        s.configure("TEntry", fieldbackground=ebg, foreground="#ffffff",
                    insertcolor=accent, borderwidth=0)
        s.configure("TSpinbox", fieldbackground=ebg, foreground="#ffffff",
                    background=ebg, arrowcolor=accent, borderwidth=0)
        s.configure("TCombobox", fieldbackground=ebg, foreground="#ffffff",
                    selectbackground=accent, selectforeground=bg)
        s.map("TCombobox", fieldbackground=[("readonly", ebg)],
              foreground=[("readonly", "#ffffff")])
        s.configure("TCheckbutton", background=bg, foreground=fg, font=("Consolas", 10))
        s.map("TCheckbutton", background=[("active", bg)])
        s.configure("TSeparator", background=hi)

    # ── 공통 빌더 ───────────────────────────────────
    def _section(self, parent, title, style="Header.TLabel"):
        f = ttk.Frame(parent); f.pack(fill="x", padx=10, pady=(10, 2))
        ttk.Label(f, text=title, style=style).pack(anchor="w")
        tk.Frame(parent, height=1, bg="#24354d").pack(fill="x", padx=10, pady=(0, 6))

    def _row(self, parent, label, factory, **kw):
        row = ttk.Frame(parent); row.pack(fill="x", padx=16, pady=2)
        ttk.Label(row, text=label, width=26, anchor="w",
                  style="Sub.TLabel").pack(side="left")
        w = factory(row, **kw)
        w.pack(side="left", fill="x", expand=True)
        return w

    def _entry(self, parent, default="", **kw):
        var = tk.StringVar(value=str(default))
        e = ttk.Entry(parent, textvariable=var, **kw)
        e._var = var; return e

    def _spin(self, parent, from_, to, default, step=1.0):
        var = tk.DoubleVar(value=default)
        s = ttk.Spinbox(parent, from_=from_, to=to, increment=step,
                        textvariable=var, font=("Consolas", 10))
        s._var = var; return s

    def _combo(self, parent, values, default):
        var = tk.StringVar(value=default)
        c = ttk.Combobox(parent, textvariable=var, values=values,
                         state="readonly", font=("Consolas", 10))
        c._var = var; return c

    # ── UI 빌드 ─────────────────────────────────────
    def _build_ui(self) -> None:
        # 타이틀바
        tk.Label(self, text="  OPENCPN IDS SIGNAL GENERATOR  v5",
                 bg="#35d0ff", fg="#08101a",
                 font=("Consolas", 14, "bold"), padx=10, pady=8).pack(fill="x")
        tk.Label(self,
                 text="  AIS NMEA 0183 UDP Sender  |  Ghost Fleet Simulator  "
                      "|  ML 우회 테스트  |  실시간 조작",
                 bg="#112033", fg="#8aa1bb",
                 font=("Consolas", 9), padx=10, pady=3).pack(fill="x")

        main = ttk.Frame(self); main.pack(fill="both", expand=True)

        # 좌측 패널 (스크롤)
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        tk.Frame(main, width=1, bg="#24354d").pack(side="left", fill="y")

        # 우측 패널 (로그) — 고정 너비 비율
        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True)

        # ── 스크롤 캔버스 (고정 너비 제거, fill=both) ──
        canvas = tk.Canvas(left, bg="#09111d", highlightthickness=0)
        vbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        hbar = ttk.Scrollbar(left, orient="horizontal", command=canvas.xview)

        self.scroll_frame = ttk.Frame(canvas)
        self._sf_window = canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")

        def _on_sf_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(e):
            # 스크롤 프레임 너비를 canvas 너비에 맞춤
            canvas.itemconfig(self._sf_window, width=e.width)

        self.scroll_frame.bind("<Configure>", _on_sf_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        sf = self.scroll_frame
        self._build_network_section(sf)

        self.gen_panel = ttk.Frame(sf); self.gen_panel.pack(fill="x")
        self._build_center_section(self.gen_panel)
        self._build_movement_section(self.gen_panel)
        self._build_attack_section(self.gen_panel)
        # 패턴별 파라미터 패널 (모두 생성, 필요한 것만 표시)
        self._build_speed_spike_section(self.gen_panel)
        self._build_anchor_move_section(self.gen_panel)
        self._build_course_mismatch_section(self.gen_panel)
        self._build_position_jump_section(self.gen_panel)
        self._build_jbu_section(self.gen_panel)
        self._build_pincer_section(self.gen_panel)
        self._build_wave_section(self.gen_panel)
        self._build_ghost_circle_section(self.gen_panel)
        self._build_circle_patrol_section(self.gen_panel)
        self._build_linear_bounce_section(self.gen_panel)
        self._build_realistic_speed_section(self.gen_panel)
        self._build_ml_low_slow_section(self.gen_panel)
        self._build_ml_temporal_section(self.gen_panel)
        self._build_ml_gradual_drift_section(self.gen_panel)
        self._build_ml_mimicry_section(self.gen_panel)
        self._build_fn_dt_section(self.gen_panel)
        self._build_fn_ramp_section(self.gen_panel)
        self._build_fn_cog_section(self.gen_panel)
        self._build_fn_nav_section(self.gen_panel)
        self._build_adv_smooth_section(self.gen_panel)
        self._build_adv_desync_section(self.gen_panel)
        self._build_adv_window_edge_section(self.gen_panel)
        self._build_adv_contextual_section(self.gen_panel)
        self._build_adv_shadow_section(self.gen_panel)
        self._build_extra_section(self.gen_panel)

        self.file_panel = ttk.Frame(sf); self.file_panel.pack(fill="x")
        self._build_file_section(self.file_panel)

        self._build_control_section(sf)

        # 로그 패널
        tk.Label(right, text="  LIVE TRANSMISSION LOG",
                 bg="#09111d", fg="#35d0ff",
                 font=("Consolas", 11, "bold"), padx=12, pady=8).pack(fill="x")
        self.log_box = scrolledtext.ScrolledText(
            right, bg="#051019", fg="#dff7ff", font=("Consolas", 9),
            insertbackground="#35d0ff", selectbackground="#1b3555",
            relief="flat", borderwidth=0, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_box.tag_config("info",  foreground="#c6d6ea")
        self.log_box.tag_config("start", foreground="#35d0ff")
        self.log_box.tag_config("error", foreground="#ff6b6b")

    # ── 각 섹션 빌더 ────────────────────────────────
    def _build_network_section(self, p):
        self._section(p, "네트워크")
        self.host_entry    = self._row(p, "대상 IP",           self._entry, default="127.0.0.1")
        self.port_entry    = self._row(p, "UDP 포트",          self._entry, default="1111")
        self.interval_spin = self._row(p, "생성 신호 주기(초)", self._spin, from_=0.2, to=120.0, default=2.0, step=0.1)

    def _build_center_section(self, p):
        self._section(p, "기준 좌표 (기본: 서해 37°N 21°E)")
        self.lat_entry = self._row(p, "중심 위도 (N)", self._entry, default="37.00")
        self.lon_entry = self._row(p, "중심 경도 (E)", self._entry, default="21.00")

    def _build_movement_section(self, p):
        self._section(p, "★ 선단 이동 제어", style="Accent.TLabel")
        self.move_speed   = self._row(p, "이동 속도 (kn)",    self._spin, from_=0.0, to=30.0, default=0.0, step=0.5)
        self.move_heading = self._row(p, "이동 방향 (도)",    self._spin, from_=0, to=359, default=0, step=5)
        self.move_accel   = self._row(p, "가속도 (kn/분)",    self._spin, from_=0.0, to=5.0, default=0.0, step=0.1)

    def _build_attack_section(self, p):
        self._section(p, "생성 패턴")
        row = ttk.Frame(p); row.pack(fill="x", padx=16, pady=4)
        self.attack_var = tk.StringVar(value=ATTACK_OPTIONS[0][1])
        cb = ttk.Combobox(row, textvariable=self.attack_var,
                          values=[lbl for _, lbl in ATTACK_OPTIONS],
                          state="readonly", font=("Consolas", 11))
        cb.pack(fill="x"); cb.bind("<<ComboboxSelected>>", self._on_attack_change)

    # 패턴 파라미터 패널들
    def _make_param_frame(self, parent):
        f = ttk.Frame(parent); f.pack(fill="x"); return f

    def _build_speed_spike_section(self, p):
        self.pf_speed_spike = self._make_param_frame(p)
        f = self.pf_speed_spike
        self._section(f, "속도 이상 설정")
        self.circle_count         = self._row(f, "선박 수",           self._spin, from_=1,   to=200,  default=25,  step=1)
        self.circle_radius        = self._row(f, "기본 속도 (kn)",    self._spin, from_=0.0, to=40.0, default=8.0, step=0.5)
        self.circle_speed         = self._row(f, "스파이크 속도 (kn)",self._spin, from_=0.0, to=60.0, default=30.0,step=1.0)
        self.circle_mode          = self._row(f, "스파이크 방식",     self._combo, values=["간헐","순간"], default="간헐")
        self.circle_converge_rate = self._row(f, "스파이크 주기 (초)",self._spin, from_=1.0, to=120.0, default=10.0, step=1.0)

    def _build_anchor_move_section(self, p):
        self.pf_anchor_move = self._make_param_frame(p)
        f = self.pf_anchor_move
        self._section(f, "정박 이동 이상 설정")
        self.grid_rows    = self._row(f, "선박 수",           self._spin, from_=1, to=300,  default=30,  step=1)
        self.grid_cols    = self._row(f, "클러스터 반경 (도)",self._spin, from_=0.01,to=1.0,default=0.10,step=0.01)
        self.grid_spacing = self._row(f, "이상 이동 속도 (kn)",self._spin,from_=0.0,to=10.0,default=3.0, step=0.1)
        self.grid_speed   = self._row(f, "COG 방향 (도)",    self._spin, from_=0,  to=359,  default=90,  step=5)
        self.grid_heading = self._row(f, "경도 오프셋 (도)", self._spin, from_=-1.0,to=1.0, default=0.0, step=0.01)
        self.grid_rotate  = self._row(f, "드리프트 강도",    self._spin, from_=-1.0,to=1.0, default=0.0, step=0.05)

    def _build_course_mismatch_section(self, p):
        self.pf_course_mismatch = self._make_param_frame(p)
        f = self.pf_course_mismatch
        self._section(f, "COG/HDG 불일치 설정")
        self.spiral_count  = self._row(f, "선박 수",            self._spin, from_=3,   to=200,  default=20,   step=1)
        self.spiral_turns  = self._row(f, "불일치 각도 (도)",   self._spin, from_=90.0,to=180.0,default=150.0,step=5.0)
        self.spiral_max_r  = self._row(f, "기본 속도 (kn)",     self._spin, from_=0.0, to=30.0, default=10.0, step=0.5)
        self.spiral_speed  = self._row(f, "COG 변화 속도",      self._spin, from_=0.0, to=20.0, default=5.0,  step=0.5)
        self.spiral_expand = self._row(f, "HDG 편차 (도)",      self._spin, from_=0.0, to=180.0,default=120.0,step=5.0)

    def _build_position_jump_section(self, p):
        self.pf_position_jump = self._make_param_frame(p)
        f = self.pf_position_jump
        self._section(f, "위치 점프 이상 설정")
        self.random_count          = self._row(f, "선박 수",        self._spin,  from_=1,   to=300, default=30,  step=1)
        self.random_spread         = self._row(f, "점프 반경 (도)", self._spin,  from_=0.05,to=2.0, default=0.30,step=0.05)
        self.random_converge_strength = self._row(f, "점프 간격 (초)", self._spin,from_=1.0,to=60.0,default=10.0,step=0.5)
        self.random_converge_lat   = self._row(f, "점프 기준 위도", self._entry, default="37.00")
        self.random_converge_lon   = self._row(f, "점프 기준 경도", self._entry, default="21.00")

    def _build_jbu_section(self, p):
        self.pf_jbu = self._make_param_frame(p)
        f = self.pf_jbu
        self._section(f, "JBU 글자 선단 설정")
        self.jbu_scale = self._row(f, "글자 크기 배율", self._spin, from_=0.5, to=5.0, default=1.0, step=0.1)

    def _build_pincer_section(self, p):
        self.pf_pincer = self._make_param_frame(p)
        f = self.pf_pincer
        self._section(f, "집게 협공 설정")
        self.pincer_count = self._row(f, "선박 수 (양날)", self._spin, from_=4,  to=80,  default=20,  step=2)
        self.pincer_width = self._row(f, "날개 폭 (도)",   self._spin, from_=0.05,to=2.0,default=0.5, step=0.05)
        self.pincer_depth = self._row(f, "종심 (도)",      self._spin, from_=0.05,to=1.5,default=0.3, step=0.05)
        self.pincer_speed = self._row(f, "수렴 속도 (kn)", self._spin, from_=1,  to=30,  default=8.0, step=0.5)

    def _build_wave_section(self, p):
        self.pf_wave = self._make_param_frame(p)
        f = self.pf_wave
        self._section(f, "파상 대형 설정")
        self.wave_count     = self._row(f, "선박 수",       self._spin, from_=3,   to=60,  default=24,   step=3)
        self.wave_lanes     = self._row(f, "열 수",         self._spin, from_=1,   to=6,   default=3,    step=1)
        self.wave_width     = self._row(f, "전체 폭 (도)",  self._spin, from_=0.1, to=2.0, default=0.6,  step=0.1)
        self.wave_amplitude = self._row(f, "횡진폭 (도)",   self._spin, from_=0.01,to=0.5, default=0.15, step=0.01)
        self.wave_speed     = self._row(f, "전진 속도 (kn)",self._spin, from_=1,   to=30,  default=10.0, step=0.5)
        self.wave_freq      = self._row(f, "사인파 주파수", self._spin, from_=0.005,to=0.2,default=0.05, step=0.005)

    def _build_ghost_circle_section(self, p):
        self.pf_ghost_circle = self._make_param_frame(p)
        f = self.pf_ghost_circle
        self._section(f, "고스트쉽 원형 정지 설정")
        ttk.Label(f, text="  ▶ navStatus=1(정박) 선박들이 원형으로 나타났다 사라짐",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.gc_count            = self._row(f, "선박 수",            self._spin, from_=4,   to=100, default=20,   step=1)
        self.gc_radius           = self._row(f, "원 반경 (도)",       self._spin, from_=0.01,to=1.0, default=0.15, step=0.01)
        self.gc_appear_interval  = self._row(f, "출현 주기 (초)",     self._spin, from_=5.0, to=300, default=30.0, step=5.0)
        self.gc_vanish_after     = self._row(f, "유지 시간 (초)",     self._spin, from_=5.0, to=300, default=20.0, step=5.0)

    def _build_circle_patrol_section(self, p):
        self.pf_circle_patrol = self._make_param_frame(p)
        f = self.pf_circle_patrol
        self._section(f, "원형 순찰 설정")
        self.cp_count          = self._row(f, "선박 수",             self._spin, from_=2,   to=100, default=15,   step=1)
        self.cp_radius         = self._row(f, "순찰 반경 (도)",      self._spin, from_=0.05,to=1.0, default=0.2,  step=0.01)
        self.cp_sog            = self._row(f, "기본 속도 (kn)",      self._spin, from_=1.0, to=40.0,default=12.0, step=0.5)
        self.cp_spike_sog      = self._row(f, "이상 속도 (kn, 0=없음)",self._spin,from_=0.0,to=60.0,default=0.0,  step=1.0)
        self.cp_spike_interval = self._row(f, "이상 주기 (초)",      self._spin, from_=5.0, to=120, default=20.0, step=5.0)

    def _build_linear_bounce_section(self, p):
        self.pf_linear_bounce = self._make_param_frame(p)
        f = self.pf_linear_bounce
        self._section(f, "직선 왕복 설정")
        self.lb_count          = self._row(f, "선박 수",               self._spin, from_=1,  to=100, default=15,   step=1)
        self.lb_length         = self._row(f, "왕복 길이 (도)",        self._spin, from_=0.1,to=2.0, default=0.4,  step=0.05)
        self.lb_heading        = self._row(f, "이동 방향 (도)",        self._spin, from_=0,  to=359, default=0,    step=5)
        self.lb_sog            = self._row(f, "기본 속도 (kn)",        self._spin, from_=1.0,to=30.0,default=10.0, step=0.5)
        self.lb_spike_sog      = self._row(f, "이상 속도 (kn, 0=없음)",self._spin, from_=0.0,to=60.0,default=0.0,  step=1.0)
        self.lb_spike_interval = self._row(f, "이상 주기 (초)",        self._spin, from_=5.0,to=120, default=20.0, step=5.0)

    def _build_realistic_speed_section(self, p):
        self.pf_realistic_speed = self._make_param_frame(p)
        f = self.pf_realistic_speed
        self._section(f, "실제루트 이상속도 설정")
        ttk.Label(f, text="  ▶ 13개 웨이포인트 항로를 순항 중 특정 구간에서 속도 이상",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.rs_count      = self._row(f, "선박 수",               self._spin, from_=1,   to=50,  default=10,   step=1)
        self.rs_sog_normal = self._row(f, "정상 속도 (kn)",        self._spin, from_=1.0, to=30.0,default=12.0, step=0.5)
        self.rs_sog_spike  = self._row(f, "이상 속도 (kn)",        self._spin, from_=1.0, to=60.0,default=35.0, step=1.0)
        self.rs_spike_wp   = self._row(f, "이상 구간 WP 인덱스(0~12)",self._spin,from_=0, to=12,  default=4,    step=1)

    def _build_ml_low_slow_section(self, p):
        self.pf_ml_low_slow = self._make_param_frame(p)
        f = self.pf_ml_low_slow
        self._section(f, "[ML] Low & Slow 설정", style="ML.TLabel")
        ttk.Label(f, text="  ▶ 모든 임계값 동시 하회. 규칙 IDS 전 통과, ML은 동시 하회 패턴 포착",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.ml_ls_count = self._row(f, "선박 수", self._spin, from_=1, to=50, default=10, step=1)

    def _build_ml_temporal_section(self, p):
        self.pf_ml_temporal = self._make_param_frame(p)
        f = self.pf_ml_temporal
        self._section(f, "[ML] Temporal Camouflage 설정", style="ML.TLabel")
        ttk.Label(f, text="  ▶ 정상 N개 사이 이상 1개 삽입. 윈도우 피처에 희석",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.ml_tc_count        = self._row(f, "선박 수",             self._spin, from_=1,   to=50,  default=10,  step=1)
        self.ml_tc_normal_n     = self._row(f, "정상 메시지 수 (N)",  self._spin, from_=2,   to=20,  default=8,   step=1)
        self.ml_tc_anomaly_sog  = self._row(f, "이상 삽입 SOG (kn)", self._spin, from_=10.0,to=60.0,default=40.0,step=1.0)

    def _build_ml_gradual_drift_section(self, p):
        self.pf_ml_gradual_drift = self._make_param_frame(p)
        f = self.pf_ml_gradual_drift
        self._section(f, "[ML] Gradual Drift 설정", style="ML.TLabel")
        ttk.Label(f, text="  ▶ GPS 노이즈 수준 이동이 누적 → 실질 위치 조작",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.ml_gd_count = self._row(f, "선박 수",             self._spin, from_=1, to=50,    default=10,    step=1)
        self.ml_gd_step  = self._row(f, "스텝 크기 (도/틱)",  self._spin, from_=0.0001, to=0.001, default=0.0004, step=0.0001)

    def _build_ml_mimicry_section(self, p):
        self.pf_ml_mimicry = self._make_param_frame(p)
        f = self.pf_ml_mimicry
        self._section(f, "[ML] Feature Mimicry 설정", style="ML.TLabel")
        ttk.Label(f, text="  ▶ 정상 SOG 프로파일 복사 + 실제 위치는 다른 방향 이동",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.ml_mm_count      = self._row(f, "선박 수",              self._spin, from_=1,   to=50,  default=10,  step=1)
        self.ml_mm_hidden_sog = self._row(f, "실제 이동 속도 (kn)", self._spin, from_=1.0, to=40.0,default=15.0,step=0.5)

    def _build_fn_dt_section(self, p):
        self.pf_fn_dt = self._make_param_frame(p)
        f = self.pf_fn_dt
        self._section(f, "[FN-1] dt 구간 점프  (주기 65~120초 권장)", style="Accent.TLabel")
        ttk.Label(f, text="  ▶ Check5/6 회피: dt>60이면 속도급변·위치점프 검사 skip",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.fn_dt_count     = self._row(f, "선박 수",           self._spin, from_=1, to=50,  default=10,  step=1)
        self.fn_dt_jump_dist = self._row(f, "점프 거리 (도)",    self._spin, from_=0.05,to=0.5,default=0.15,step=0.01)

    def _build_fn_ramp_section(self, p):
        self.pf_fn_ramp = self._make_param_frame(p)
        f = self.pf_fn_ramp
        self._section(f, "[FN-2] 속도 단계 상승  (주기 30~55초 권장)", style="Accent.TLabel")
        ttk.Label(f, text="  ▶ Check5 회피: Δsog<10.0/메시지 유지",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.fn_ramp_count = self._row(f, "선박 수",         self._spin, from_=1,  to=50,  default=10,  step=1)
        self.fn_ramp_start = self._row(f, "시작 속도 (kn)", self._spin, from_=0.0,to=5.0, default=2.0, step=0.5)
        self.fn_ramp_step  = self._row(f, "증가량 (kn,<10)",self._spin, from_=1.0,to=9.9, default=9.5, step=0.1)
        self.fn_ramp_max   = self._row(f, "상한 (kn,<30)",  self._spin, from_=5.0,to=29.9,default=29.0,step=1.0)

    def _build_fn_cog_section(self, p):
        self.pf_fn_cog = self._make_param_frame(p)
        f = self.pf_fn_cog
        self._section(f, "[FN-3] COG/HDG 경계값  (91~99도)", style="Accent.TLabel")
        ttk.Label(f, text="  ▶ Check4 회피: diff<100도 미만은 탐지 안됨",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.fn_cog_count    = self._row(f, "선박 수",            self._spin, from_=1,   to=50,  default=10,  step=1)
        self.fn_cog_mismatch = self._row(f, "COG-HDG 불일치 (도)",self._spin, from_=80.0,to=99.9,default=95.0,step=1.0)

    def _build_fn_nav_section(self, p):
        self.pf_fn_nav = self._make_param_frame(p)
        f = self.pf_fn_nav
        self._section(f, "[FN-4] navStatus 회피  (2/3/7/8/11/12)", style="Accent.TLabel")
        ttk.Label(f, text="  ▶ Check3 회피: 1/5/6 외는 SOG≥0.5여도 탐지 안됨",
                  style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0,4))
        self.fn_nav_count = self._row(f, "선박 수",      self._spin, from_=1,  to=50,  default=12, step=1)
        self.fn_nav_sog   = self._row(f, "SOG (kn≥0.5)",self._spin, from_=0.5,to=20.0,default=3.0,step=0.5)

    def _build_adv_smooth_section(self, p):
        self.pf_adv_smooth = self._make_param_frame(p)
        f = self.pf_adv_smooth
        self._section(f, "E1  Smooth Trajectory 설정", style="ADV.TLabel")
        ttk.Label(f,
            text="  ▶ CTRV 운동모델 준수 → Kalman 잔차 ≈ 0, jerk 피처 무력화",
            style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0, 4))
        self.adv_sm_count     = self._row(f, "선박 수",          self._spin, from_=1,   to=50,  default=10,   step=1)
        self.adv_sm_sog       = self._row(f, "기본 SOG (kn)",    self._spin, from_=1.0, to=40.0,default=15.0, step=0.5)
        self.adv_sm_turn_rate = self._row(f, "선회율 (deg/s)",   self._spin, from_=0.1, to=10.0,default=2.0,  step=0.1)

    def _build_adv_desync_section(self, p):
        self.pf_adv_desync = self._make_param_frame(p)
        f = self.pf_adv_desync
        self._section(f, "E2  Fleet Desync 설정", style="ADV.TLabel")
        ttk.Label(f,
            text="  ▶ MMSI/shipType/SOG 분포 개별화 → fleet 상관 피처 파괴",
            style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0, 4))
        self.adv_ds_count     = self._row(f, "선박 수",           self._spin, from_=1,   to=50,  default=12,   step=1)
        self.adv_ds_sog_spike = self._row(f, "이상 SOG (kn)",    self._spin, from_=10.0,to=60.0,default=38.0, step=1.0)

    def _build_adv_window_edge_section(self, p):
        self.pf_adv_window_edge = self._make_param_frame(p)
        f = self.pf_adv_window_edge
        self._section(f, "E3  Window Edge 설정", style="ADV.TLabel")
        ttk.Label(f,
            text="  ▶ ML window 크기-1 주기로 이상 삽입 → 양쪽 window에 1개씩만 노출",
            style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0, 4))
        self.adv_we_count      = self._row(f, "선박 수",            self._spin, from_=1,   to=50,   default=10,   step=1)
        self.adv_we_window     = self._row(f, "가정 ML window 크기",self._spin, from_=5,   to=50,   default=20,   step=1)
        self.adv_we_sog        = self._row(f, "이상 SOG (kn)",      self._spin, from_=10.0,to=60.0, default=42.0, step=1.0)
        self.adv_we_normal_sog = self._row(f, "정상 SOG (kn)",      self._spin, from_=1.0, to=30.0, default=12.0, step=0.5)

    def _build_adv_contextual_section(self, p):
        self.pf_adv_contextual = self._make_param_frame(p)
        f = self.pf_adv_contextual
        self._section(f, "E4  Contextual Blend 설정", style="ADV.TLabel")
        ttk.Label(f,
            text="  ▶ 어선 조업 패턴 위장(shipType=30, navStatus=7) + 실제 침투 이동",
            style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0, 4))
        self.adv_ct_count     = self._row(f, "선박 수",         self._spin, from_=1,   to=50,  default=10,  step=1)
        self.adv_ct_drift_dir = self._row(f, "실제 침투 방향 (도)",self._spin,from_=0,  to=359, default=45,  step=5)
        self.adv_ct_drift_sog = self._row(f, "실제 침투 속도 (kn)",self._spin,from_=0.5,to=10.0,default=3.0,step=0.5)

    def _build_adv_shadow_section(self, p):
        self.pf_adv_shadow = self._make_param_frame(p)
        f = self.pf_adv_shadow
        self._section(f, "E5  Shadow Vessel 설정", style="ADV.TLabel")
        ttk.Label(f,
            text="  ▶ 한국 MID(440/441) + 연안화물 프로파일로 목표 좌표 자연 접근",
            style="Sub.TLabel").pack(anchor="w", padx=16, pady=(0, 4))
        self.adv_sh_count        = self._row(f, "선박 수",           self._spin,  from_=1,   to=30,   default=8,    step=1)
        self.adv_sh_target_lat   = self._row(f, "목표 위도",         self._entry, default="37.00")
        self.adv_sh_target_lon   = self._row(f, "목표 경도",         self._entry, default="21.00")
        self.adv_sh_approach_sog = self._row(f, "접근 속도 (kn)",    self._spin,  from_=5.0, to=20.0, default=12.0, step=0.5)

    def _build_extra_section(self, p):
        self._section(p, "추가 옵션")
        row = ttk.Frame(p); row.pack(fill="x", padx=16, pady=4)
        self.anchor_var = tk.BooleanVar(value=False)   # ← 기본 비활성화
        ttk.Checkbutton(row, text="중앙 정박선 추가 (MMSI: 440123456)",
                        variable=self.anchor_var).pack(anchor="w")

    def _build_file_section(self, p):
        self._section(p, "정상 신호 파일")
        frow = ttk.Frame(p); frow.pack(fill="x", padx=16, pady=2)
        ttk.Label(frow, text="NMEA 파일", width=26, anchor="w",
                  style="Sub.TLabel").pack(side="left")
        default_path = str(DEFAULT_SAMPLE_FILE if DEFAULT_SAMPLE_FILE.exists() else "")
        self.file_path_var = tk.StringVar(value=default_path)
        ttk.Entry(frow, textvariable=self.file_path_var).pack(side="left", fill="x", expand=True)
        tk.Button(frow, text="찾기", bg="#172334", fg="#edf4ff", relief="flat",
                  activebackground="#24354d", command=self._browse_file,
                  padx=12, pady=4).pack(side="left", padx=(6, 0))
        self.file_interval_spin = self._row(p, "문장 간격(초)", self._spin,
                                            from_=0.01, to=5.0, default=0.1, step=0.01)
        rrow = ttk.Frame(p); rrow.pack(fill="x", padx=16, pady=4)
        self.file_repeat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rrow, text="파일 끝까지 전송 후 반복",
                        variable=self.file_repeat_var).pack(anchor="w")

    def _build_control_section(self, p):
        w = ttk.Frame(p); w.pack(fill="x")
        ttk.Separator(w, orient="horizontal").pack(fill="x", padx=10, pady=10)
        ctrl = ttk.Frame(w); ctrl.pack(fill="x", padx=10, pady=(0, 6))

        gen_row = ttk.Frame(ctrl); gen_row.pack(fill="x", pady=(0, 6))
        ttk.Label(gen_row, text="생성 신호", width=12, anchor="w",
                  style="Sub.TLabel").pack(side="left")
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

        file_row = ttk.Frame(ctrl); file_row.pack(fill="x", pady=(0, 6))
        ttk.Label(file_row, text="정상 파일", width=12, anchor="w",
                  style="Sub.TLabel").pack(side="left")
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
        self.stop_all_btn.pack(fill="x", pady=(0, 6))

        # ★ 실시간 조작 창 버튼
        self.rt_btn = tk.Button(
            ctrl, text="🎮  실시간 조작 창 열기",
            bg="#1a2a1a", fg="#44ff88",
            font=("Consolas", 11, "bold"), activebackground="#203020",
            relief="flat", cursor="hand2", padx=16, pady=7,
            command=self.open_rt_control)
        self.rt_btn.pack(fill="x", pady=(0, 10))

    # ── 패턴 파라미터 프레임 맵 ────────────────────
    @property
    def _pattern_frames(self) -> dict:
        return {
            "speed_spike":         self.pf_speed_spike,
            "anchor_move":         self.pf_anchor_move,
            "course_mismatch":     self.pf_course_mismatch,
            "position_jump":       self.pf_position_jump,
            "jbu_fleet":           self.pf_jbu,
            "pincer":              self.pf_pincer,
            "wave":                self.pf_wave,
            "ghost_circle_static": self.pf_ghost_circle,
            "circle_patrol":       self.pf_circle_patrol,
            "linear_bounce":       self.pf_linear_bounce,
            "realistic_speed":     self.pf_realistic_speed,
            "ml_low_slow":         self.pf_ml_low_slow,
            "ml_temporal":         self.pf_ml_temporal,
            "ml_gradual_drift":    self.pf_ml_gradual_drift,
            "ml_mimicry":          self.pf_ml_mimicry,
            "blind_dt_jump":       self.pf_fn_dt,
            "blind_speed_ramp":    self.pf_fn_ramp,
            "blind_cog_border":    self.pf_fn_cog,
            "blind_nav_status":    self.pf_fn_nav,
            "adv_smooth":          self.pf_adv_smooth,
            "adv_desync":          self.pf_adv_desync,
            "adv_window_edge":     self.pf_adv_window_edge,
            "adv_contextual":      self.pf_adv_contextual,
            "adv_shadow":          self.pf_adv_shadow,
        }

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
        for key, frame in self._pattern_frames.items():
            if key == attack_key:
                if not frame.winfo_manager():
                    frame.pack(fill="x")
            elif frame.winfo_manager():
                frame.pack_forget()

    def open_rt_control(self):
        if self.rt_window is None or not self.rt_window.winfo_exists():
            self.rt_window = RealTimeControlWindow(self)
        else:
            self.rt_window.lift()
            self.rt_window.focus_force()

    # ── 설정 수집 ───────────────────────────────────
    def _get_common_cfg(self):
        host = self.host_entry._var.get().strip()
        if not host: raise ValueError("대상 IP를 입력하세요.")
        port = int(self.port_entry._var.get())
        if not 1 <= port <= 65535: raise ValueError("UDP 포트 범위 오류")
        return {"host": host, "port": port}

    def _get_generated_cfg(self):
        cfg = self._get_common_cfg()
        interval = float(self.interval_spin._var.get())
        if interval <= 0: raise ValueError("생성 신호 주기 > 0 이어야 합니다.")
        attack_label = self.attack_var.get()
        attack_key   = ATTACK_LABEL_TO_KEY[attack_label]

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
            "attack_key":  attack_key,
            "attack_label":attack_label,
            "add_anchor":  self.anchor_var.get(),
            # 이동 제어
            "move_speed":   float(self.move_speed._var.get()),
            "move_heading": float(self.move_heading._var.get()),
            "move_accel":   float(self.move_accel._var.get()),
            # 속도 이상
            "speed_count":    min(200, max(1, int(self.circle_count._var.get()))),
            "speed_base":     float(self.circle_radius._var.get()),
            "speed_spike":    float(self.circle_speed._var.get()),
            "speed_mode":     self.circle_mode._var.get(),
            "speed_interval": float(self.circle_converge_rate._var.get()),
            # 정박 이동
            "anchor_count":      min(300, max(1, int(self.grid_rows._var.get()))),
            "anchor_radius":     float(self.grid_cols._var.get()),
            "anchor_speed":      float(self.grid_spacing._var.get()),
            "anchor_cog":        float(self.grid_speed._var.get()),
            "anchor_lon_offset": float(self.grid_heading._var.get()),
            "anchor_drift":      float(self.grid_rotate._var.get()),
            # COG/HDG 불일치
            "course_count":   min(200, max(3, int(self.spiral_count._var.get()))),
            "course_mismatch":float(self.spiral_turns._var.get()),
            "course_speed":   float(self.spiral_max_r._var.get()),
            "course_drift":   float(self.spiral_speed._var.get()),
            "course_offset":  float(self.spiral_expand._var.get()),
            # 위치 점프
            "jump_count":      min(300, max(1, int(self.random_count._var.get()))),
            "jump_radius":     float(self.random_spread._var.get()),
            "jump_interval":   float(self.random_converge_strength._var.get()),
            "jump_center_lat": rcl,
            "jump_center_lon": rcn,
            # JBU
            "jbu_scale": float(self.jbu_scale._var.get()),
            # Pincer
            "pincer_count": int(self.pincer_count._var.get()),
            "pincer_width": float(self.pincer_width._var.get()),
            "pincer_depth": float(self.pincer_depth._var.get()),
            "pincer_speed": float(self.pincer_speed._var.get()),
            # Wave
            "wave_count":     int(self.wave_count._var.get()),
            "wave_lanes":     int(self.wave_lanes._var.get()),
            "wave_width":     float(self.wave_width._var.get()),
            "wave_amplitude": float(self.wave_amplitude._var.get()),
            "wave_speed":     float(self.wave_speed._var.get()),
            "wave_freq":      float(self.wave_freq._var.get()),
            # Ghost circle static
            "gc_count":           int(self.gc_count._var.get()),
            "gc_radius":          float(self.gc_radius._var.get()),
            "gc_appear_interval": float(self.gc_appear_interval._var.get()),
            "gc_vanish_after":    float(self.gc_vanish_after._var.get()),
            # Circle patrol
            "cp_count":           int(self.cp_count._var.get()),
            "cp_radius":          float(self.cp_radius._var.get()),
            "cp_sog":             float(self.cp_sog._var.get()),
            "cp_spike_sog":       float(self.cp_spike_sog._var.get()),
            "cp_spike_interval":  float(self.cp_spike_interval._var.get()),
            # Linear bounce
            "lb_count":           int(self.lb_count._var.get()),
            "lb_length":          float(self.lb_length._var.get()),
            "lb_heading":         float(self.lb_heading._var.get()),
            "lb_sog":             float(self.lb_sog._var.get()),
            "lb_spike_sog":       float(self.lb_spike_sog._var.get()),
            "lb_spike_interval":  float(self.lb_spike_interval._var.get()),
            # Realistic speed
            "rs_count":       int(self.rs_count._var.get()),
            "rs_sog_normal":  float(self.rs_sog_normal._var.get()),
            "rs_sog_spike":   float(self.rs_sog_spike._var.get()),
            "rs_spike_wp":    int(self.rs_spike_wp._var.get()),
            # ML patterns
            "ml_ls_count":        int(self.ml_ls_count._var.get()),
            "ml_tc_count":        int(self.ml_tc_count._var.get()),
            "ml_tc_normal_n":     int(self.ml_tc_normal_n._var.get()),
            "ml_tc_anomaly_sog":  float(self.ml_tc_anomaly_sog._var.get()),
            "ml_gd_count":        int(self.ml_gd_count._var.get()),
            "ml_gd_step":         float(self.ml_gd_step._var.get()),
            "ml_mm_count":        int(self.ml_mm_count._var.get()),
            "ml_mm_hidden_sog":   float(self.ml_mm_hidden_sog._var.get()),
            # FN tests
            "fn_dt_count":     int(self.fn_dt_count._var.get()),
            "fn_dt_jump_dist": float(self.fn_dt_jump_dist._var.get()),
            "fn_ramp_count":   int(self.fn_ramp_count._var.get()),
            "fn_ramp_start":   float(self.fn_ramp_start._var.get()),
            "fn_ramp_step":    float(self.fn_ramp_step._var.get()),
            "fn_ramp_max":     float(self.fn_ramp_max._var.get()),
            "fn_cog_count":    int(self.fn_cog_count._var.get()),
            "fn_cog_mismatch": float(self.fn_cog_mismatch._var.get()),
            "fn_nav_count":    int(self.fn_nav_count._var.get()),
            "fn_nav_sog":      float(self.fn_nav_sog._var.get()),
            # E1 Smooth
            "adv_sm_count":      int(self.adv_sm_count._var.get()),
            "adv_sm_sog":        float(self.adv_sm_sog._var.get()),
            "adv_sm_turn_rate":  float(self.adv_sm_turn_rate._var.get()),
            # E2 Desync
            "adv_ds_count":      int(self.adv_ds_count._var.get()),
            "adv_ds_sog_spike":  float(self.adv_ds_sog_spike._var.get()),
            # E3 Window Edge
            "adv_we_count":      int(self.adv_we_count._var.get()),
            "adv_we_window":     int(self.adv_we_window._var.get()),
            "adv_we_sog":        float(self.adv_we_sog._var.get()),
            "adv_we_normal_sog": float(self.adv_we_normal_sog._var.get()),
            # E4 Contextual
            "adv_ct_count":      int(self.adv_ct_count._var.get()),
            "adv_ct_drift_dir":  float(self.adv_ct_drift_dir._var.get()),
            "adv_ct_drift_sog":  float(self.adv_ct_drift_sog._var.get()),
            # E5 Shadow
            "adv_sh_count":        int(self.adv_sh_count._var.get()),
            "adv_sh_target_lat":   float(self.adv_sh_target_lat._var.get() or self.lat_entry._var.get()),
            "adv_sh_target_lon":   float(self.adv_sh_target_lon._var.get() or self.lon_entry._var.get()),
            "adv_sh_approach_sog": float(self.adv_sh_approach_sog._var.get()),
        })
        return cfg

    def _get_file_cfg(self):
        cfg = self._get_common_cfg()
        file_path = Path(self.file_path_var.get().strip())
        if not file_path.exists(): raise ValueError("파일 경로를 확인하세요.")
        interval = float(self.file_interval_spin._var.get())
        if interval <= 0: raise ValueError("문장 간격 > 0 이어야 합니다.")
        cfg.update({"file_path": str(file_path),
                    "file_interval": interval,
                    "file_repeat": self.file_repeat_var.get()})
        return cfg

    # ── 채널 상태 ───────────────────────────────────
    def _any_running(self) -> bool:
        return ((self.generated_thread is not None and self.generated_thread.is_alive()) or
                (self.file_thread is not None and self.file_thread.is_alive()))

    def _set_channel_state(self, channel: str, running: bool) -> None:
        if channel == "generated":
            if running:
                self.generated_start_btn.config(state="disabled", bg="#172334", fg="#5f738c")
                self.generated_stop_btn.config(state="normal")
            else:
                self.generated_start_btn.config(state="normal", bg="#35d0ff", fg="#08101a")
                self.generated_stop_btn.config(state="disabled")
        else:
            if running:
                self.file_start_btn.config(state="disabled", bg="#172334", fg="#5f738c")
                self.file_stop_btn.config(state="normal")
            else:
                self.file_start_btn.config(state="normal", bg="#7ef0c9", fg="#08101a")
                self.file_stop_btn.config(state="disabled")
        self.stop_all_btn.config(state="normal" if self._any_running() else "disabled")

    # ── 송신 제어 ───────────────────────────────────
    def start_generated_sender(self) -> None:
        if self.generated_thread is not None and self.generated_thread.is_alive():
            self.log("[생성] 이미 실행 중", "error"); return
        try:
            cfg = self._get_generated_cfg()
        except ValueError as e:
            messagebox.showerror("입력 오류", str(e)); return
        self.generated_stop_event = threading.Event()
        self._set_channel_state("generated", True)
        self.log("[생성 대기] 스레드 시작", "start")
        self.generated_thread = threading.Thread(
            target=sender_worker,
            args=("generated", cfg, log_queue, self.generated_stop_event),
            daemon=True)
        self.generated_thread.start()

    def stop_generated_sender(self) -> None:
        if self.generated_thread is not None and self.generated_thread.is_alive():
            self.generated_stop_event.set()
            self.log("[생성 중단] 요청됨", "error")

    def start_file_sender(self) -> None:
        if self.file_thread is not None and self.file_thread.is_alive():
            self.log("[파일] 이미 실행 중", "error"); return
        try:
            cfg = self._get_file_cfg()
        except ValueError as e:
            messagebox.showerror("입력 오류", str(e)); return
        self.file_stop_event = threading.Event()
        self._set_channel_state("file", True)
        self.log("[파일 대기] 스레드 시작", "start")
        self.file_thread = threading.Thread(
            target=sender_worker,
            args=("file", cfg, log_queue, self.file_stop_event),
            daemon=True)
        self.file_thread.start()

    def stop_file_sender(self) -> None:
        if self.file_thread is not None and self.file_thread.is_alive():
            self.file_stop_event.set()
            self.log("[파일 중단] 요청됨", "error")

    def stop_all_senders(self) -> None:
        self.stop_generated_sender()
        self.stop_file_sender()

    def log(self, message: str, level: str = "info") -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{ts}] {message}\n", level)
        self.log_box.see("end")

    def _poll_log(self) -> None:
        while not log_queue.empty():
            item = log_queue.get_nowait()
            if item.get("kind") == "channel_state" and item.get("state") == "finished":
                self._set_channel_state(item.get("channel", ""), False)
                continue
            self.log(item.get("message", ""), item.get("level", "info"))
        self.after(200, self._poll_log)

    def _on_close(self) -> None:
        self.generated_stop_event.set()
        self.file_stop_event.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()