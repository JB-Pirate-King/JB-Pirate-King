"""
AIS IDS 서버 — 진입점

흐름:
  OpenCPN (Output 설정) ──TCP 10110──▶ NMEA 수신 스레드
                                          │
                                          ▼
                                    AIS 파서 (ais_parser.py)
                                          │
                                          ▼
                                    피처 추출 (feature.py)
                                          │
                                          ▼
                                    ML 탐지 (ml_detector.py)
                                          │ 이상 탐지 시
                                          ▼
                                    경보 기록 (alert_logger.py)
                                    /logs/ais_ids_alerts.log
                                    /logs/alerts.json

  REST API (HTTP 5000):
    GET  /status    서버 상태, ML 로드 여부, 추적 중인 선박 수
    GET  /alerts    최근 경보 목록 (JSON)
    GET  /vessels   현재 추적 중인 선박 목록 (JSON)

환경변수:
  NMEA_HOST  (기본 0.0.0.0)
  NMEA_PORT  (기본 10110)
  API_HOST   (기본 0.0.0.0)
  API_PORT   (기본 5000)
  MODEL_DIR  (기본 /models)
  LOG_DIR    (기본 /logs)
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections import defaultdict
from typing import DefaultDict, Dict, List

from flask import Flask, jsonify, request

from ais_parser import AISTarget, parse_aivdm_sentence
from feature import SEQ_LEN, build_inference_window
from ml_detector import MLDetector
from alert_logger import AlertLogger

# ── 설정 ─────────────────────────────────────────────────────────────
NMEA_HOST  = os.getenv("NMEA_HOST",  "0.0.0.0")
NMEA_PORT  = int(os.getenv("NMEA_PORT",  "10110"))
API_HOST   = os.getenv("API_HOST",   "0.0.0.0")
API_PORT   = int(os.getenv("API_PORT",   "5000"))
MODEL_DIR  = os.getenv("MODEL_DIR",  "/models")
LOG_DIR    = os.getenv("LOG_DIR",    "/logs")
MAX_HIST   = 100   # MMSI 별 보관 최대 레코드 수

# ── 로거 설정 ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ais_ids")

# ── 공유 상태 ─────────────────────────────────────────────────────────
# mmsi → [rec_dict, ...]  (원시 레코드 히스토리)
_history: DefaultDict[int, List[Dict]] = defaultdict(list)
_lock    = threading.Lock()

# 통계
_stats: Dict[str, int] = {
    "rx_sentences":  0,
    "rx_targets":    0,
    "ml_detections": 0,
}

# ── ML + 경보 로거 ────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)

detector = MLDetector(MODEL_DIR)
alerter  = AlertLogger(
    log_path  = os.path.join(LOG_DIR, "ais_ids_alerts.log"),
    json_path = os.path.join(LOG_DIR, "alerts.json"),
)


# ══════════════════════════════════════════════════════════════════════
# AIS 처리 파이프라인
# ══════════════════════════════════════════════════════════════════════

def _process_target(target: AISTarget, src_ip: str) -> None:
    """수신된 AISTarget 을 히스토리에 추가하고 ML 탐지 수행."""
    rec = {
        "mmsi":       target.mmsi,
        "lat":        target.lat,
        "lon":        target.lon,
        "sog":        target.sog,
        "cog":        target.cog,
        "heading":    target.heading,
        "nav_status": target.nav_status,
        "timestamp":  target.timestamp,
    }

    with _lock:
        hist = _history[target.mmsi]
        hist.append(rec)
        if len(hist) > MAX_HIST:
            hist.pop(0)

        seq = build_inference_window(hist)

    _stats["rx_targets"] += 1

    if seq is None:
        return   # 아직 시퀀스 충분하지 않음

    if not detector.loaded:
        return

    try:
        is_anom, score, feat_desc = detector.detect(seq)
    except Exception as exc:
        log.warning("ML 추론 오류 MMSI=%d: %s", target.mmsi, exc)
        return

    if is_anom:
        _stats["ml_detections"] += 1
        log.warning(
            "이상 탐지  MMSI=%-12d  score=%.4f  feat=%s",
            target.mmsi, score, feat_desc,
        )
        alerter.log(
            mmsi=target.mmsi,
            score=score,
            description=feat_desc,
            extra={
                "lat": round(target.lat, 5),
                "lon": round(target.lon, 5),
                "sog": target.sog,
                "cog": round(target.cog, 1),
            },
        )


# ══════════════════════════════════════════════════════════════════════
# TCP NMEA 서버
# ══════════════════════════════════════════════════════════════════════

def _handle_client(conn: socket.socket, addr: tuple) -> None:
    """OpenCPN 클라이언트 연결 처리 (1 스레드 / 연결)."""
    src_ip = addr[0]
    log.info("연결 수락: %s:%d", *addr)
    buf = ""
    try:
        conn.settimeout(120)
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk.decode("ascii", errors="ignore")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                _stats["rx_sentences"] += 1
                target = parse_aivdm_sentence(line)
                if target:
                    try:
                        _process_target(target, src_ip)
                    except Exception as exc:
                        log.warning("AIS 처리 오류 src=%s line=%r: %s", src_ip, line, exc)
    except (ConnectionResetError, TimeoutError, OSError):
        pass
    finally:
        conn.close()
        log.info("연결 종료: %s:%d", *addr)


def _run_nmea_server() -> None:
    """TCP NMEA 수신 서버 (무한 루프)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((NMEA_HOST, NMEA_PORT))
        srv.listen(16)
        log.info("NMEA 서버 대기 중: %s:%d", NMEA_HOST, NMEA_PORT)
        while True:
            try:
                conn, addr = srv.accept()
                t = threading.Thread(
                    target=_handle_client,
                    args=(conn, addr),
                    daemon=True,
                    name=f"client-{addr[0]}:{addr[1]}",
                )
                t.start()
            except OSError as exc:
                log.error("NMEA accept 오류: %s", exc)
                time.sleep(1)


