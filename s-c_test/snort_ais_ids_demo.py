#!/usr/bin/env python3
"""
snort_ais_ids_demo.py
---------------------
AIS-IDS ML 탐지 모델을 Snort 스타일로 구동하는 데모.

[동작 모드]
  1. --pcap <file>  : PCAP 파일에서 UDP 패킷을 읽어 AIVDM 분석
  2. --udp  <port>  : UDP 소켓으로 실시간 AIVDM 수신 (기본 10110)
  3. --replay       : 내장 시나리오(정상/이상)로 데모 실행 (기본)

[필요 패키지]
  pip install pyais onnxruntime  (선택, 없으면 heuristic fallback 사용)
"""

import sys
import os
import math
import time
import socket
import struct
import argparse
import json
import datetime
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# ──────────────────────────────────────────────
#  선택적 의존성
# ──────────────────────────────────────────────
try:
    import onnxruntime as ort
    HAVE_ORT = True
except ImportError:
    HAVE_ORT = False

# ──────────────────────────────────────────────
#  상수
# ──────────────────────────────────────────────
ML_FEATURE_COUNT = 15
ML_SEQ_LEN       = 10
MAX_HISTORY      = 60

GID = 9001

# Alert SID 정의
class SID:
    ML_ANOMALY            = 1
    POSITION_JUMP         = 2
    SPEED_VIOLATION       = 3
    STATUS_SPEED_MISMATCH = 4
    COG_HDG_MISMATCH      = 5
    SUDDEN_SPEED_CHANGE   = 6
    SIGNAL_LOSS           = 7
    INVALID_MMSI          = 8

SID_MSG = {
    SID.ML_ANOMALY:            "AIS ML Anomaly Detected",
    SID.POSITION_JUMP:         "AIS Position Jump Detected",
    SID.SPEED_VIOLATION:       "AIS Speed Violation",
    SID.STATUS_SPEED_MISMATCH: "AIS Status/Speed Mismatch",
    SID.COG_HDG_MISMATCH:      "AIS COG/HDG Mismatch",
    SID.SUDDEN_SPEED_CHANGE:   "AIS Sudden Speed Change",
    SID.SIGNAL_LOSS:           "AIS Signal Loss on Reappearance",
    SID.INVALID_MMSI:          "AIS Invalid MMSI",
}

ANSI_RED    = "\033[91m"
ANSI_YELLOW = "\033[93m"
ANSI_GREEN  = "\033[92m"
ANSI_CYAN   = "\033[96m"
ANSI_RESET  = "\033[0m"
ANSI_BOLD   = "\033[1m"

# ──────────────────────────────────────────────
#  데이터 구조
# ──────────────────────────────────────────────
@dataclass
class AISTarget:
    mmsi:       int   = 0
    sog:        float = 0.0    # knots
    cog:        float = 0.0    # degrees
    hdg:        int   = 511    # degrees (511 = N/A)
    lat:        float = 0.0
    lon:        float = 0.0
    navStatus:  int   = 15     # 15 = undefined
    shipType:   int   = 0
    rxTime:     float = 0.0

# ──────────────────────────────────────────────
#  AIVDM 비트 디코더  (순수 Python, 외부 의존 없음)
# ──────────────────────────────────────────────
def _payload_to_bits(payload: str) -> list:
    """6-bit ASCII 페이로드 → 비트 리스트"""
    bits = []
    for ch in payload:
        val = ord(ch) - 48
        if val > 40:
            val -= 8
        for b in range(5, -1, -1):
            bits.append((val >> b) & 1)
    return bits

def _get_uint(bits, start, length):
    val = 0
    for i in range(length):
        val = (val << 1) | bits[start + i]
    return val

def _get_int(bits, start, length):
    val = _get_uint(bits, start, length)
    if bits[start]:  # sign bit
        val -= (1 << length)
    return val

