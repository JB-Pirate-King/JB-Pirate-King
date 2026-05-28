"""
automation/sheets_tracker.py

Google Sheets에 학습 결과를 기록한다.
시트 구조:
  pipeline_results  — 모델별 실험 결과
  data_coverage     — 전처리된 날짜 범위 추적

사용 전 준비:
  1. Google Cloud Console → 서비스 계정 생성 → JSON 키 다운로드
  2. 해당 서비스 계정 이메일을 시트에 편집자로 공유
  3. GSHEETS_CREDS_FILE=automation/credentials.json 설정
"""

from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from automation.config import (
    GSHEETS_CREDS_FILE,
    GSHEETS_SPREADSHEET_ID,
    GSHEETS_SHEET_NAME,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_RESULT_HEADERS = [
    "timestamp", "model", "epochs", "seq_len", "n_features",
    "train_dr_%", "holdout_dr_%", "fp_rate_%",
    "mlflow_run_id", "github_release",
]

_COVERAGE_HEADERS = [
    "timestamp", "raw_files", "date_start", "date_end",
    "preprocessed_rows", "notes",
]


def _get_client():
    creds = Credentials.from_service_account_file(GSHEETS_CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _ensure_headers(ws, headers: list[str]):
    existing = ws.row_values(1)
    if existing != headers:
        ws.insert_row(headers, 1)


class SheetsTracker:
    def __init__(self):
        if not GSHEETS_SPREADSHEET_ID:
            print("[sheets] GSHEETS_SPREADSHEET_ID 미설정 — Google Sheets 스킵")
            self._disabled = True
            return
        self._disabled = False

        client = _get_client()
        self._ss = client.open_by_key(GSHEETS_SPREADSHEET_ID)

        self._results_ws  = self._get_or_create_sheet(GSHEETS_SHEET_NAME, _RESULT_HEADERS)
        self._coverage_ws = self._get_or_create_sheet("data_coverage", _COVERAGE_HEADERS)

    def _get_or_create_sheet(self, name: str, headers: list[str]):
        try:
            ws = self._ss.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = self._ss.add_worksheet(title=name, rows=1000, cols=len(headers))
        _ensure_headers(ws, headers)
        return ws

    def log_result(
        self,
        model: str,
        epochs: int,
        train_dr: float,
        holdout_dr: float,
        fp_rate: float,
        mlflow_run_id: str = "",
        github_release: str = "",
    ):
        if self._disabled:
            return
        self._results_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            model, epochs, 10, 12,
            round(train_dr, 2),
            round(holdout_dr, 2),
            round(fp_rate, 2),
            mlflow_run_id,
            github_release,
        ])
        print(f"[sheets] {model} 결과 기록 완료")

    def log_data_coverage(
        self,
        raw_files: int,
        date_start: str,
        date_end: str,
        preprocessed_rows: int,
        notes: str = "",
    ):
        if self._disabled:
            return
        self._coverage_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            raw_files, date_start, date_end,
            preprocessed_rows, notes,
        ])
        print(f"[sheets] 데이터 커버리지 기록 완료 ({date_start} ~ {date_end})")

    def get_best_model(self) -> dict | None:
        if self._disabled:
            return None
        rows = self._results_ws.get_all_records()
        if not rows:
            return None
        return max(rows, key=lambda r: float(r.get("holdout_dr_%", 0)))
