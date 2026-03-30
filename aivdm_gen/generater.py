#!/usr/bin/env python3
"""
OpenCPN 유령선박 테스트 - NMEA 0183 UDP 송신기
부산 앞바다 기준 유령선단 시뮬레이션

신호 구성:
  - 원형 유령선단  : 부산 앞바다 중심으로 큰 원형 항행 (40척, 빽빽하게)
  - JBU 글자 선박  : 원 내부에 J B U 글자 형태로 촘촘하게 배치 (글자당 28척)
  - 중앙 정상선박  : 원 중앙에 멈춰있는 정상 AIS 선박 (1척)

좌표 설계 기준:
  - 원 반지름 R_LAT = 0.45도 ≈ 50km
  - JBU 글자는 원 안에 꽉 차게 배치 (위아래로 원 경계까지)
  - 글자 세 개가 가로로 나란히: J(왼), B(중), U(우)
  - 각 선박은 글자 윤곽을 따라 순환 이동

target: localhost:1111 (UDP)
"""

import socket
import time
import math
import random

# ──────────────── 기본 설정 ────────────────
UDP_HOST = "127.0.0.1"
UDP_PORT = 1111

# 부산 앞바다 중심 좌표 (가덕도 동쪽 외해)
CENTER_LAT = 35.05
CENTER_LON = 129.15

# 원 반지름
R_LAT = 0.45   # 위도 방향 ≈ 50km
R_LON = R_LAT / math.cos(math.radians(CENTER_LAT))  # 경도 보정

# 업데이트 주기 (초)
UPDATE_INTERVAL = 2.0


# ──────────────── NMEA 유틸 ────────────────

def nmea_checksum(sentence: str) -> str:
    chk = 0
    for ch in sentence:
        chk ^= ord(ch)
    return f"{chk:02X}"


def build_vdm(mmsi, lat, lon, sog, cog, heading, nav_status=0):
    """AIS Type-1 위치보고 NMEA VDM 생성"""
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
        val = sum(bits[i+j] << (5 - j) for j in range(6))
        c = val + 48
        if c > 87:
            c += 8
        payload += chr(c)

    body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{body}*{nmea_checksum(body)}\r\n"