def decode_aivdm(sentence: str) -> Optional[AISTarget]:
    """
    NMEA AIVDM 문장을 파싱해 AISTarget 반환.
    지원: Message Type 1, 2, 3 (Class A Position Report)
          Message Type 18 (Class B Position Report)
    """
    sentence = sentence.strip()
    if not (sentence.startswith("!AIVDM") or sentence.startswith("!AIVDO")):
        return None

    # 체크섬 검증
    if '*' in sentence:
        body, cs = sentence.rsplit('*', 1)
        calc = 0
        for ch in body[1:]:
            calc ^= ord(ch)
        if calc != int(cs[:2], 16):
            return None  # 체크섬 불일치

    parts = sentence.split(',')
    if len(parts) < 7:
        return None

    # 멀티파트는 첫 파트만 처리 (완전 구현은 snort inspector에서)
    fill_bits = int(parts[6].split('*')[0]) if parts[6].split('*')[0].isdigit() else 0
    payload   = parts[5]

    bits = _payload_to_bits(payload)
    if len(bits) < 140:
        return None

    msg_type = _get_uint(bits, 0, 6)
    if msg_type not in (1, 2, 3, 18):
        return None

    t = AISTarget()
    t.rxTime = time.time()
    t.mmsi   = _get_uint(bits, 8, 30)

    if msg_type in (1, 2, 3):
        t.navStatus = _get_uint(bits, 38, 4)
        sog_raw     = _get_uint(bits, 50, 10)
        t.sog       = sog_raw / 10.0 if sog_raw < 1023 else 0.0
        lon_raw     = _get_int(bits,  61, 28)
        lat_raw     = _get_int(bits,  89, 27)
        t.lon       = lon_raw / 600000.0
        t.lat       = lat_raw / 600000.0
        cog_raw     = _get_uint(bits, 116, 12)
        t.cog       = cog_raw / 10.0 if cog_raw < 3600 else 0.0
        t.hdg       = _get_uint(bits, 128, 9)

    elif msg_type == 18:  # Class B
        t.navStatus = 15
        sog_raw     = _get_uint(bits, 46, 10)
        t.sog       = sog_raw / 10.0 if sog_raw < 1023 else 0.0
        lon_raw     = _get_int(bits,  57, 28)
        lat_raw     = _get_int(bits,  85, 27)
        t.lon       = lon_raw / 600000.0
        t.lat       = lat_raw / 600000.0
        cog_raw     = _get_uint(bits, 112, 12)
        t.cog       = cog_raw / 10.0 if cog_raw < 3600 else 0.0
        t.hdg       = _get_uint(bits, 124, 9)

    if t.mmsi <= 0 or (t.lat == 0.0 and t.lon == 0.0):
        return None

    return t


# ──────────────────────────────────────────────
#  피처 추출  (ais_ids.cpp의 to_snapshot() 이식)
# ──────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = (math.sin(dLat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dLon/2)**2)
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dLonr = math.radians(lon2 - lon1)
    b = math.atan2(
        math.sin(dLonr) * math.cos(lat2r),
        math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dLonr)
    )
    return (math.degrees(b) + 360.0) % 360.0

STATUS_MAX_SOG = [30.0, 1.0, 5.0, 10.0, 10.0, 1.0, 5.0, 15.0, 15.0]

def extract_features(history: deque) -> Optional[list]:
    """15개 특징 추출 (ais_ids.cpp 동일 로직)"""
    if len(history) < 2:
        return None

    prev = history[-2]
    cur  = history[-1]

    dt = cur.rxTime - prev.rxTime
    if dt <= 0:
        return None

    dist_km = haversine_km(prev.lat, prev.lon, cur.lat, cur.lon)
    expected_dist_km = cur.sog * dt / 3600.0 * 1.852

    # bearing_cog_diff
    bearing_cog_diff = -1.0
    if cur.sog >= 0.5:
        bear = bearing_deg(prev.lat, prev.lon, cur.lat, cur.lon)
        diff = abs(bear - cur.cog)
        if diff > 180.0: diff = 360.0 - diff
        bearing_cog_diff = diff

    # cog_hdg_diff
    cog_hdg_diff = -1.0
    if cur.hdg < 511:
        diff = abs(cur.cog - cur.hdg)
        if diff > 180.0: diff = 360.0 - diff
        cog_hdg_diff = diff

    # sog_change
    sog_change = abs(cur.sog - prev.sog)

    # cog_change
    cog_diff = abs(cur.cog - prev.cog)
    if cog_diff > 180.0: cog_diff = 360.0 - cog_diff
    cog_change = cog_diff

    # sog_status_ratio
    sidx = cur.navStatus
    max_sog = STATUS_MAX_SOG[sidx] if 0 <= sidx <= 8 else 30.0
    sog_status_ratio = cur.sog / max_sog if max_sog > 0 else 0.0

    # dist_expected_ratio
    dist_expected_ratio = dist_km / (expected_dist_km + 1e-6)

    # cog_hdg_change
    prev_cog_hdg_diff = -1.0
    if prev.hdg < 511:
        d = abs(prev.cog - prev.hdg)
        if d > 180.0: d = 360.0 - d
        prev_cog_hdg_diff = d
    cog_hdg_change = 0.0
    if cog_hdg_diff >= 0.0 and prev_cog_hdg_diff >= 0.0:
        cog_hdg_change = abs(cog_hdg_diff - prev_cog_hdg_diff)

    # cog_hdg_std (시퀀스 전체)
    chd_vals = []
    for h in history:
        if h.hdg < 511:
            d = abs(h.cog - h.hdg)
            if d > 180.0: d = 360.0 - d
            chd_vals.append(d)
        else:
            chd_vals.append(0.0)
    cog_hdg_std = 0.0
    if len(chd_vals) >= 2:
        mean = sum(chd_vals) / len(chd_vals)
        var  = sum((v - mean)**2 for v in chd_vals) / (len(chd_vals) - 1)
        cog_hdg_std = math.sqrt(var)

    return [
        cur.sog, cur.cog, float(cur.hdg), float(cur.navStatus),
        float(dt), dist_km,
        expected_dist_km, bearing_cog_diff,
        cog_hdg_diff, sog_change, cog_change,
        sog_status_ratio, dist_expected_ratio,
        cog_hdg_change, cog_hdg_std
    ]


