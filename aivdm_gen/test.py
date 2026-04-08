#!/usr/bin/env python3
"""
OpenCPN IDS Signal Generator  v4
AIS NMEA 0183 UDP Sender – Ghost Fleet Attack Simulator

패턴: 원형 / 격자 / 나선형 / 무작위
선단 이동: 속도(kn) + 방향(도) + 가속도(kn/분)
기본 좌표: 37°N / 121°E (서해 해역)
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


# ──────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────

ATTACK_OPTIONS = [
    ("circle", "원형 선단"),
    ("grid",   "격자 선단"),
    ("spiral", "나선형 선단"),
    ("random", "무작위 확산"),
]
ATTACK_LABEL_TO_KEY = {label: key for key, label in ATTACK_OPTIONS}

DEFAULT_SAMPLE_FILE = Path(__file__).with_name("nmea_data_sample.txt")
_KN_TO_DEG_PER_SEC = 1852.0 / 111320.0 / 3600.0   # knot → 위도°/s

log_queue: "queue.Queue[dict]" = queue.Queue()


# ──────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────

def _qlog(q, msg: str, level: str = "info") -> None:
    q.put({"kind": "log", "message": msg, "level": level})

def _qch(q, channel: str, state: str) -> None:
    q.put({"kind": "channel_state", "channel": channel, "state": state})

def _sleep(ev: threading.Event, sec: float) -> bool:
    """ev가 set되면 False, 시간이 다 되면 True 반환."""
    end = time.time() + max(0.0, sec)
    while not ev.is_set():
        rem = end - time.time()
        if rem <= 0:
            return True
        time.sleep(min(0.05, rem))
    return False

def nmea_checksum(s: str) -> str:
    v = 0
    for c in s:
        v ^= ord(c)
    return f"{v:02X}"

def encode_payload(bits: list[int]) -> str:
    while len(bits) % 6:
        bits.append(0)
    out = []
    for i in range(0, len(bits), 6):
        val = 0
        for b in bits[i:i+6]:
            val = (val << 1) | b
        cc = val + 48
        if cc > 87:
            cc += 8
        out.append(chr(cc))
    return "".join(out)

def build_vdm(mmsi, lat, lon, sog, cog, heading, nav_status=0) -> str:
    bits: list[int] = []
    def push(v: int, w: int):
        for i in range(w-1, -1, -1):
            bits.append((v >> i) & 1)
    push(1, 6); push(0, 2); push(mmsi, 30); push(nav_status, 4)
    push(0, 8); push(int(round(sog*10)) & 0x3FF, 10); push(1, 1)
    push(int(round(lon*600000)) & 0xFFFFFFF, 28)
    push(int(round(lat*600000)) & 0x7FFFFFF, 27)
    push(int(round(cog*10)) & 0xFFF, 12)
    push(heading % 360, 9)
    push(int(time.time()) % 60, 6)
    push(0, 2); push(0, 3); push(0, 1); push(0, 19)
    payload = encode_payload(bits)
    body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{body}*{nmea_checksum(body)}\r\n"

def build_vsd(mmsi: int, name: str) -> str:
    n = name[:20].upper().ljust(20, "@")
    bits: list[int] = []
    def push(v: int, w: int):
        for i in range(w-1, -1, -1):
            bits.append((v >> i) & 1)
    def push_str(s: str, w: int):
        for c in s[:w]:
            code = ord(c)
            if code >= 64:
                code -= 64
            push(code, 6)
    push(24, 6); push(0, 2); push(mmsi, 30); push(0, 2)
    push_str(n, 20); push(0, 8)
    payload = encode_payload(bits)
    body = f"AIVDM,1,1,,A,{payload},0"
    return f"!{body}*{nmea_checksum(body)}\r\n"

def load_nmea(path) -> list[str]:
    p = Path(path)
    with p.open("r", encoding="utf-8", errors="ignore") as f:
        msgs = [l.strip() + "\r\n" for l in f if l.strip().startswith("!AIVDM")]
    if not msgs:
        raise ValueError("파일에서 !AIVDM 문장을 찾지 못했습니다.")
    return msgs


# ──────────────────────────────────────────────────
# 선단 이동 오프셋
# ──────────────────────────────────────────────────

def translation_offset(cfg: dict, elapsed: float) -> tuple[float, float]:
    """
    선단 전체가 elapsed초 동안 이동한 누적 위경도 오프셋.
    v = move_speed + move_accel*(elapsed/60)   [kn]
    거리 = 평균속도 × elapsed
    """
    speed   = float(cfg.get("move_speed",   0.0))   # kn
    heading = float(cfg.get("move_heading", 0.0))   # 도
    accel   = float(cfg.get("move_accel",   0.0))   # kn/min

    # 등가속 이동: 평균속도 × 시간
    avg_speed = speed + accel * (elapsed / 60.0) / 2.0
    dist_deg  = avg_speed * _KN_TO_DEG_PER_SEC * elapsed   # 위도°

    rad  = math.radians(heading)
    dlat = math.cos(rad) * dist_deg
    dlon = math.sin(rad) * dist_deg * 1.2   # 경도 보정
    return dlat, dlon


# ──────────────────────────────────────────────────
# Vessel
# ──────────────────────────────────────────────────

class Vessel:
    def __init__(self, mmsi: int, name: str, nav_status: int = 0):
        self.mmsi       = mmsi
        self.name       = name
        self.nav_status = nav_status
        self.lat = self.lon = self.sog = self.cog = 0.0
        self.heading = 0

    def pos_msg(self) -> str:
        return build_vdm(self.mmsi, self.lat, self.lon,
                         self.sog, self.cog, self.heading, self.nav_status)

    def name_msg(self) -> str:
        return build_vsd(self.mmsi, self.name)


# ──────────────────────────────────────────────────
# 원형 선단  ★ COG/heading 자연스럽게 수정
# ──────────────────────────────────────────────────

def make_circle_fleet(cfg) -> list[Vessel]:
    clat   = float(cfg["center_lat"])
    clon   = float(cfg["center_lon"])
    count  = int(cfg["circle_count"])
    radius = float(cfg["circle_radius"])
    speed  = float(cfg["circle_speed"])
    mode   = str(cfg.get("circle_mode", "rotate"))

    fleet = []
    for i in range(count):
        angle = (2 * math.pi / count) * i
        v = Vessel(990100000 + i, f"GHOST-C{i+1:02d}")
        v._angle0       = angle          # 초기 위상
        v._ang_speed    = speed          # rad/s  (양수=시계방향)
        v._center_lat   = clat
        v._center_lon   = clon
        v._radius       = radius
        v._base_radius  = radius
        v._mode         = mode
        fleet.append(v)
    return fleet


def update_circle_fleet(fleet: list[Vessel], elapsed: float, cfg: dict) -> None:
    dlat, dlon      = translation_offset(cfg, elapsed)
    converge_rate   = float(cfg.get("circle_converge_rate", 0.001))
    ang_speed       = float(cfg.get("circle_speed", 0.008))

    for v in fleet:
        if not hasattr(v, "_angle0"):
            continue

        # ── 현재 각도 (시계방향: 북=0, 동=π/2 → angle 기준은 수학 좌표계)
        # 수학 좌표계에서 시계방향은 angle 감소 방향
        angle = v._angle0 - ang_speed * elapsed   # 시계방향 회전

        # 수렴/발산 반지름 조정
        if v._mode == "converge":
            v._radius = max(0.001, v._base_radius - converge_rate * elapsed)
        elif v._mode == "diverge":
            v._radius = v._base_radius + converge_rate * elapsed
        else:
            v._radius = v._base_radius

        cx = v._center_lat + dlat
        cy = v._center_lon + dlon

        # ── 위치: 수학 좌표계 기준
        #   lat = cx + r*sin(angle)   (북쪽이 양수)
        #   lon = cy + r*cos(angle)*1.2
        v.lat = cx + v._radius * math.sin(angle)
        v.lon = cy + v._radius * math.cos(angle) * 1.2

        # ── COG: 시계방향 접선 방향
        # 시계방향 접선은 현재 각도에서 -π/2 (수학 좌표계)
        # 실제 지리 헤딩 = atan2(경도변화, 위도변화) 를 AIS 진북 기준으로
        # 접선 벡터 (시계방향): d(lat)/dt = -r·cos(angle)·ω (음수angspeed → 양수ω)
        #                        d(lon)/dt = +r·sin(angle)·ω
        tangent_lat = v._radius * math.cos(angle)   # ω가 1이면 dlat/dt = +cos(angle) (반시계)
        tangent_lon = -v._radius * math.sin(angle)  # → 시계방향은 부호 반전
        # AIS COG: atan2(동쪽, 북쪽) → atan2(dlon, dlat)
        cog_rad = math.atan2(tangent_lon, tangent_lat)
        v.cog     = cog_rad * 180.0 / math.pi % 360
        v.heading = int(round(v.cog)) % 360

        # SOG: 각속도 × 반지름 → kn 단위로 근사
        v.sog = max(0.5, ang_speed * v._radius * 111320 / 1852)


# ──────────────────────────────────────────────────
# 격자 선단
# ──────────────────────────────────────────────────

def make_grid_fleet(cfg) -> list[Vessel]:
    clat    = float(cfg["center_lat"])
    clon    = float(cfg["center_lon"])
    rows    = int(cfg["grid_rows"])
    cols    = int(cfg["grid_cols"])
    spacing = float(cfg["grid_spacing"])
    speed   = float(cfg["grid_speed"])
    heading = float(cfg["grid_heading"])

    fleet = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            v = Vessel(990500000 + idx, f"GHOST-G{idx+1:02d}")
            v._base_lat = clat + (r - rows/2) * spacing
            v._base_lon = clon + (c - cols/2) * spacing * 1.2
            v.lat = v._base_lat
            v.lon = v._base_lon
            v.sog     = speed
            v.cog     = heading
            v.heading = int(heading)
            v._drift_lat = random.uniform(-0.0002, 0.0002)
            v._drift_lon = random.uniform(-0.0002, 0.0002)
            fleet.append(v)
            idx += 1
    return fleet


def update_grid_fleet(fleet: list[Vessel], dt: float, elapsed: float, cfg: dict) -> None:
    dlat, dlon   = translation_offset(cfg, elapsed)
    rotate_dpm   = float(cfg.get("grid_rotate", 0.0))   # 도/분
    rot           = math.radians(rotate_dpm * elapsed / 60.0)
    cos_r, sin_r  = math.cos(rot), math.sin(rot)
    clat = float(cfg["center_lat"])
    clon = float(cfg["center_lon"])

    for v in fleet:
        if not hasattr(v, "_base_lat"):
            continue
        rad  = math.radians(v.cog)
        step = v.sog * _KN_TO_DEG_PER_SEC * dt
        v._base_lat += math.cos(rad) * step + v._drift_lat * dt * 0.1
        v._base_lon += math.sin(rad) * step * 1.2 + v._drift_lon * dt * 0.1

        if rotate_dpm != 0:
            y = v._base_lat - clat
            x = v._base_lon - clon
            v.lat = clat + y*cos_r - x*sin_r + dlat
            v.lon = clon + y*sin_r + x*cos_r + dlon
        else:
            v.lat = v._base_lat + dlat
            v.lon = v._base_lon + dlon


# ──────────────────────────────────────────────────
# 나선형 선단
# ──────────────────────────────────────────────────

def make_spiral_fleet(cfg) -> list[Vessel]:
    clat       = float(cfg["center_lat"])
    clon       = float(cfg["center_lon"])
    count      = int(cfg["spiral_count"])
    turns      = float(cfg["spiral_turns"])
    max_radius = float(cfg["spiral_max_radius"])
    speed      = float(cfg["spiral_speed"])

    fleet = []
    for i in range(count):
        ratio = i / max(count-1, 1)
        angle = 2 * math.pi * turns * ratio
        r     = max_radius * ratio
        v = Vessel(990600000 + i, f"GHOST-S{i+1:02d}")
        v.lat = clat + r * math.sin(angle)
        v.lon = clon + r * math.cos(angle) * 1.2
        v._idx         = i
        v._count       = count
        v._turns       = turns
        v._center_lat  = clat
        v._center_lon  = clon
        v._base_max_r  = max_radius
        v._ang_speed   = speed
        fleet.append(v)
    return fleet


def update_spiral_fleet(fleet: list[Vessel], elapsed: float, cfg: dict) -> None:
    dlat, dlon   = translation_offset(cfg, elapsed)
    expand_rate  = float(cfg.get("spiral_expand_rate", 0.0))  # 비율/분 (소수)

    for v in fleet:
        if not hasattr(v, "_idx"):
            continue
        ratio  = v._idx / max(v._count-1, 1)
        angle  = 2 * math.pi * v._turns * ratio + elapsed * v._ang_speed
        eff_r  = max(0.001, v._base_max_r * (1 + expand_rate * elapsed / 60.0))
        r      = eff_r * ratio

        cx = v._center_lat + dlat
        cy = v._center_lon + dlon
        v.lat = cx + r * math.sin(angle)
        v.lon = cy + r * math.cos(angle) * 1.2

        # COG: 나선 접선 (회전 방향)
        cog_rad = math.atan2(-math.cos(angle), math.sin(angle))
        v.cog     = cog_rad * 180.0 / math.pi % 360
        v.heading = int(round(v.cog)) % 360
        v.sog     = max(0.5, v._ang_speed * r * 111320 / 1852)


# ──────────────────────────────────────────────────
# 무작위 확산
# ──────────────────────────────────────────────────

def make_random_fleet(cfg) -> list[Vessel]:
    clat   = float(cfg["center_lat"])
    clon   = float(cfg["center_lon"])
    count  = int(cfg["random_count"])
    spread = float(cfg["random_spread"])

    fleet = []
    for i in range(count):
        v = Vessel(990700000 + i, f"GHOST-R{i+1:02d}")
        v.lat     = clat + random.uniform(-spread, spread)
        v.lon     = clon + random.uniform(-spread*1.2, spread*1.2)
        v.sog     = random.uniform(2.0, 15.0)
        v.cog     = random.uniform(0, 360)
        v.heading = int(v.cog)
        v._drift_lat = random.uniform(-0.001, 0.001)
        v._drift_lon = random.uniform(-0.001, 0.001)
        fleet.append(v)
    return fleet


def update_random_fleet(fleet: list[Vessel], dt: float, elapsed: float, cfg: dict) -> None:
    dlat_t, dlon_t      = translation_offset(cfg, elapsed)
    converge_lat        = cfg.get("random_converge_lat")
    converge_lon        = cfg.get("random_converge_lon")
    converge_strength   = float(cfg.get("random_converge_strength", 0.0))

    for v in fleet:
        if not hasattr(v, "_drift_lat"):
            continue
        v.cog = (v.cog + random.uniform(-5, 5)) % 360

        if converge_strength > 0 and converge_lat is not None:
            tlat = float(converge_lat) + dlat_t
            tlon = float(converge_lon) + dlon_t
            dlat_ = tlat - v.lat
            dlon_ = tlon - v.lon
            dist  = math.sqrt(dlat_**2 + dlon_**2) + 1e-9
            pull  = converge_strength * dt * 0.0001
            v.lat += (dlat_ / dist) * pull
            v.lon += (dlon_ / dist) * pull
            v.cog = math.degrees(math.atan2(dlon_, dlat_)) % 360
        else:
            rad   = math.radians(v.cog)
            step  = v.sog * _KN_TO_DEG_PER_SEC * dt
            v.lat += math.cos(rad) * step
            v.lon += math.sin(rad) * step

        # 집단 이동 (작은 bias)
        v.lat += dlat_t * dt * 0.0001
        v.lon += dlon_t * dt * 0.0001
        v.heading = int(v.cog) % 360


# ──────────────────────────────────────────────────
# 앵커 선박
# ──────────────────────────────────────────────────

def make_anchor_vessel(cfg) -> Vessel:
    v = Vessel(440123456, "BUSAN ANCHOR", nav_status=1)
    v.lat = float(cfg["center_lat"])
    v.lon = float(cfg["center_lon"])
    return v


# ──────────────────────────────────────────────────
# 빌드 / 업데이트 디스패처
# ──────────────────────────────────────────────────

def build_fleet(cfg) -> list[Vessel]:
    key = str(cfg["attack_key"])
    builders = {
        "circle": make_circle_fleet,
        "grid":   make_grid_fleet,
        "spiral": make_spiral_fleet,
        "random": make_random_fleet,
    }
    if key not in builders:
        raise ValueError(f"지원하지 않는 패턴: {key}")
    fleet = builders[key](cfg)
    if cfg.get("add_anchor"):
        fleet.append(make_anchor_vessel(cfg))
    return fleet


def update_fleet(fleet, key: str, elapsed: float, dt: float, cfg: dict) -> None:
    if key == "circle":
        update_circle_fleet(fleet, elapsed, cfg)
    elif key == "grid":
        update_grid_fleet(fleet, dt, elapsed, cfg)
    elif key == "spiral":
        update_spiral_fleet(fleet, elapsed, cfg)
    elif key == "random":
        update_random_fleet(fleet, dt, elapsed, cfg)


# ──────────────────────────────────────────────────
# 송신 루프
# ──────────────────────────────────────────────────

def send_generated_loop(cfg, log_q, stop: threading.Event) -> bool:
    host        = str(cfg["host"])
    port        = int(cfg["port"])
    interval    = float(cfg["interval"])
    attack_key  = str(cfg["attack_key"])
    attack_lbl  = str(cfg["attack_label"])
    move_speed  = float(cfg.get("move_speed", 0.0))
    move_hdg    = float(cfg.get("move_heading", 0.0))

    fleet      = build_fleet(cfg)
    name_sent: set[int] = set()
    start_time = time.time()
    iteration  = 0

    _qlog(log_q, f"[생성 시작] 패턴: {attack_lbl} | 선박 {len(fleet)}척", "start")
    _qlog(log_q, f"[전송] {host}:{port} | 주기 {interval:.2f}s", "info")
    if move_speed > 0:
        _qlog(log_q, f"[이동] {move_speed:.1f}kn @ {move_hdg:.0f}°", "info")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        prev_time = start_time
        while not stop.is_set():
            iteration += 1
            now     = time.time()
            elapsed = now - start_time
            dt      = now - prev_time
            prev_time = now

            update_fleet(fleet, attack_key, elapsed, dt, cfg)

            sent = 0
            for v in fleet:
                if stop.is_set():
                    return False
                if v.mmsi not in name_sent:
                    sock.sendto(v.name_msg().encode("ascii"), (host, port))
                    name_sent.add(v.mmsi)
                    if not _sleep(stop, 0.01):
                        return False
                sock.sendto(v.pos_msg().encode("ascii"), (host, port))
                sent += 1
                if not _sleep(stop, 0.005):
                    return False

            cycle_elapsed = time.time() - now
            if iteration == 1 or iteration % 5 == 0:
                dlat, dlon = translation_offset(cfg, elapsed)
                _qlog(log_q,
                      f"[생성] {iteration}회차 | {sent}척 | {cycle_elapsed:.2f}s"
                      f" | 오프셋 Δlat={dlat:+.4f} Δlon={dlon:+.4f}", "info")

            if not _sleep(stop, max(0.0, interval - cycle_elapsed)):
                return False
    return False


def send_file_loop(cfg, log_q, stop: threading.Event) -> bool:
    host     = str(cfg["host"])
    port     = int(cfg["port"])
    fpath    = Path(str(cfg["file_path"]))
    interval = float(cfg["file_interval"])
    repeat   = bool(cfg["file_repeat"])
    msgs     = load_nmea(fpath)

    _qlog(log_q, f"[파일 시작] {fpath.name} | AIVDM {len(msgs)}개", "start")
    _qlog(log_q, f"[전송] {host}:{port} | 간격 {interval:.2f}s | 반복 {'켜짐' if repeat else '꺼짐'}", "info")

    sent_total = 0
    cycle = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        while not stop.is_set():
            cycle += 1
            for idx, msg in enumerate(msgs, 1):
                if stop.is_set():
                    return False
                sock.sendto(msg.encode("ascii"), (host, port))
                sent_total += 1
                _qlog(log_q, f"[파일 {idx:04d}] {msg.strip()}", "info")
                if not _sleep(stop, interval):
                    return False
            if not repeat:
                _qlog(log_q, f"[파일 완료] 총 {sent_total}건", "start")
                return True
            _qlog(log_q, f"[파일 반복] {cycle}회차 완료 | 누적 {sent_total}건", "info")
    return False


def worker(channel: str, cfg, log_q, stop: threading.Event) -> None:
    completed = False
    try:
        if channel == "generated":
            completed = send_generated_loop(cfg, log_q, stop)
        else:
            completed = send_file_loop(cfg, log_q, stop)
    except Exception as e:
        _qlog(log_q, f"[오류] {e}", "error")
    finally:
        label = "생성" if channel == "generated" else "파일"
        if stop.is_set():
            _qlog(log_q, f"[{label} 종료] 사용자 중단", "start")
        elif completed:
            _qlog(log_q, f"[{label} 종료] 송신 완료", "start")
        else:
            _qlog(log_q, f"[{label} 종료] 스레드 종료", "start")
        _qch(log_q, channel, "finished")


# ──────────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OpenCPN IDS Signal Generator  v4")
        self.configure(bg="#09111d")
        self.minsize(1020, 720)
        self.resizable(True, True)
        self.gen_thread: threading.Thread | None = None
        self.file_thread: threading.Thread | None = None
        self.gen_stop  = threading.Event()
        self.file_stop = threading.Event()

        self._styles()
        self._build_ui()
        self._set_state("generated", False)
        self._set_state("file", False)
        self._on_attack_change()
        self._poll()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 스타일 ─────────────────────────────────────
    def _styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        BG, AC, FG = "#09111d", "#35d0ff", "#edf4ff"
        SUB, ENT, HL = "#9db0c7", "#172334", "#24354d"
        s.configure(".", background=BG, foreground=FG, font=("Consolas", 10))
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=FG, font=("Consolas", 10))
        s.configure("H.TLabel", background=BG, foreground=AC, font=("Consolas", 13, "bold"))
        s.configure("S.TLabel", background=BG, foreground=SUB, font=("Consolas", 9))
        s.configure("A.TLabel", background=BG, foreground="#ffcc44", font=("Consolas", 10, "bold"))
        s.configure("TEntry",   fieldbackground=ENT, foreground="#fff", insertcolor=AC, borderwidth=0)
        s.configure("TSpinbox", fieldbackground=ENT, foreground="#fff", background=ENT,
                     arrowcolor=AC, borderwidth=0)
        s.configure("TCombobox", fieldbackground=ENT, foreground="#fff",
                     selectbackground=AC, selectforeground=BG)
        s.map("TCombobox", fieldbackground=[("readonly", ENT)], foreground=[("readonly", "#fff")])
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.map("TCheckbutton", background=[("active", BG)])

    # ── 빌더 헬퍼 ──────────────────────────────────
    def _sec(self, parent, title: str, accent=False):
        f = ttk.Frame(parent)
        f.pack(fill="x", padx=10, pady=(10, 2))
        ttk.Label(f, text=title, style="A.TLabel" if accent else "H.TLabel").pack(anchor="w")
        tk.Frame(parent, height=1, bg="#24354d").pack(fill="x", padx=10, pady=(0, 6))

    def _row(self, parent, label: str, factory, **kw):
        r = ttk.Frame(parent)
        r.pack(fill="x", padx=16, pady=2)
        ttk.Label(r, text=label, width=26, anchor="w", style="S.TLabel").pack(side="left")
        w = factory(r, **kw)
        w.pack(side="left", fill="x", expand=True)
        return w

    def _entry(self, parent, default="", **kw):
        var = tk.StringVar(value=str(default))
        e = ttk.Entry(parent, textvariable=var, **kw)
        e._var = var
        return e

    def _spin(self, parent, from_, to, default, step=1.0):
        var = tk.DoubleVar(value=default)
        s = ttk.Spinbox(parent, from_=from_, to=to, increment=step,
                         textvariable=var, font=("Consolas", 10))
        s._var = var
        return s

    def _combo(self, parent, values, default):
        var = tk.StringVar(value=default)
        c = ttk.Combobox(parent, textvariable=var, values=values,
                          state="readonly", font=("Consolas", 10))
        c._var = var
        return c

    # ── UI 빌드 ─────────────────────────────────────
    def _build_ui(self):
        # 타이틀바
        tb = ttk.Frame(self); tb.pack(fill="x")
        tk.Label(tb, text="  OPENCPN IDS SIGNAL GENERATOR  v4",
                 bg="#35d0ff", fg="#08101a", font=("Consolas", 14, "bold"),
                 padx=10, pady=8).pack(fill="x")
        tk.Label(tb, text="  AIS NMEA 0183 UDP Sender  |  Ghost Fleet Attack Simulator",
                 bg="#112033", fg="#8aa1bb", font=("Consolas", 9),
                 padx=10, pady=3).pack(fill="x")

        main = ttk.Frame(self); main.pack(fill="both", expand=True)

        left = ttk.Frame(main); left.pack(side="left", fill="both")
        tk.Frame(main, width=1, bg="#24354d").pack(side="left", fill="y")
        right = ttk.Frame(main); right.pack(side="right", fill="both", expand=True)

        # 스크롤 캔버스
        canvas = tk.Canvas(left, bg="#09111d", highlightthickness=0, width=500)
        sb = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        self.sf = ttk.Frame(canvas)
        self.sf.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                         lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        sf = self.sf
        self._build_network(sf)

        self.gen_panel = ttk.Frame(sf); self.gen_panel.pack(fill="x")
        gp = self.gen_panel
        self._build_center(gp)
        self._build_movement(gp)
        self._build_attack_selector(gp)
        self._build_circle(gp)
        self._build_grid(gp)
        self._build_spiral(gp)
        self._build_random(gp)
        self._build_extra(gp)

        self.file_panel = ttk.Frame(sf); self.file_panel.pack(fill="x")
        self._build_file(self.file_panel)

        self.ctrl_panel = self._build_controls(sf)

        # 로그
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

    def _build_network(self, p):
        self._sec(p, "네트워크")
        self.host_entry    = self._row(p, "대상 IP",          self._entry, default="127.0.0.1")
        self.port_entry    = self._row(p, "UDP 포트",         self._entry, default="1111")
        self.interval_spin = self._row(p, "생성 신호 주기(초)", self._spin,
                                        from_=0.2, to=30.0, default=2.0, step=0.1)

    def _build_center(self, p):
        self._sec(p, "기준 좌표  (기본: 서해 37°N 121°E)")
        self.lat_entry = self._row(p, "중심 위도 (N)", self._entry, default="37.00")
        self.lon_entry = self._row(p, "중심 경도 (E)", self._entry, default="121.00")

    def _build_movement(self, p):
        self._sec(p, "★ 선단 이동 제어", accent=True)
        self.move_speed   = self._row(p, "이동 속도 (kn)",       self._spin,
                                       from_=0.0, to=30.0, default=0.0, step=0.5)
        self.move_heading = self._row(p, "이동 방향 (도, 진북=0)", self._spin,
                                       from_=0, to=359, default=0, step=5)
        self.move_accel   = self._row(p, "가속도 (kn/분)",        self._spin,
                                       from_=0.0, to=5.0, default=0.0, step=0.1)

    def _build_attack_selector(self, p):
        self._sec(p, "생성 패턴")
        row = ttk.Frame(p); row.pack(fill="x", padx=16, pady=4)
        self.attack_var = tk.StringVar(value=ATTACK_OPTIONS[0][1])
        cb = ttk.Combobox(row, textvariable=self.attack_var,
                           values=[lbl for _, lbl in ATTACK_OPTIONS],
                           state="readonly", font=("Consolas", 11))
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", self._on_attack_change)

    def _build_circle(self, p):
        self.circle_frame = ttk.Frame(p); self.circle_frame.pack(fill="x")
        cf = self.circle_frame
        self._sec(cf, "원형 선단 설정")
        self.circle_count  = self._row(cf, "선박 수",               self._spin,
                                        from_=1, to=100, default=15, step=1)
        self.circle_radius = self._row(cf, "반지름 (도)",            self._spin,
                                        from_=0.01, to=2.0, default=0.22, step=0.01)
        self.circle_speed  = self._row(cf, "각속도 (rad/s)",         self._spin,
                                        from_=0.001, to=0.2, default=0.008, step=0.001)
        self.circle_mode   = self._row(cf, "모드",                   self._combo,
                                        values=["rotate","converge","diverge"],
                                        default="rotate")
        self.circle_conv   = self._row(cf, "수렴/발산 속도 (도/s)",   self._spin,
                                        from_=0.0, to=0.02, default=0.001, step=0.0005)

    def _build_grid(self, p):
        self.grid_frame = ttk.Frame(p); self.grid_frame.pack(fill="x")
        gf = self.grid_frame
        self._sec(gf, "격자 선단 설정")
        self.grid_rows    = self._row(gf, "행 수",           self._spin, from_=1, to=20, default=5, step=1)
        self.grid_cols    = self._row(gf, "열 수",           self._spin, from_=1, to=20, default=5, step=1)
        self.grid_spacing = self._row(gf, "간격 (도)",        self._spin, from_=0.005, to=0.5, default=0.05, step=0.005)
        self.grid_speed   = self._row(gf, "속도 (kn)",       self._spin, from_=0, to=30, default=5.0, step=0.5)
        self.grid_heading = self._row(gf, "진행 방향 (도)",   self._spin, from_=0, to=359, default=0, step=5)
        self.grid_rotate  = self._row(gf, "선단 회전 (도/분)", self._spin, from_=-30.0, to=30.0, default=0.0, step=1.0)

    def _build_spiral(self, p):
        self.spiral_frame = ttk.Frame(p); self.spiral_frame.pack(fill="x")
        sf = self.spiral_frame
        self._sec(sf, "나선형 선단 설정")
        self.spiral_count  = self._row(sf, "선박 수",              self._spin, from_=3, to=60, default=20, step=1)
        self.spiral_turns  = self._row(sf, "회전 수",              self._spin, from_=0.5, to=5.0, default=2.0, step=0.5)
        self.spiral_max_r  = self._row(sf, "최대 반지름 (도)",      self._spin, from_=0.05, to=1.5, default=0.30, step=0.01)
        self.spiral_speed  = self._row(sf, "회전 속도 (rad/s)",    self._spin, from_=0.001, to=0.1, default=0.005, step=0.001)
        self.spiral_expand = self._row(sf, "반지름 팽창율 (%/분)",  self._spin, from_=-50.0, to=100.0, default=0.0, step=5.0)

    def _build_random(self, p):
        self.random_frame = ttk.Frame(p); self.random_frame.pack(fill="x")
        rf = self.random_frame
        self._sec(rf, "무작위 확산 설정")
        self.random_count    = self._row(rf, "선박 수",            self._spin, from_=1, to=150, default=30, step=1)
        self.random_spread   = self._row(rf, "확산 반경 (도)",      self._spin, from_=0.05, to=2.0, default=0.30, step=0.05)
        self.random_conv_str = self._row(rf, "수렴 강도 (0=없음)",  self._spin, from_=0.0, to=10.0, default=0.0, step=0.5)
        self.random_conv_lat = self._row(rf, "수렴 위도",           self._entry, default="37.00")
        self.random_conv_lon = self._row(rf, "수렴 경도",           self._entry, default="121.00")

    def _build_extra(self, p):
        self._sec(p, "추가 옵션")
        row = ttk.Frame(p); row.pack(fill="x", padx=16, pady=4)
        self.anchor_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="중앙 정박선 추가 (MMSI: 440123456)",
                         variable=self.anchor_var).pack(anchor="w")

    def _build_file(self, p):
        self._sec(p, "정상 신호 파일")
        fr = ttk.Frame(p); fr.pack(fill="x", padx=16, pady=2)
        ttk.Label(fr, text="NMEA 파일", width=26, anchor="w", style="S.TLabel").pack(side="left")
        default_path = str(DEFAULT_SAMPLE_FILE if DEFAULT_SAMPLE_FILE.exists() else "")
        self.file_path_var = tk.StringVar(value=default_path)
        ttk.Entry(fr, textvariable=self.file_path_var).pack(side="left", fill="x", expand=True)
        tk.Button(fr, text="찾기", bg="#172334", fg="#edf4ff", relief="flat",
                   activebackground="#24354d", command=self._browse,
                   padx=12, pady=4).pack(side="left", padx=(6, 0))
        self.file_interval = self._row(p, "문장 간격(초)", self._spin,
                                        from_=0.01, to=5.0, default=0.1, step=0.01)
        rr = ttk.Frame(p); rr.pack(fill="x", padx=16, pady=4)
        self.file_repeat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rr, text="파일 끝까지 전송 후 반복",
                         variable=self.file_repeat_var).pack(anchor="w")

    def _build_controls(self, p) -> ttk.Frame:
        wrap = ttk.Frame(p); wrap.pack(fill="x")
        ttk.Separator(wrap, orient="horizontal").pack(fill="x", padx=10, pady=10)
        ctrl = ttk.Frame(wrap); ctrl.pack(fill="x", padx=10, pady=(0, 16))

        def btn(parent, text, bg, fg, cmd, abg):
            return tk.Button(parent, text=text, bg=bg, fg=fg,
                              font=("Consolas", 11, "bold"), activebackground=abg,
                              relief="flat", cursor="hand2", padx=16, pady=7, command=cmd)

        gr = ttk.Frame(ctrl); gr.pack(fill="x", pady=(0, 6))
        ttk.Label(gr, text="생성 신호", width=12, anchor="w", style="S.TLabel").pack(side="left")
        self.gen_start = btn(gr, "생성 시작", "#35d0ff", "#08101a", self.start_gen,  "#67ddff")
        self.gen_start.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.gen_stop  = btn(gr, "생성 중단", "#172334", "#ff7c7c", self.stop_gen,   "#24354d")
        self.gen_stop.pack(side="left", fill="x", expand=True, padx=(4, 0))

        fr2 = ttk.Frame(ctrl); fr2.pack(fill="x", pady=(0, 6))
        ttk.Label(fr2, text="정상 파일", width=12, anchor="w", style="S.TLabel").pack(side="left")
        self.file_start = btn(fr2, "파일 시작", "#7ef0c9", "#08101a", self.start_file, "#9af6d8")
        self.file_start.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.file_stop  = btn(fr2, "파일 중단", "#172334", "#ffb36b", self.stop_file,  "#24354d")
        self.file_stop.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.all_stop = btn(ctrl, "전체 중단", "#24354d", "#edf4ff", self.stop_all, "#304663")
        self.all_stop.pack(fill="x")
        return wrap

    # ── 이벤트 ─────────────────────────────────────
    def _browse(self):
        d = DEFAULT_SAMPLE_FILE.parent if DEFAULT_SAMPLE_FILE.exists() else Path.cwd()
        f = filedialog.askopenfilename(
            title="NMEA 파일 선택", initialdir=str(d),
            filetypes=[("NMEA files", "*.txt *.nmea *.log"), ("All files", "*.*")])
        if f:
            self.file_path_var.set(f)

    def _on_attack_change(self, _=None):
        key = ATTACK_LABEL_TO_KEY[self.attack_var.get()]
        frames = {"circle": self.circle_frame, "grid": self.grid_frame,
                  "spiral": self.spiral_frame, "random": self.random_frame}
        for k, fr in frames.items():
            if k == key:
                if not fr.winfo_manager():
                    fr.pack(fill="x")
            elif fr.winfo_manager():
                fr.pack_forget()

    # ── 설정 수집 ───────────────────────────────────
    def _common_cfg(self):
        host = self.host_entry._var.get().strip()
        if not host:
            raise ValueError("대상 IP를 입력하세요.")
        port = int(self.port_entry._var.get())
        if not 1 <= port <= 65535:
            raise ValueError("UDP 포트는 1~65535 범위여야 합니다.")
        return {"host": host, "port": port}

    def _gen_cfg(self):
        cfg = self._common_cfg()
        interval = float(self.interval_spin._var.get())
        if interval <= 0:
            raise ValueError("생성 신호 주기는 0보다 커야 합니다.")
        attack_lbl = self.attack_var.get()

        try:
            rcl = float(self.random_conv_lat._var.get())
            rcn = float(self.random_conv_lon._var.get())
        except Exception:
            rcl = float(self.lat_entry._var.get())
            rcn = float(self.lon_entry._var.get())

        cfg.update({
            "interval":     interval,
            "center_lat":   float(self.lat_entry._var.get()),
            "center_lon":   float(self.lon_entry._var.get()),
            "attack_key":   ATTACK_LABEL_TO_KEY[attack_lbl],
            "attack_label": attack_lbl,
            "add_anchor":   self.anchor_var.get(),
            # 이동
            "move_speed":   float(self.move_speed._var.get()),
            "move_heading": float(self.move_heading._var.get()),
            "move_accel":   float(self.move_accel._var.get()),
            # 원형
            "circle_count":         min(200, max(1, int(float(self.circle_count._var.get())))),
            "circle_radius":        float(self.circle_radius._var.get()),
            "circle_speed":         float(self.circle_speed._var.get()),
            "circle_mode":          self.circle_mode._var.get(),
            "circle_converge_rate": float(self.circle_conv._var.get()),
            # 격자
            "grid_rows":    min(30, max(1, int(float(self.grid_rows._var.get())))),
            "grid_cols":    min(30, max(1, int(float(self.grid_cols._var.get())))),
            "grid_spacing": float(self.grid_spacing._var.get()),
            "grid_speed":   float(self.grid_speed._var.get()),
            "grid_heading": float(self.grid_heading._var.get()),
            "grid_rotate":  float(self.grid_rotate._var.get()),
            # 나선
            "spiral_count":       min(200, max(3, int(float(self.spiral_count._var.get())))),
            "spiral_turns":       float(self.spiral_turns._var.get()),
            "spiral_max_radius":  float(self.spiral_max_r._var.get()),
            "spiral_speed":       float(self.spiral_speed._var.get()),
            "spiral_expand_rate": float(self.spiral_expand._var.get()) / 100.0,
            # 무작위
            "random_count":             min(300, max(1, int(float(self.random_count._var.get())))),
            "random_spread":            float(self.random_spread._var.get()),
            "random_converge_strength": float(self.random_conv_str._var.get()),
            "random_converge_lat":      rcl,
            "random_converge_lon":      rcn,
        })
        return cfg

    def _file_cfg(self):
        cfg = self._common_cfg()
        fpath = Path(self.file_path_var.get().strip())
        if not fpath.exists():
            raise ValueError("정상 신호 파일 경로를 확인하세요.")
        interval = float(self.file_interval._var.get())
        if interval <= 0:
            raise ValueError("문장 간격은 0보다 커야 합니다.")
        cfg.update({"file_path": str(fpath), "file_interval": interval,
                     "file_repeat": self.file_repeat_var.get()})
        return cfg

    # ── 채널 상태 ───────────────────────────────────
    def _any_running(self):
        return (
            (self.gen_thread  is not None and self.gen_thread.is_alive()) or
            (self.file_thread is not None and self.file_thread.is_alive())
        )

    def _set_state(self, ch: str, running: bool):
        if ch == "generated":
            self.gen_start.config(
                state="disabled" if running else "normal",
                bg="#172334" if running else "#35d0ff",
                fg="#5f738c" if running else "#08101a")
            self.gen_stop.config(state="normal" if running else "disabled")
        else:
            self.file_start.config(
                state="disabled" if running else "normal",
                bg="#172334" if running else "#7ef0c9",
                fg="#5f738c" if running else "#08101a")
            self.file_stop.config(state="normal" if running else "disabled")
        self.all_stop.config(state="normal" if self._any_running() or running else "disabled")

    # ── 송신 제어 ───────────────────────────────────
    def start_gen(self):
        if self.gen_thread and self.gen_thread.is_alive():
            self.log("[생성] 이미 실행 중", "error"); return
        try:
            cfg = self._gen_cfg()
        except ValueError as e:
            messagebox.showerror("입력 오류", str(e)); return
        self.gen_stop = threading.Event()
        self._set_state("generated", True)
        self.log("[생성 대기] 송신 스레드 시작", "start")
        self.gen_thread = threading.Thread(
            target=worker, args=("generated", cfg, log_queue, self.gen_stop), daemon=True)
        self.gen_thread.start()

    def stop_gen(self):
        if self.gen_thread and self.gen_thread.is_alive():
            self.gen_stop.set()
            self.log("[생성 중단] 사용자 중단 요청", "error")

    def start_file(self):
        if self.file_thread and self.file_thread.is_alive():
            self.log("[파일] 이미 실행 중", "error"); return
        try:
            cfg = self._file_cfg()
        except ValueError as e:
            messagebox.showerror("입력 오류", str(e)); return
        self.file_stop = threading.Event()
        self._set_state("file", True)
        self.log("[파일 대기] 송신 스레드 시작", "start")
        self.file_thread = threading.Thread(
            target=worker, args=("file", cfg, log_queue, self.file_stop), daemon=True)
        self.file_thread.start()

    def stop_file(self):
        if self.file_thread and self.file_thread.is_alive():
            self.file_stop.set()
            self.log("[파일 중단] 사용자 중단 요청", "error")

    def stop_all(self):
        self.stop_gen()
        self.stop_file()

    def log(self, msg: str, level: str = "info"):
        ts = time.strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{ts}] {msg}\n", level)
        self.log_box.see("end")

    def _poll(self):
        while not log_queue.empty():
            item = log_queue.get_nowait()
            if item.get("kind") == "channel_state" and item.get("state") == "finished":
                self._set_state(item.get("channel", ""), False)
                continue
            self.log(item["message"], item.get("level", "info"))
        self.after(200, self._poll)

    def _on_close(self):
        self.gen_stop.set()
        self.file_stop.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()