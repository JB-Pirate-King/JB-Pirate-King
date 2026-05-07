"""
AIVDM/NMEA 파서

ITU-R M.1371-5 규격 기반 Type 1/2/3 (Class A 위치 보고) 메시지 디코딩.
외부 의존성 없이 순수 Python 비트 연산으로 구현.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class AISTarget:
    mmsi:       int
    lat:        float   # 위도 (도)
    lon:        float   # 경도 (도)
    sog:        float   # Speed Over Ground (노트)
    cog:        float   # Course Over Ground (도)
    heading:    int     # True Heading (도, 511 = 미수신)
    nav_status: int     # Navigation Status (0-15)
    timestamp:  float   # 수신 시각 (time.time())


# ── 내부 헬퍼 ────────────────────────────────────────────────────────

def _sixbit_to_bits(payload: str) -> str:
    """AIVDM 6-bit ASCII 페이로드 → 이진 문자열"""
    result = []
    for ch in payload:
        n = ord(ch) - 48
        if n > 40:
            n -= 8
        result.append(format(n & 0x3F, "06b"))
    return "".join(result)


def _uint(bits: str, start: int, length: int) -> int:
    return int(bits[start:start + length], 2)


def _sint(bits: str, start: int, length: int) -> int:
    val = _uint(bits, start, length)
    if bits[start] == "1":          # 최상위 비트 = 부호
        val -= 1 << length
    return val


def _nmea_checksum(sentence: str) -> str:
    cs = 0
    for ch in sentence:
        cs ^= ord(ch)
    return f"{cs:02X}"


# ── 공개 인터페이스 ──────────────────────────────────────────────────

def parse_aivdm_sentence(line: str) -> Optional[AISTarget]:
    """
    단일 AIVDM/AIVDO 문장 파싱.
    Type 1/2/3 (Class A 위치 보고) 만 처리하며 다른 타입은 None 반환.

    예) !AIVDM,1,1,,A,15M67N0P00G?Uf6E`FepT@3n00Sa,0*73
    """
    line = line.strip()

    # 프리픽스 확인
    if not (line.startswith("!AIVDM") or line.startswith("!AIVDO")):
        return None

    # 체크섬 검증
    if "*" in line:
        body, cs_str = line.rsplit("*", 1)
        expected = _nmea_checksum(body[1:])   # '!' 이후부터
        if expected != cs_str[:2].upper():
            return None

    parts = line.split(",")
    if len(parts) < 6:
        return None

    # 멀티-문장 메시지는 첫 번째 조각만 수신한 경우 무시
    try:
        total_frags = int(parts[1])
        frag_num    = int(parts[2])
    except ValueError:
        return None
    if total_frags != 1 or frag_num != 1:
        # Type 1/2/3은 항상 단일 문장이므로 무시해도 무방
        return None

    payload = parts[5]
    if not payload:
        return None

    try:
        bits = _sixbit_to_bits(payload)
    except Exception:
        return None

    # Type 1/2/3 최소 168 비트
    if len(bits) < 168:
        return None

    msg_type = _uint(bits, 0, 6)
    if msg_type not in (1, 2, 3):
        return None

    mmsi       = _uint(bits, 8,  30)
    nav_status = _uint(bits, 38,  4)
    sog_raw    = _uint(bits, 50, 10)   # 1/10 노트
    lon_raw    = _sint(bits, 61, 28)   # 1/10000 분
    lat_raw    = _sint(bits, 89, 27)   # 1/10000 분
    cog_raw    = _uint(bits, 116, 12)  # 1/10 도
    heading    = _uint(bits, 128,  9)  # 도 (511 = 미수신)

    sog = sog_raw / 10.0
    lon = lon_raw / 600000.0
    lat = lat_raw / 600000.0
    cog = cog_raw / 10.0

    # 범위 검증
    if sog > 102.2:
        sog = 0.0
    if cog >= 360.0:
        cog = 0.0
    if not (-90.0 <= lat <= 90.0):
        return None
    if not (-180.0 <= lon <= 180.0):
        return None
    if lon == 0.0 and lat == 0.0:
        return None   # 위치 미수신 (기본값)

    return AISTarget(
        mmsi=mmsi,
        lat=lat,
        lon=lon,
        sog=sog,
        cog=cog,
        heading=heading,
        nav_status=nav_status,
        timestamp=time.time(),
    )