# ══════════════════════════════════════════════════════════════════════
# Flask REST API
# ══════════════════════════════════════════════════════════════════════

app = Flask(__name__)
app.json.ensure_ascii = False


@app.get("/status")
def api_status():
    with _lock:
        vessel_count = len(_history)
    return jsonify({
        "running":         True,
        "ml_loaded":       detector.loaded,
        "ml_status":       detector.status_msg,
        "tracked_vessels": vessel_count,
        "stats":           dict(_stats),
        "alert_count":     alerter.count(),
    })


@app.get("/alerts")
def api_alerts():
    n = min(int(request.args.get("n", 50)), 200)
    return jsonify(alerter.get_recent(n))


@app.get("/vessels")
def api_vessels():
    with _lock:
        snapshot = {
            mmsi: list(hist)[-1]
            for mmsi, hist in _history.items()
            if hist
        }
    result = {}
    for mmsi, last in snapshot.items():
        result[str(mmsi)] = {
            "mmsi":       mmsi,
            "lat":        last["lat"],
            "lon":        last["lon"],
            "sog":        last["sog"],
            "cog":        last["cog"],
            "nav_status": last["nav_status"],
            "history_len": len(_history.get(mmsi, [])),
        }
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    log.info("AIS IDS 서버 시작")
    log.info("MODEL_DIR=%s  LOG_DIR=%s", MODEL_DIR, LOG_DIR)

    # ML 모델 로드
    if detector.load():
        log.info("ML 로드 성공: %s", detector.status_msg)
    else:
        log.warning("ML 로드 실패: %s - 탐지 비활성", detector.status_msg)

    # NMEA TCP 서버 (백그라운드 스레드)
    nmea_thread = threading.Thread(
        target=_run_nmea_server, daemon=True, name="nmea-server"
    )
    nmea_thread.start()

    # Flask REST API (메인 스레드 블로킹)
    log.info("REST API 시작: %s:%d", API_HOST, API_PORT)
    app.run(host=API_HOST, port=API_PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
