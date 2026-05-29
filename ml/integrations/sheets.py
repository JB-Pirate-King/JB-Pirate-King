"""
Google Sheets 파이프라인 결과 기록

탭 구성:
  {model}   : 모델 전용 탭 — 브랜치별 블록 (학습/평가/FE 스텝 상세)
  실행요약  : 전체 run 1줄 요약
  상세로그  : 모든 단계 raw 로그
  시나리오결과 : run × 시나리오 탐지율 (FP≈1% 기준)
  피처중요도   : run × 피처 순열 중요도
"""
import json
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ── 탭별 헤더 ─────────────────────────────────────────────────────────

MODEL_HEADERS = [
    "branch", "timestamp", "stage", "status",
    "det_change", "n_features", "adopted", "threshold", "elapsed_s"
]

SUMMARY_HEADERS = [
    "timestamp", "branch", "model", "epochs", "max_mmsi", "data_file",
    "fe_steps", "fe_baseline", "fe_det", "fe_gain_pp",
    "fe_n_feat", "fe_features", "fe_threshold",
    "notes"
]

DETAIL_HEADERS = [
    "timestamp", "branch", "stage", "status",
    "det_rate", "n_features", "threshold", "elapsed_sec", "notes"
]

SCENARIO_HEADERS = [
    "timestamp", "branch", "model", "fp_target",
    "scenario", "det_rate"
]

IMPORTANCE_HEADERS = [
    "timestamp", "branch", "fe_step",
    "feature", "importance_pp", "description"
]

FIXED_TABS = {
    "실행요약":    SUMMARY_HEADERS,
    "상세로그":    DETAIL_HEADERS,
    "시나리오결과": SCENARIO_HEADERS,
    "피처중요도":  IMPORTANCE_HEADERS,
}


