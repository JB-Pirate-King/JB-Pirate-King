"""
automation/sheets_tracker.py

Google Sheets에 학습 결과를 기록한다.
기존 스프레드시트 시트 구조에 맞춰 정확한 시트명/컬럼을 사용한다.

  🤖 ML 모델 성능  — 모델별 탐지율 결과
  🔄 실행 로그     — 파이프라인 실행 이력

사용 전 준비:
  1. Google Cloud Console → 서비스 계정 생성 → JSON 키 다운로드
  2. 해당 서비스 계정 이메일을 시트에 편집자로 공유
  3. GSHEETS_CREDS_FILE=automation/credentials.json 설정
"""

import uuid
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from automation.config import (
    GSHEETS_CREDS_FILE,
    GSHEETS_SPREADSHEET_ID,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# 기존 시트명 (실제 스프레드시트와 일치)
SHEET_ML_PERF  = "🤖 ML 모델 성능"
SHEET_RUN_LOG  = "🔄 실행 로그"

# 🤖 ML 모델 성능 컬럼 (기존 헤더 행 보존, append only)
# 모델 | 유형 | 오탐율 | 학습 시나리오 탐지율 | 홀드아웃 F 탐지율 | 홀드아웃 G 탐지율 | 특이사항 | 배포여부

# 🔄 실행 로그 컬럼 (기존 헤더와 동일)
# 실행ID | 시작시간 | 종료시간 | 모델 | 단계 | 상태 | 소요시간(초) | 비고


def _get_client():
    creds = Credentials.from_service_account_file(GSHEETS_CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


class SheetsTracker:
    def __init__(self):
        if not GSHEETS_SPREADSHEET_ID:
            print("[sheets] GSHEETS_SPREADSHEET_ID 미설정 — Google Sheets 스킵")
            self._disabled = True
            return

        try:
            client = _get_client()
            self._ss = client.open_by_key(GSHEETS_SPREADSHEET_ID)
            self._perf_ws = self._ss.worksheet(SHEET_ML_PERF)
            self._log_ws  = self._ss.worksheet(SHEET_RUN_LOG)
            self._disabled = False
        except Exception as e:
            print(f"[sheets] 연결 실패: {e}")
            self._disabled = True

    # ── ML 모델 성능 시트 ─────────────────────────────────────────────
    def log_model_result(
        self,
        model: str,
        model_type: str,            # "비지도학습" | "지도학습"
        fp_rate: float,             # 오탐율 (%)
        train_dr: float,            # 학습 시나리오 탐지율 (%)
        holdout_f_dr: float | None = None,  # 홀드아웃 F 탐지율
        holdout_g_dr: float | None = None,  # 홀드아웃 G 탐지율
        notes: str = "",
        deployed: bool = False,
    ):
        if self._disabled:
            return
        row = [
            model,
            model_type,
            f"{fp_rate:.2f}%",
            f"{train_dr:.2f}%",
            f"{holdout_f_dr:.2f}%" if holdout_f_dr is not None else "-",
            f"{holdout_g_dr:.2f}%" if holdout_g_dr is not None else "-",
            notes,
            "✅ 배포" if deployed else "",
        ]
        self._perf_ws.append_row(row)
        print(f"[sheets] 🤖 ML 모델 성능 기록: {model}")

    # ── 실행 로그 시트 ────────────────────────────────────────────────
    def log_run_start(
        self, model: str, step: str, run_id: str | None = None
    ) -> tuple[str, str]:
        """실행 시작 기록. (run_id, start_time) 반환"""
        if self._disabled:
            return "", ""
        run_id = run_id or f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_ws.append_row([
            run_id, start, "", model, step, "실행중", "", "",
        ])
        print(f"[sheets] 🔄 실행 시작: {run_id} / {model} / {step}")
        return run_id, start

    def log_run_end(
        self,
        run_id: str,
        start_time: str,
        model: str,
        step: str,
        status: str,          # "완료" | "실패"
        elapsed_sec: float,
        notes: str = "",
    ):
        """실행 완료 기록 (새 행 append)"""
        if self._disabled:
            return
        end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_ws.append_row([
            run_id, start_time, end, model, step, status,
            round(elapsed_sec, 1), notes,
        ])
        print(f"[sheets] 🔄 실행 완료: {run_id} → {status} ({elapsed_sec:.0f}s)")

    # ── 편의 래퍼 (pipeline_runner.py 호환) ─────────────────────────
    def log_result(
        self,
        model: str,
        epochs: int,
        train_dr: float,
        holdout_dr: float,
        fp_rate: float,
        mlflow_run_id: str = "",
        github_release: str = "",
        holdout_g_dr: float | None = None,
    ):
        """pipeline_runner.py에서 호출하는 통합 결과 기록"""
        notes_parts = []
        if mlflow_run_id:
            notes_parts.append(f"mlflow:{mlflow_run_id[:8]}")
        if github_release:
            notes_parts.append(github_release)
        if epochs:
            notes_parts.append(f"ep{epochs}")

        self.log_model_result(
            model=model,
            model_type="비지도학습",
            fp_rate=fp_rate,
            train_dr=train_dr,
            holdout_f_dr=holdout_dr,
            holdout_g_dr=holdout_g_dr,
            notes=", ".join(notes_parts),
        )

    def get_best_model(self) -> dict | None:
        """홀드아웃 F 탐지율 기준 최고 모델 반환"""
        if self._disabled:
            return None
        try:
            rows = self._perf_ws.get_all_records()
            if not rows:
                return None
            def _dr(r):
                v = str(r.get("홀드아웃 F 탐지율", "0")).replace("%", "").strip()
                try:
                    return float(v)
                except ValueError:
                    return 0.0
            return max(rows, key=_dr)
        except Exception:
            return None