# ──────────────────────────────────────────────
#  이상 탐지기
# ──────────────────────────────────────────────
class AnomalyDetector:
    """
    ONNX 모델이 있으면 ML 탐지, 없으면 heuristic fallback.
    scaler.json 포맷:  {"min": [...15...], "max": [...15...]}
    """

    def __init__(self, model_dir: str = ""):
        self.session   = None
        self.scaler_min = None
        self.scaler_max = None
        self.threshold  = 0.05
        self.loaded     = False

        if model_dir:
            self._load(model_dir)

    def _load(self, model_dir: str):
        if not HAVE_ORT:
            print(f"{ANSI_YELLOW}[WARN] onnxruntime 없음 → heuristic fallback{ANSI_RESET}")
            return

        model_path    = os.path.join(model_dir, "model.onnx")
        scaler_path   = os.path.join(model_dir, "scaler.json")
        threshold_path = os.path.join(model_dir, "threshold.txt")

        if not all(os.path.exists(p) for p in [model_path, scaler_path, threshold_path]):
            print(f"{ANSI_YELLOW}[WARN] 모델 파일 미발견 ({model_dir}) → heuristic fallback{ANSI_RESET}")
            return

        try:
            self.session = ort.InferenceSession(model_path)
            with open(scaler_path) as f:
                j = json.load(f)
            self.scaler_min = j["min"]
            self.scaler_max = j["max"]
            with open(threshold_path) as f:
                self.threshold = float(f.read().strip())
            self.loaded = True
            print(f"{ANSI_GREEN}[INFO] ONNX 모델 로드 완료 (threshold={self.threshold:.6f}){ANSI_RESET}")
        except Exception as e:
            print(f"{ANSI_YELLOW}[WARN] 모델 로드 실패: {e} → heuristic fallback{ANSI_RESET}")

    def _scale(self, i: int, val: float) -> float:
        rng = self.scaler_max[i] - self.scaler_min[i]
        if rng < 1e-9: return 0.0
        return (val - self.scaler_min[i]) / rng

    def detect(self, seq: list) -> tuple:
        """
        seq: ML_SEQ_LEN 길이의 [15-feature] 리스트
        반환: (is_anomaly: bool, error: float)
        """
        if self.loaded and len(seq) >= ML_SEQ_LEN:
            return self._detect_onnx(seq)
        else:
            return self._detect_heuristic(seq)

    def _detect_onnx(self, seq: list) -> tuple:
        import numpy as np
        data_seq = seq[-ML_SEQ_LEN:]
        flat = []
        for feat in data_seq:
            flat.extend([self._scale(i, feat[i]) for i in range(ML_FEATURE_COUNT)])

        inp = np.array(flat, dtype=np.float32).reshape(1, ML_SEQ_LEN, ML_FEATURE_COUNT)
        out = self.session.run(["output"], {"x": inp})[0]

        mse = float(((out.flatten() - inp.flatten())**2).mean())
        return mse > self.threshold, mse

    def _detect_heuristic(self, seq: list) -> tuple:
        """
        ONNX 없을 때 사용하는 통계적 이상 탐지.
        각 피처의 최근 값이 시퀀스 평균에서 3σ 이상 벗어나면 이상.
        """
        if len(seq) < 3:
            return False, 0.0

        score = 0.0
        n_features_checked = 0

        for fi in range(ML_FEATURE_COUNT):
            vals = [s[fi] for s in seq if s[fi] >= 0]
            if len(vals) < 2:
                continue
            mean = sum(vals) / len(vals)
            std  = math.sqrt(sum((v - mean)**2 for v in vals) / len(vals))
            if std < 1e-6:
                continue
            z = abs(vals[-1] - mean) / std
            score += z
            n_features_checked += 1

        if n_features_checked == 0:
            return False, 0.0

        avg_z = score / n_features_checked
        # avg_z > 2.5 를 이상으로 판정 (임계값)
        return avg_z > 2.5, avg_z