class PipelineSheets:
    def __init__(self, credentials_file: str, sheet_id: str):
        creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
        gc = gspread.authorize(creds)
        self.sh = gc.open_by_key(sheet_id)
        self._tabs: dict[str, gspread.Worksheet] = {}
        self._summary_row: int | None = None
        self._ensure_fixed_tabs()

    # ── 내부 유틸 ────────────────────────────────────────────────────

    def _append_and_track(self, ws: gspread.Worksheet, row: list) -> int:
        data_rows = len(ws.get_all_values())
        if data_rows >= ws.row_count - 10:
            ws.add_rows(500)
        ws.append_row(row)
        return data_rows + 1

    def _ensure_fixed_tabs(self):
        for title, headers in FIXED_TABS.items():
            try:
                ws = self.sh.worksheet(title)
            except gspread.WorksheetNotFound:
                ws = self.sh.add_worksheet(title=title, rows=3000,
                                           cols=len(headers) + 1)
            if ws.row_values(1) != headers:
                ws.insert_row(headers, index=1)
            self._tabs[title] = ws

    def _ensure_model_tab(self, model: str) -> gspread.Worksheet:
        if model in self._tabs:
            return self._tabs[model]
        try:
            ws = self.sh.worksheet(model)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=model, rows=3000,
                                       cols=len(MODEL_HEADERS) + 1)
        if ws.row_values(1) != MODEL_HEADERS:
            ws.insert_row(MODEL_HEADERS, index=1)
        self._tabs[model] = ws
        return ws

    def _ws(self, title: str) -> gspread.Worksheet:
        return self._tabs[title]

    # ── 실행 시작 ────────────────────────────────────────────────────

    def log_run_start(self, branch: str, model: str, epochs: int, max_mmsi: int,
                      data_file: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        ws_model = self._ensure_model_tab(model)
        self._append_and_track(ws_model, [""] * len(MODEL_HEADERS))
        self._append_and_track(ws_model, [
            f"▶ {branch}", ts, "─ RUN START ─", "진행 중",
            f"epochs={epochs}", f"max_mmsi={max_mmsi}", data_file, "", ""
        ])

        ws_d = self._ws("상세로그")
        self._append_and_track(ws_d, [""] * len(DETAIL_HEADERS))
        self._append_and_track(ws_d, [ts, branch, "─ START ─", model,
                                      "", "", "", "", f"epochs={epochs}"])

        ws_s = self._ws("실행요약")
        self._summary_row = self._append_and_track(ws_s, [
            ts, branch, model, epochs, max_mmsi, data_file,
            "-", "-", "-", "-", "-", "-", "-",
            "진행 중"
        ])

    # ── 학습 ────────────────────────────────────────────────────────

    def log_train(self, branch: str, model: str, epochs: int, max_mmsi: int,
                  status: str, elapsed_sec: float = None, notes: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elapsed_str = f"{elapsed_sec:.0f}s" if elapsed_sec else ""

        ws_model = self._ensure_model_tab(model)
        self._append_and_track(ws_model, [
            "", ts, "베이스학습", status,
            elapsed_str, "", "", "", notes
        ])

        self._append_and_track(self._ws("상세로그"), [
            ts, branch, "베이스학습", status,
            "", "", "", elapsed_str.rstrip("s"), notes
        ])

        if self._summary_row:
            self._ws("실행요약").update_cell(self._summary_row,
                                             SUMMARY_HEADERS.index("train_status") + 1, status)

    # ── 평가 ────────────────────────────────────────────────────────

    def log_eval(self, branch: str, model: str, status: str,
                 det_fp1: float = None, det_fp5: float = None,
                 det_fp10: float = None, holdout_fp10: float = None,
                 fp1_holdout: float = None,
                 elapsed_sec: float = None, notes: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f = lambda v, d=1: f"{v:.{d}f}" if v is not None else ""

        m1 = f"FP1%: {f(det_fp1)}%|{f(fp1_holdout)}%" if det_fp1 else ""
        m2 = f"FP5%: {f(det_fp5)}%" if det_fp5 else ""
        m3 = f"FP10%: {f(det_fp10)}%|{f(holdout_fp10)}%" if det_fp10 else ""
        elapsed_str = f"{elapsed_sec:.0f}s" if elapsed_sec else ""

        ws_model = self._ensure_model_tab(model)
        self._append_and_track(ws_model, [
            "", ts, "평가", status, m1, m2, m3, elapsed_str, notes
        ])

        self._append_and_track(self._ws("상세로그"), [
            ts, branch, "평가", status,
            f(det_fp1), "", "", elapsed_str.rstrip("s"), notes
        ])

        if self._summary_row:
            ws_s = self._ws("실행요약")
            for col_name, val in [
                ("fp1_train",    f(det_fp1)),
                ("fp1_holdout",  f(fp1_holdout)),
                ("fp5_train",    f(det_fp5)),
                ("fp10_train",   f(det_fp10)),
                ("fp10_holdout", f(holdout_fp10)),
            ]:
                if val:
                    ws_s.update_cell(self._summary_row,
                                     SUMMARY_HEADERS.index(col_name) + 1, val)

    def log_scenarios(self, branch: str, model: str, fp_target: str,
                      scenarios: dict[str, float]):
        """시나리오별 탐지율 기록 (시나리오결과 탭).
        scenarios: {시나리오명: 탐지율%}
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws = self._ws("시나리오결과")
        for name, rate in sorted(scenarios.items()):
            self._append_and_track(ws, [
                ts, branch, model, fp_target, name, f"{rate:.1f}"
            ])

    # ── 피처 엔지니어링 ──────────────────────────────────────────────

    def log_fe(self, branch: str, run_num: int, status: str,
               model: str = "", fe_step: int = None,
               baseline_det: float = None, best_det: float = None,
               n_features: int = None, adopted: list = None,
               all_features: list = None,
               threshold: float = None, elapsed_sec: float = None,
               notes: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        gain = (best_det - baseline_det) if (best_det is not None and baseline_det is not None) else None
        adopted_str = ", ".join(adopted) if adopted else "채택없음"
        features_str = ", ".join(all_features) if all_features else adopted_str
        elapsed_str = f"{elapsed_sec:.0f}s" if elapsed_sec else ""
        step_label = f"FE Step {fe_step}" if fe_step else "피처 엔지니어링 학습"

        det_str = (f"{baseline_det:.1f}%→{best_det:.1f}%({gain:+.1f}pp)"
                   if gain is not None else f"{best_det:.1f}%" if best_det else "")

        if model:
            ws_model = self._ensure_model_tab(model)
            self._append_and_track(ws_model, [
                "", ts, step_label, status,
                det_str,
                f"{n_features}피처" if n_features else "",
                adopted_str,
                f"thr={threshold:.6f}" if threshold else "",
                elapsed_str
            ])

        self._append_and_track(self._ws("상세로그"), [
            ts, branch, step_label, status,
            f"{best_det:.1f}" if best_det else "",
            str(n_features) if n_features else "",
            f"{threshold:.8f}" if threshold else "",
            elapsed_str.rstrip("s"), adopted_str
        ])

    def log_importance(self, branch: str, fe_step: int,
                       importance: list[tuple[str, float]],
                       descriptions: dict[str, str]):
        """순열 중요도 기록 (피처중요도 탭).
        importance: [(feature, pp), ...] 정렬된 순서
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws = self._ws("피처중요도")
        for feat, pp in importance:
            desc = descriptions.get(feat, "")
            self._append_and_track(ws, [
                ts, branch, f"Step {fe_step}", feat, f"{pp:.4f}", desc
            ])

    def log_run_done(self, branch: str, model: str, success: bool = True, notes: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        icon = "✅" if success else "❌"
        ws_model = self._ensure_model_tab(model)
        self._append_and_track(ws_model, [
            "", ts, f"{icon} RUN DONE", "완료" if success else "중단",
            "", "", "", "", notes
        ])

    def update_run_summary(self, train_ok: bool = None,
                           det_fp1: float = None,
                           fe_steps: int = None,
                           fe_baseline: float = None,
                           fe_det: float = None,
                           fe_n_feat: int = None,
                           adopted: list = None,
                           threshold: float = None,
                           notes: str = "완료"):
        if not self._summary_row:
            return
        ws = self._ws("실행요약")
        row = self._summary_row
        if row > ws.row_count:
            ws.add_rows(row - ws.row_count + 50)

        fe_gain = (fe_det - fe_baseline) if (fe_det and fe_baseline) else None
        updates = {}
        if fe_steps is not None:
            updates["fe_steps"] = str(fe_steps)
        if fe_baseline is not None:
            updates["fe_baseline"] = f"{fe_baseline:.1f}"
        if fe_det is not None:
            updates["fe_det"] = f"{fe_det:.1f}"
        if fe_gain is not None:
            updates["fe_gain_pp"] = f"{fe_gain:+.1f}"
        if fe_n_feat is not None:
            updates["fe_n_feat"] = str(fe_n_feat)
        if adopted is not None:
            updates["fe_features"] = ", ".join(adopted)
        if threshold is not None:
            updates["fe_threshold"] = f"{threshold:.8f}"
        updates["notes"] = notes

        for col_name, val in updates.items():
            if col_name in SUMMARY_HEADERS:
                ws.update_cell(row, SUMMARY_HEADERS.index(col_name) + 1, val)

    # ── 하위 호환 ─────────────────────────────────────────────────────

    def log(self, branch: str, stage: str, status: str,
            det_rate: float = None, n_features: int = None,
            threshold: float = None, elapsed_sec: float = None,
            notes: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append_and_track(self._ws("상세로그"), [
            ts, branch, stage, status,
            f"{det_rate:.2f}" if det_rate is not None else "",
            str(n_features) if n_features is not None else "",
            f"{threshold:.8f}" if threshold is not None else "",
            f"{elapsed_sec:.0f}" if elapsed_sec is not None else "",
            notes
        ])


def from_config(config_path: str = "ml/pipeline_config.json") -> "PipelineSheets":
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    return PipelineSheets(
        credentials_file=cfg["google_sheets"]["credentials_file"],
        sheet_id=cfg["google_sheets"]["sheet_id"]
    )
