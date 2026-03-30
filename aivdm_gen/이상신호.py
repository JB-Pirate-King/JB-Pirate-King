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