# ──────────────────────────────────────────────
#  경보 발생기 (Snort 스타일)
# ──────────────────────────────────────────────
class AlertEmitter:
    def __init__(self, log_path: str = "ais_ids_alerts.log"):
        self.log_path  = log_path
        self.alert_cnt = 0
        self._f = open(log_path, "a")

    def emit(self, sid: int, mmsi: int, detail: str, src_ip: str = "0.0.0.0"):
        self.alert_cnt += 1
        ts  = datetime.datetime.now().strftime("%m/%d-%H:%M:%S.%f")[:19]
        msg = SID_MSG.get(sid, "Unknown")

        # Snort 경보 형식 모방
        line = (
            f"[**] [{GID}:{sid}:1] {msg} [**]\n"
            f"[Classification: Policy Violation] [Priority: 2]\n"
            f"{ts} {src_ip}:0 -> BROADCAST:10110\n"
            f"MMSI: {mmsi} | {detail}\n"
            f"{'─'*60}"
        )
        print(f"{ANSI_RED}{ANSI_BOLD}{line}{ANSI_RESET}")
        self._f.write(line + "\n")
        self._f.flush()

    def close(self):
        self._f.close()


# ──────────────────────────────────────────────
#  메인 처리 엔진
# ──────────────────────────────────────────────
class AisIdsEngine:
    def __init__(self, model_dir: str = "", alerter: AlertEmitter = None):
        self.history:   Dict[int, deque]  = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
        self.seq_buf:   Dict[int, list]   = defaultdict(list)   # ML 피처 시퀀스
        self.detector = AnomalyDetector(model_dir)
        self.alerter  = alerter or AlertEmitter()
        self.pkt_count = 0
        self.alert_count = 0

    def process_sentence(self, sentence: str, src_ip: str = "0.0.0.0"):
        """AIVDM 문장 하나를 처리"""
        t = decode_aivdm(sentence)
        if t is None:
            return

        self.pkt_count += 1
        history = self.history[t.mmsi]
        history.append(t)

        # ── 규칙 기반 탐지 ───────────────────────────────
        self._rule_checks(t, history, src_ip)

        # ── ML 피처 추출 + 이상 탐지 ─────────────────────
        feat = extract_features(history)
        if feat is not None:
            self.seq_buf[t.mmsi].append(feat)
            if len(self.seq_buf[t.mmsi]) > ML_SEQ_LEN:
                self.seq_buf[t.mmsi].pop(0)

            is_anom, err = self.detector.detect(self.seq_buf[t.mmsi])
            if is_anom:
                self._alert(SID.ML_ANOMALY, t.mmsi, src_ip,
                            f"MSE/Score={err:.4f}")

    def _rule_checks(self, cur: AISTarget, history: deque, src_ip: str):
        # 최근 수신 시간 체크
        if len(history) >= 2:
            prev = list(history)[-2]
            dt = cur.rxTime - prev.rxTime

            # 위치 점프 (60초 내 5km 이상)
            if 0 < dt <= 60:
                dist = haversine_km(prev.lat, prev.lon, cur.lat, cur.lon)
                if dist > 5.0:
                    self._alert(SID.POSITION_JUMP, cur.mmsi, src_ip,
                                f"dist={dist:.2f}km in {dt:.0f}s")

            # 급격한 속도 변화 (60초 내 10kn 이상)
            if cur.sog < 102.2 and prev.sog < 102.2 and 0 < dt <= 60:
                delta = abs(cur.sog - prev.sog)
                if delta > 10.0:
                    self._alert(SID.SUDDEN_SPEED_CHANGE, cur.mmsi, src_ip,
                                f"SOG {prev.sog:.1f}→{cur.sog:.1f}kn (Δ{delta:.1f})")

            # 신호 소실 재등장 (300초 이상)
            if dt > 300:
                self._alert(SID.SIGNAL_LOSS, cur.mmsi, src_ip,
                            f"gap={dt:.0f}s")

        # 정박/계류 중 SOG
        if cur.sog >= 0.5 and cur.navStatus in (1, 5, 6):
            self._alert(SID.STATUS_SPEED_MISMATCH, cur.mmsi, src_ip,
                        f"navStatus={cur.navStatus} SOG={cur.sog:.1f}kn")

        # COG/HDG 불일치
        if cur.cog < 360.0 and cur.hdg < 360:
            diff = abs(cur.cog - cur.hdg)
            if diff > 180.0: diff = 360.0 - diff
            if diff > 100.0:
                self._alert(SID.COG_HDG_MISMATCH, cur.mmsi, src_ip,
                            f"COG={cur.cog:.1f} HDG={cur.hdg} diff={diff:.1f}")

    def _alert(self, sid: int, mmsi: int, src_ip: str, detail: str):
        self.alert_count += 1
        self.alerter.emit(sid, mmsi, detail, src_ip)

    def stats(self):
        print(f"\n{ANSI_CYAN}{'═'*60}")
        print(f"  총 처리 패킷:  {self.pkt_count}")
        print(f"  추적 선박 수:  {len(self.history)}")
        print(f"  발생 경보 수:  {self.alert_count}")
        print(f"{'═'*60}{ANSI_RESET}")