def build_vsd(mmsi, vessel_name):
    """AIS Type-24 Part A (선박명) NMEA VDM 생성"""
    name = vessel_name[:20].upper().ljust(20, "@")
    bits = []

    def push(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    push(24, 6)
    push(0, 2)
    push(mmsi, 30)
    push(0, 2)
    for ch in name:
        c = ord(ch)
        if c >= 64:
            c -= 64
        push(c, 6)
    push(0, 8)

    while len(bits) % 6:
        bits.append(0)

    payload = ""
    for i in range(0, len(bits), 6):
        val = sum(bits[i+j] << (5 - j) for j in range(6))
        c = val + 48
        if c > 87:
            c += 8
        payload += chr(c)

    body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{body}*{nmea_checksum(body)}\r\n"


# ──────────────── 선박 클래스 ────────────────

class Vessel:
    def __init__(self, mmsi, name, nav_status=0):
        self.mmsi = mmsi
        self.name = name
        self.nav_status = nav_status
        self.lat = CENTER_LAT
        self.lon = CENTER_LON
        self.sog = 0.0
        self.cog = 0.0
        self.heading = 0
        self._circle_angle = 0.0
        self._circle_speed = 0.006
        self._waypoints = []   # [(lat, lon), ...]
        self._wp_idx = 0
        self._wp_progress = 0.0

    def position_message(self):
        return build_vdm(self.mmsi, self.lat, self.lon,
                         self.sog, self.cog, self.heading, self.nav_status)

    def name_message(self):
        return build_vsd(self.mmsi, self.name)


# ──────────────── 글자 윤곽 생성 ────────────────
# 좌표계: (lat, lon) — lat=위도(남북), lon=경도(동서)
# 글자 중심 기준 오프셋으로 표현, 나중에 절대 좌표로 변환

def arc_pts(c_lat, c_lon, r_lat, r_lon, a_start, a_end, n):
    """
    타원호 점들 생성.
    c_lat/c_lon: 타원 중심
    r_lat/r_lon: 위도/경도 방향 반지름
    a_start~a_end: 라디안 (수학 방향: 동쪽=0, 북쪽=π/2)
    n: 점 개수
    """
    pts = []
    for i in range(n):
        angle = a_start + (a_end - a_start) * i / (n - 1)
        pts.append((
            c_lat + r_lat * math.sin(angle),
            c_lon + r_lon * math.cos(angle)
        ))
    return pts


def lerp_pts(p0, p1, n):
    """두 점 사이 직선 보간"""
    return [
        (p0[0] + (p1[0] - p0[0]) * i / (n - 1),
         p0[1] + (p1[1] - p0[1]) * i / (n - 1))
        for i in range(n)
    ]


def letter_J_raw(c_lat, c_lon, w, h, seg=12):
    """
    J 글자 윤곽 (lat, lon) 리스트.
    위쪽: 가로 세리프 (짧게) + 수직 내림
    아래쪽: 왼쪽 방향 반원 꼬리

    c_lat/c_lon: 글자 중심
    w: 경도 폭 (도)
    h: 위도 높이 (도)
    """
    top    = c_lat + h * 0.5
    bot    = c_lat - h * 0.5
    right  = c_lon + w * 0.4
    left   = c_lon - w * 0.35

    # 상단 세리프 (가로 짧은 획: left_serif ~ right)
    serif_l = c_lon - w * 0.15
    pts = []

    # 세리프 가로 (왼쪽 → 오른쪽)
    pts += lerp_pts((top, serif_l), (top, right), seg // 2)
    # 수직 내림 (right, top → right, 꼬리 시작점)
    tail_top = c_lat - h * 0.1
    pts += lerp_pts((top, right), (tail_top, right), seg * 2)
    # 하단 반원 꼬리: 오른쪽 → 아래 → 왼쪽 (반시계)
    # 반원 중심: (tail_top - r_tail, right)
    r_tail_lat = h * 0.32
    r_tail_lon = w * 0.60
    arc_c_lat  = tail_top - r_tail_lat
    arc_c_lon  = right - r_tail_lon * 0.0  # 중심은 수직선 아래
    arc_c_lon  = c_lon - w * 0.05

    tail_arc = arc_pts(
        arc_c_lat, arc_c_lon,
        r_tail_lat, r_tail_lon,
        math.pi * 0.0,   # 오른쪽 (0도)
        math.pi * 1.0,   # 왼쪽 (180도)
        seg * 2
    )
    # arc_pts는 동쪽=0 북쪽=π/2 → 시작이 동쪽(오른쪽)이고 북쪽 방향으로 돔
    # 반원을 아래쪽으로 굽히려면 sin을 반전
    tail_arc = [
        (arc_c_lat - r_tail_lat * math.sin(math.pi * i / (seg * 2 - 1)),
         arc_c_lon + r_tail_lon * math.cos(math.pi - math.pi * i / (seg * 2 - 1)))
        for i in range(seg * 2)
    ]
    pts += tail_arc

    return pts


def letter_B_raw(c_lat, c_lon, w, h, seg=12):
    """
    B 글자 윤곽.
    - 왼쪽 수직선 (위 → 아래)
    - 아래쪽 반원 (오른쪽으로 볼록)
    - 가운데 수평선 (오른쪽 → 왼쪽)
    - 위쪽 반원 (오른쪽으로 볼록, 약간 작게)
    """
    top   = c_lat + h * 0.5
    bot   = c_lat - h * 0.5
    mid   = c_lat + h * 0.02   # 약간 위쪽 중간
    left  = c_lon - w * 0.45

    pts = []

    # 왼쪽 수직선 위 → 아래
    pts += lerp_pts((top, left), (bot, left), seg * 2)

    # 아래 반원: bot_left → 오른쪽 볼록 → mid_left
    r_bot_lat = (mid - bot) * 0.92
    r_bot_lon = w * 0.52
    arc_c_bot_lat = (bot + mid) / 2
    arc_c_bot_lon = left
    bot_arc = [
        (arc_c_bot_lat + r_bot_lat * math.sin(math.pi + math.pi * i / (seg * 2 - 1)),
         arc_c_bot_lon + r_bot_lon * math.cos(math.pi + math.pi * i / (seg * 2 - 1)))
        for i in range(seg * 2)
    ]
    # 아래 반원은 왼쪽에서 시작해서 오른쪽으로 볼록하게 나갔다 돌아옴
    # sin: 아래(π)=0, 오른쪽(3π/2)=최대, 위(2π)=0
    bot_arc = [
        (arc_c_bot_lat - r_bot_lat * math.cos(math.pi * i / (seg * 2 - 1)),
         arc_c_bot_lon + r_bot_lon * math.sin(math.pi * i / (seg * 2 - 1)))
        for i in range(seg * 2)
    ]
    pts += bot_arc

    # 가운데 가로선: 오른쪽 끝 → 왼쪽
    pts += lerp_pts((mid, left + r_bot_lon * 0.1), (mid, left), seg)

    # 위 반원: mid_left → 오른쪽 볼록 → top_left
    r_top_lat = (top - mid) * 0.88
    r_top_lon = w * 0.46
    arc_c_top_lat = (top + mid) / 2
    arc_c_top_lon = left
    top_arc = [
        (arc_c_top_lat - r_top_lat * math.cos(math.pi * i / (seg * 2 - 1)),
         arc_c_top_lon + r_top_lon * math.sin(math.pi * i / (seg * 2 - 1)))
        for i in range(seg * 2)
    ]
    pts += top_arc

    # 위 끝 → 수직선 상단으로 닫기
    pts += lerp_pts((top, left + r_top_lon * 0.1), (top, left), seg // 2)

    return pts


def letter_U_raw(c_lat, c_lon, w, h, seg=12):
    """
    U 글자 윤곽.
    - 왼쪽 수직선 위 → 아래 (하단 반원 시작점까지)
    - 하단 반원 (왼쪽 → 아래 → 오른쪽)
    - 오른쪽 수직선 아래 → 위
    """
    top   = c_lat + h * 0.5
    left  = c_lon - w * 0.45
    right = c_lon + w * 0.45

    # 반원 시작 위도: 수직선 아래쪽
    arc_top_lat = c_lat - h * 0.18  # 반원이 시작되는 위도
    r_arc_lat   = h * 0.30          # 반원 위도 반지름
    r_arc_lon   = w * 0.45          # 반원 경도 반지름
    arc_c_lat   = arc_top_lat - r_arc_lat
    arc_c_lon   = c_lon

    pts = []

    # 왼쪽 수직선 위 → 반원 시작
    pts += lerp_pts((top, left), (arc_top_lat, left), seg * 2)

    # 하단 반원: 왼쪽(π) → 아래(3π/2) → 오른쪽(2π=0)
    bottom_arc = [
        (arc_c_lat + r_arc_lat * math.sin(math.pi - math.pi * i / (seg * 2 - 1)),
         arc_c_lon - r_arc_lon * math.cos(math.pi - math.pi * i / (seg * 2 - 1)))
        for i in range(seg * 2)
    ]
    # 올바른 U자 하단: 왼쪽에서 시작해 아래로 굽어 오른쪽으로
    bottom_arc = [
        (arc_c_lat - r_arc_lat * math.cos(math.pi * i / (seg * 2 - 1)),
         arc_c_lon - r_arc_lon + r_arc_lon * 2 * i / (seg * 2 - 1))
        for i in range(seg * 2)
    ]
    # 더 정확한 반원: 각도 π(왼) → 0(오른), 아래쪽으로 볼록
    bottom_arc = []
    for i in range(seg * 2):
        angle = math.pi - math.pi * i / (seg * 2 - 1)  # π → 0
        lat = arc_c_lat - r_arc_lat * math.sin(angle - math.pi / 2 + math.pi / 2)
        # sin(π)=0, sin(π/2)=1, sin(0)=0 — 아래로 볼록하게
        lat = arc_c_lat - r_arc_lat * abs(math.sin(angle))
        lon = arc_c_lon + r_arc_lon * math.cos(angle)
        bottom_arc.append((lat, lon))
    pts += bottom_arc

    # 오른쪽 수직선 반원 끝 → 위
    pts += lerp_pts((arc_top_lat, right), (top, right), seg * 2)

    return pts


def path_length(pts):
    total = 0.0
    for i in range(len(pts)):
        a = pts[i]
        b = pts[(i + 1) % len(pts)]
        dlat = b[0] - a[0]
        dlon = (b[1] - a[1]) * math.cos(math.radians(CENTER_LAT))
        total += math.sqrt(dlat**2 + dlon**2)
    return total


def place_ships_on_path(pts, n_ships, mmsi_base, prefix):
    """
    pts 경로를 따라 n_ships척을 균등 배치.
    각 선박의 초기 위치와 wp_idx를 다르게 설정.
    """
    # 각 세그먼트 길이 (경도 보정 포함)
    seg_lens = []
    cos_lat = math.cos(math.radians(CENTER_LAT))
    for i in range(len(pts)):
        a = pts[i]
        b = pts[(i + 1) % len(pts)]
        dlat = b[0] - a[0]
        dlon = (b[1] - a[1]) * cos_lat
        seg_lens.append(math.sqrt(dlat**2 + dlon**2))
    total_len = sum(seg_lens)

    ships = []
    for s in range(n_ships):
        target_dist = total_len * s / n_ships
        cumul = 0.0
        for seg_i, seg_len in enumerate(seg_lens):
            if cumul + seg_len >= target_dist or seg_i == len(seg_lens) - 1:
                t = (target_dist - cumul) / (seg_len + 1e-12)
                t = min(max(t, 0.0), 1.0)
                a = pts[seg_i]
                b = pts[(seg_i + 1) % len(pts)]
                lat = a[0] + (b[0] - a[0]) * t
                lon = a[1] + (b[1] - a[1]) * t

                v = Vessel(mmsi_base + s, f"{prefix}{s+1:02d}")
                v.lat = lat
                v.lon = lon
                v._waypoints = list(pts)
                v._wp_idx = seg_i
                v._wp_progress = t
                v.sog = 4.5
                ships.append(v)
                break
            cumul += seg_len

    return ships


# ──────────────── 선박 집합 구성 ────────────────

def make_circle_fleet(n=40):
    fleet = []
    for i in range(n):
        v = Vessel(990100000 + i, f"GC{i+1:03d}")
        v._circle_angle = (2 * math.pi / n) * i
        v._circle_speed = 0.006
        v.sog = 9.0
        # 초기 위치 설정
        v.lat = CENTER_LAT + R_LAT * math.sin(v._circle_angle)
        v.lon = CENTER_LON + R_LON * math.cos(v._circle_angle)
        fleet.append(v)
    return fleet


def make_jbu_fleet():
    """
    J B U 세 글자를 원 안에 꽉 차게 배치.

    글자 영역:
      - 높이: R_LAT * 1.75 (원 위아래 경계에 거의 닿게)
      - 폭:   R_LON * 1.55 (세 글자 합산)
      - 세 글자 중심 경도: 균등 3등분
    """
    letter_h   = R_LAT * 1.75          # 글자 세로 크기 (도)
    total_w    = R_LON * 1.55          # 세 글자 합산 가로 (도)
    letter_w   = total_w / 3.0         # 글자 한 개 가로 (도)

    c_lat = CENTER_LAT + R_LAT * 0.02  # 약간 위로 (시각 균형)

    # 각 글자 중심 경도
    lon_J = CENTER_LON - total_w / 2 + letter_w * 0.5
    lon_B = CENTER_LON
    lon_U = CENTER_LON + total_w / 2 - letter_w * 0.5

    N = 28  # 글자당 선박 수

    fleet = []

    j_pts = letter_J_raw(c_lat, lon_J, letter_w * 0.88, letter_h, seg=14)
    fleet += place_ships_on_path(j_pts, N, 990200000, "GJ")

    b_pts = letter_B_raw(c_lat, lon_B, letter_w * 0.88, letter_h, seg=14)
    fleet += place_ships_on_path(b_pts, N, 990300000, "GB")

    u_pts = letter_U_raw(c_lat, lon_U, letter_w * 0.88, letter_h, seg=14)
    fleet += place_ships_on_path(u_pts, N, 990400000, "GU")

    return fleet


def make_center_vessel():
    v = Vessel(440123456, "BUSAN ANCHOR", nav_status=1)
    v.lat = CENTER_LAT
    v.lon = CENTER_LON
    v.sog = 0.0
    v.cog = 0.0
    v.heading = 45
    return v


# ──────────────── 위치 업데이트 ────────────────

def update_circle_fleet(fleet, t):
    for v in fleet:
        angle = v._circle_angle + t * v._circle_speed
        v.lat = CENTER_LAT + R_LAT * math.sin(angle)
        v.lon = CENTER_LON + R_LON * math.cos(angle)
        dlat = R_LAT * math.cos(angle)
        dlon = -R_LON * math.sin(angle)
        v.cog = math.degrees(math.atan2(dlon, dlat)) % 360
        v.heading = int(v.cog)
        v.sog = 9.0 + 1.5 * math.sin(angle * 4)


def update_jbu_fleet(fleet, dt):
    cos_lat = math.cos(math.radians(CENTER_LAT))
    for v in fleet:
        wps = v._waypoints
        if len(wps) < 2:
            continue

        # SOG → 도/초 (위도 근사)
        step = v.sog * (1852 / 3600) / 111120 * dt

        cur = v._wp_idx % len(wps)
        nxt = (cur + 1) % len(wps)
        clat, clon = wps[cur]
        nlat, nlon = wps[nxt]
        dlat = nlat - clat
        dlon = (nlon - clon) * cos_lat
        seg_len = math.sqrt(dlat**2 + dlon**2)

        if seg_len < 1e-10:
            v._wp_idx = nxt
            continue

        v._wp_progress += step / seg_len

        # 웨이포인트 넘어가면 다음으로
        while v._wp_progress >= 1.0:
            v._wp_progress -= 1.0
            v._wp_idx = (v._wp_idx + 1) % len(wps)
            cur = v._wp_idx
            nxt = (cur + 1) % len(wps)
            clat, clon = wps[cur]
            nlat, nlon = wps[nxt]
            dlat = nlat - clat
            dlon = (nlon - clon) * cos_lat
            seg_len = math.sqrt(dlat**2 + dlon**2)
            if seg_len < 1e-10:
                break

        p = min(v._wp_progress, 1.0)
        clat, clon = wps[v._wp_idx % len(wps)]
        nlat, nlon = wps[(v._wp_idx + 1) % len(wps)]
        v.lat = clat + (nlat - clat) * p
        v.lon = clon + (nlon - clon) * p
        v.cog = math.degrees(math.atan2(nlon - clon, nlat - clat)) % 360
        v.heading = int(v.cog)


# ──────────────── UDP 송신 ────────────────

def send_nmea(sock, msg):
    try:
        sock.sendto(msg.encode("ascii"), (UDP_HOST, UDP_PORT))
    except Exception as e:
        print(f"[송신 오류] {e}")


def main():
    print("=" * 65)
    print("  OpenCPN 유령선박 UDP 테스트 송신기  v2")
    print(f"  목적지 : {UDP_HOST}:{UDP_PORT}")
    print(f"  중심   : {CENTER_LAT}°N, {CENTER_LON}°E  (부산 앞바다)")
    print(f"  원 반지름: 위도 {R_LAT:.2f}° ≈ {R_LAT*111:.0f}km")
    print("=" * 65)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    circle_fleet  = make_circle_fleet(40)
    jbu_fleet     = make_jbu_fleet()
    center_vessel = make_center_vessel()
    all_vessels   = circle_fleet + jbu_fleet + [center_vessel]
    name_sent     = set()

    n_jbu = len(jbu_fleet)
    print(f"\n선박 수:")
    print(f"  원형 선단  : {len(circle_fleet)}척")
    print(f"  JBU 선단   : {n_jbu}척  (글자당 {n_jbu//3}척)")
    print(f"  중앙 정상  : 1척")
    print(f"  합계       : {len(all_vessels)}척")
    print(f"\n[Ctrl+C 로 중단]\n")

    t = 0.0
    iteration = 0

    try:
        while True:
            iteration += 1
            t_start = time.time()

            update_circle_fleet(circle_fleet, t)
            update_jbu_fleet(jbu_fleet, UPDATE_INTERVAL)

            for v in all_vessels:
                if v.mmsi not in name_sent:
                    send_nmea(sock, v.name_message())
                    name_sent.add(v.mmsi)
                    time.sleep(0.008)
                send_nmea(sock, v.position_message())
                time.sleep(0.003)

            elapsed = time.time() - t_start
            t += UPDATE_INTERVAL

            if iteration % 10 == 0:
                j0 = jbu_fleet[0]
                b0 = jbu_fleet[n_jbu//3]
                u0 = jbu_fleet[n_jbu*2//3]
                print(f"[{iteration:4d}] | "
                      f"J[0]: {j0.lat:.4f}°N {j0.lon:.4f}°E | "
                      f"B[0]: {b0.lat:.4f}°N {b0.lon:.4f}°E | "
                      f"U[0]: {u0.lat:.4f}°N {u0.lon:.4f}°E")

            time.sleep(max(0, UPDATE_INTERVAL - elapsed))

    except KeyboardInterrupt:
        print("\n\n[중단] 송신 종료.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()