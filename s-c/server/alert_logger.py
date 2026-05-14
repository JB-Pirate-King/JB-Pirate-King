"""
경보 로거

ais_ids_alerts.log 형식 (Snort/Suricata 스타일) + NDJSON 병행 기록.
ais_ids_alerts.log 예시:
  [**] [9001:1:1] sog_change, dist_km, speed_consistency [**]
  [Classification: Policy Violation] [Priority: 2]
  05/07-12:34:56.789  MMSI: 440123456  score: 110.1387
  lat=35.10000 | lon=129.00000 | sog=10.0 | cog=180.0
  ────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List


_SEP = "─" * 60


class AlertLogger:
    def __init__(self, log_path: str, json_path: str, max_recent: int = 200):
        self._log_path   = log_path
        self._json_path  = json_path
        self._max_recent = max_recent
        self._lock       = threading.Lock()
        self._recent: deque = deque(maxlen=max_recent)

        # 로그 디렉토리 생성
        for p in (log_path, json_path):
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)

    # ── 공개 API ────────────────────────────────────────────────────

    def log(
        self,
        mmsi:        int,
        score:       float,
        description: str,
        priority:    int = 2,
        extra:       Dict | None = None,
        # src_ip 파라미터는 이전 버전 호환을 위해 유지하되 사용하지 않음
        src_ip:      str = "",
    ) -> None:
        """경보 1건 기록 (파일 + 메모리)."""
        now = datetime.now(tz=timezone.utc)
        ts_disp = now.strftime("%m/%d-%H:%M:%S.%f")[:-3]   # MM/DD-HH:MM:SS.mmm
        ts_iso  = now.isoformat()

        # ── Snort/Suricata 텍스트 포맷 ──────────────────────────────
        text_lines = [
            f"[**] [9001:1:1] {description} [**]",
            f"[Classification: Policy Violation] [Priority: {priority}]",
            f"{ts_disp}  MMSI: {mmsi}  score: {score:.6g}",
        ]
        if extra:
            detail = " | ".join(f"{k}={v}" for k, v in extra.items())
            text_lines.append(detail)
        text_lines.append(_SEP)
        text_block = "\n".join(text_lines) + "\n"

        # ── JSON 레코드 ─────────────────────────────────────────────
        record: Dict = {
            "timestamp":   ts_iso,
            "mmsi":        mmsi,
            "score":       round(score, 6),
            "description": description,
            "priority":    priority,
        }
        if extra:
            record["detail"] = extra

        with self._lock:
            self._write_text(text_block)
            self._write_json(record)
            self._recent.append(record)

    def get_recent(self, n: int = 50) -> List[Dict]:
        """최근 n 건 반환 (최신순)."""
        with self._lock:
            items = list(self._recent)
        return list(reversed(items[-n:]))

    def count(self) -> int:
        with self._lock:
            return len(self._recent)

    # ── 내부 ────────────────────────────────────────────────────────

    def _write_text(self, block: str) -> None:
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(block)
        except OSError:
            pass

    def _write_json(self, record: Dict) -> None:
        try:
            with open(self._json_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
