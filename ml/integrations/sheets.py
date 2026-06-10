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
    "fe_steps", "fe_baseline",
    "fe_det_fp1", "fe_det_fp5", "fe_det_fp10",
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


# 마스터 시트의 모델 인덱스(허브) 탭 — 모델명 클릭 시 해당 탭으로 점프
HUB_TAB = "모델목록"
HUB_HEADERS = ["model", "요약", "상세", "시나리오", "중요도", "created"]


class PipelineSheets:
    """모델별로 **탭을 분리**해 단일 마스터 스프레드시트에 기록 (A안).

    - 서비스계정은 개인 Gmail Drive 에 새 스프레드시트를 만들 수 없음(quota 0)이라
      파일 분리(B안) 대신 **한 시트 안에서 모델별 탭 접두**로 분리한다.
    - 모델 `m` 의 탭: `m_실행요약 / m_상세로그 / m_시나리오결과 / m_피처중요도 / m`(상세).
    - 허브 탭(`모델목록`)에 모델별 탭으로 점프하는 HYPERLINK 를 등록 → "클릭→이동" UX.
    - 파이프라인은 한 번에 한 모델(run)만 처리하므로 '현재 모델' 상태로 라우팅.
    - 마스터 시트 1개만 서비스계정에 공유돼 있으면 됨 (모델별 시트 공유 불필요).
    """

    def __init__(self, credentials_file: str, sheet_id: str):
        creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.master = self.gc.open_by_key(sheet_id)
        self._tabs: dict[tuple, gspread.Worksheet] = {}     # (model, base_title) -> 탭
        self._summary_row: int | None = None
        self._cur_model: str | None = None
        self._inited: set = set()                           # 탭/허브 준비된 모델
        self._hub = self._ensure_hub()
        for row in self._hub.get_all_values()[1:]:
            if row and row[0]:
                self._inited.add(row[0])

    # ── 허브 ─────────────────────────────────────────────────────────

    def _ensure_hub(self) -> gspread.Worksheet:
        try:
            ws = self.master.worksheet(HUB_TAB)
        except gspread.WorksheetNotFound:
            ws = self.master.add_worksheet(title=HUB_TAB, rows=200,
                                           cols=len(HUB_HEADERS) + 1)
        if ws.row_values(1) != HUB_HEADERS:
            ws.insert_row(HUB_HEADERS, index=1)
        return ws

    def _jump(self, ws: gspread.Worksheet, label: str) -> str:
        """같은 스프레드시트 내 특정 탭(gid)으로 점프하는 HYPERLINK 수식."""
        return f'=HYPERLINK("{self.master.url}#gid={ws.id}","{label}")'

    def _register_in_hub(self, model: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._hub.append_row([
            model,
            self._jump(self._tabs[(model, "실행요약")],   "요약"),
            self._jump(self._tabs[(model, "상세로그")],   "상세"),
            self._jump(self._tabs[(model, "시나리오결과")], "시나리오"),
            self._jump(self._tabs[(model, "피처중요도")], "중요도"),
            ts,
        ], value_input_option="USER_ENTERED")

    # ── 모델 라우팅 ──────────────────────────────────────────────────

    def _tab(self, model: str, base: str, headers: list) -> gspread.Worksheet:
        """마스터 시트에서 `{model}_{base}` 탭 확보(없으면 생성+헤더). 캐시."""
        key = (model, base)
        if key in self._tabs:
            return self._tabs[key]
        title = f"{model}_{base}"
        try:
            ws = self.master.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.master.add_worksheet(title=title, rows=3000,
                                           cols=len(headers) + 1)
        if ws.row_values(1) != headers:
            ws.insert_row(headers, index=1)
        self._tabs[key] = ws
        return ws

    def _use(self, model: str):
        """현재 모델의 5개 탭 확보 + 허브 등록(최초 1회) 후 상태 설정."""
        if (model, "실행요약") not in self._tabs:
            for base, headers in FIXED_TABS.items():
                self._tab(model, base, headers)
            self._make_detail(model)
            if model not in self._inited:
                self._register_in_hub(model)
                self._inited.add(model)
                print(f"[sheets] 모델 탭 생성 + 허브 등록: {model}")
        self._cur_model = model

    # ── 내부 유틸 ────────────────────────────────────────────────────

    def _append_and_track(self, ws: gspread.Worksheet, row: list) -> int:
        data_rows = len(ws.get_all_values())
        if data_rows >= ws.row_count - 10:
            ws.add_rows(500)
        ws.append_row(row)
        return data_rows + 1

    def _make_detail(self, model: str) -> gspread.Worksheet:
        """모델 상세 탭(`{model}`) 확보. 헤더 = MODEL_HEADERS. (재귀 회피용 내부)"""
        key = (model, "__detail__")
        if key in self._tabs:
            return self._tabs[key]
        try:
            ws = self.master.worksheet(model)
        except gspread.WorksheetNotFound:
            ws = self.master.add_worksheet(title=model, rows=3000,
                                           cols=len(MODEL_HEADERS) + 1)
        if ws.row_values(1) != MODEL_HEADERS:
            ws.insert_row(MODEL_HEADERS, index=1)
        self._tabs[key] = ws
        return ws

    def _ensure_model_tab(self, model: str) -> gspread.Worksheet:
        """공개: 현재 모델 라우팅(5탭 확보) 후 상세 탭 반환."""
        self._use(model)
        return self._tabs[(model, "__detail__")]

    def _ws(self, title: str) -> gspread.Worksheet:
        return self._tabs[(self._cur_model, title)]

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

    # ── 시나리오 결과 ─────────────────────────────────────────────────

    def log_scenarios(self, branch: str, model: str, fp_target: str,
                      scenarios: dict[str, float]):
        """시나리오별 탐지율 기록 (시나리오결과 탭).
        scenarios: {시나리오명: 탐지율%}
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._use(model)
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
        if model:
            self._use(model)
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

    def update_run_summary(self, fe_steps: int = None,
                           fe_baseline: float = None,
                           fe_det: float = None,
                           fe_det_fp5: float = None,
                           fe_det_fp10: float = None,
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

        updates = {}
        if fe_steps is not None:
            updates["fe_steps"] = str(fe_steps)
        if fe_baseline is not None:
            updates["fe_baseline"] = f"{fe_baseline:.1f}"
        if fe_det is not None:
            updates["fe_det_fp1"] = f"{fe_det:.1f}"
        if fe_det_fp5 is not None:
            updates["fe_det_fp5"] = f"{fe_det_fp5:.1f}"
        if fe_det_fp10 is not None:
            updates["fe_det_fp10"] = f"{fe_det_fp10:.1f}"
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
        sheet_id=cfg["google_sheets"]["sheet_id"],
    )