# ──────────────────────────────────────────────
#  샘플 파일 재생 모드 (sample.txt)
# ──────────────────────────────────────────────
def run_sample_file(engine: AisIdsEngine, file_path: str):
    """sample.txt 파일에서 AIVDM 문장 라인 단위로 읽어서 처리"""
    print(f"\n{ANSI_CYAN}{'═'*60}")
    print("  AIS-IDS + Snort 통합 데모  (sample file 모드)")
    print(f"{'═'*60}{ANSI_RESET}\n")

    if not os.path.exists(file_path):
        print(f"{ANSI_RED}[ERROR] 파일을 찾을 수 없습니다: {file_path}{ANSI_RESET}")
        return

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        print(f"{ANSI_GREEN}[INFO] 파일 로드 완료: {len(lines)}줄{ANSI_RESET}\n")
        
        pkt_idx = 0
        for line in lines:
            line = line.strip()
            # 빈 줄이나 주석 무시
            if not line or line.startswith('#'):
                continue
            
            # AIVDM 형식인지 확인
            if line.startswith('!AIVDM') or line.startswith('!AIVDO'):
                pkt_idx += 1
                print(f"{ANSI_GREEN}[PKT {pkt_idx:04d}] {line}{ANSI_RESET}")
                engine.process_sentence(line, "192.168.1.100")
                time.sleep(0.05)  # 시뮬레이션용 딜레이
        
        print(f"\n{ANSI_CYAN}[INFO] 샘플 파일 처리 완료: {pkt_idx}개 패킷{ANSI_RESET}")
    
    except IOError as e:
        print(f"{ANSI_RED}[ERROR] 파일 읽기 오류: {e}{ANSI_RESET}")
        return

    engine.stats()


# ──────────────────────────────────────────────
#  AIVDM 문장 생성기 (활성화)
# ──────────────────────────────────────────────
def make_aivdm_type1(mmsi, status, sog_x10, lon_x6, lat_x6, cog_x10, hdg):
    """간단한 Type 1 AIVDM 문장 생성기 (데모용)"""
    # 168-bit payload
    bits = [0] * 168

    def set_uint(start, length, val):
        for i in range(length-1, -1, -1):
            bits[start + (length-1-i)] = (val >> i) & 1

    def set_int(start, length, val):
        if val < 0:
            val += (1 << length)
        set_uint(start, length, val)

    set_uint(0,   6,  1)           # msg type
    set_uint(6,   2,  0)           # repeat
    set_uint(8,  30,  mmsi)        # mmsi
    set_uint(38,  4,  status)      # nav status
    set_uint(42,  8,  0)           # ROT (0)
    set_uint(50, 10,  sog_x10)     # SOG
    set_uint(60,  1,  1)           # position accuracy
    set_int (61, 28,  lon_x6)
    set_int (89, 27,  lat_x6)
    set_uint(116,12,  cog_x10)
    set_uint(128, 9,  hdg)
    set_uint(137, 6,  0)           # timestamp

    # 6-bit 패킹
    payload_chars = []
    for i in range(0, 168, 6):
        val = 0
        for j in range(6):
            val = (val << 1) | bits[i+j]
        val += 48
        if val > 87:
            val += 8
        payload_chars.append(chr(val))
    payload = ''.join(payload_chars)

    # 체크섬
    inner = f"AIVDM,1,1,,A,{payload},0"
    cs = 0
    for ch in inner:
        cs ^= ord(ch)
    return f"!{inner}*{cs:02X}"


# ──────────────────────────────────────────────
#  UDP 실시간 수신 모드
# ──────────────────────────────────────────────
def run_udp(engine: AisIdsEngine, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    print(f"{ANSI_CYAN}[INFO] UDP 수신 대기 중: 0.0.0.0:{port}{ANSI_RESET}")
    print(f"{ANSI_CYAN}[INFO] Ctrl+C로 종료{ANSI_RESET}\n")
    try:
        while True:
            data, addr = sock.recvfrom(4096)
            src_ip = addr[0]
            for line in data.decode("ascii", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("!AIVDM") or line.startswith("!AIVDO"):
                    print(f"{ANSI_GREEN}[PKT] {src_ip} → {line}{ANSI_RESET}")
                    engine.process_sentence(line, src_ip)
    except KeyboardInterrupt:
        print("\n")
        engine.stats()
    finally:
        sock.close()


# ──────────────────────────────────────────────
#  PCAP 재생 모드
# ──────────────────────────────────────────────
def run_pcap(engine: AisIdsEngine, path: str):
    try:
        from scapy.all import rdpcap, UDP, Raw
    except ImportError:
        print("[ERROR] scapy 필요: pip install scapy")
        sys.exit(1)

    pkts = rdpcap(path)
    print(f"{ANSI_CYAN}[INFO] PCAP 로드: {len(pkts)}개 패킷{ANSI_RESET}\n")
    for pkt in pkts:
        if UDP in pkt and Raw in pkt:
            src_ip = pkt["IP"].src if "IP" in pkt else "0.0.0.0"
            payload = pkt[Raw].load.decode("ascii", errors="ignore")
            for line in payload.splitlines():
                line = line.strip()
                if line.startswith("!AIVDM") or line.startswith("!AIVDO"):
                    print(f"{ANSI_GREEN}[PKT] {src_ip} → {line}{ANSI_RESET}")
                    engine.process_sentence(line, src_ip)
    engine.stats()


# ──────────────────────────────────────────────
#  진입점
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AIS-IDS Snort 통합 데모"
    )
    parser.add_argument("--model-dir", default="", help="ONNX 모델 디렉토리")
    parser.add_argument("--log",       default="ais_ids_alerts.log", help="경보 로그 파일")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sample", type=str, metavar="FILE", 
                       help="샘플 파일 처리 (기본: nmea_data_sample.txt)")
    group.add_argument("--udp",    type=int, metavar="PORT",
                       help="UDP 실시간 수신 포트 (예: 10110)")
    group.add_argument("--pcap",   metavar="FILE",
                       help="PCAP 파일 분석")
    args = parser.parse_args()

    print(f"\n{ANSI_BOLD}{ANSI_CYAN}")
    print("╔══════════════════════════════════════════════════════╗")
    print("║         AIS Intrusion Detection System               ║")
    print("║         Snort 3 Inspector Demo  (Python prototype)   ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"{ANSI_RESET}")

    alerter = AlertEmitter(args.log)
    engine  = AisIdsEngine(model_dir=args.model_dir, alerter=alerter)

    if args.udp:
        run_udp(engine, args.udp)
    elif args.pcap:
        run_pcap(engine, args.pcap)
    else:
        # 기본값: nmea_data_sample.txt 처리
        sample_file = args.sample or "nmea_data_sample.txt"
        run_sample_file(engine, sample_file)

    alerter.close()
    print(f"\n{ANSI_CYAN}[INFO] 경보 로그 저장: {args.log}{ANSI_RESET}")


if __name__ == "__main__":
    main()
