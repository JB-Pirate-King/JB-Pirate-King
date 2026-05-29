"""
automation/_test_run.py
파이프라인 시범 가동 스크립트.
실제 학습 없이 더미 CSV를 생성하고 전체 자동화 체인을 한 바퀴 돌린다.
실행: python automation/_test_run.py
"""
import sys, os, csv, tempfile, time, shutil
sys.stdout.reconfigure(encoding="utf-8")

# ── 프로젝트 루트 기준 임포트
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = "✅"
FAIL = "❌"
SKIP = "⏭ "

results = []

def report(name, ok, note=""):
    icon = PASS if ok else (SKIP if "스킵" in str(note) else FAIL)
    line = f"  {icon} {name:<30} {note}"
    print(line)
    results.append((name, ok, note))


# ────────────────────────────────────────────────────
# 1) 환경변수 / config 로딩
# ────────────────────────────────────────────────────
print("\n[1] config 로딩")
from automation.config import (
    MLFLOW_TRACKING_URI, SLACK_BOT_TOKEN, DISCORD_WEBHOOK_URL,
    NOTION_API_KEY, GSHEETS_SPREADSHEET_ID, GITHUB_TOKEN,
)
print(f"  MLflow URI  : {MLFLOW_TRACKING_URI}")
print(f"  Slack       : {'설정됨' if SLACK_BOT_TOKEN else '미설정'}")
print(f"  Discord     : {'설정됨' if DISCORD_WEBHOOK_URL else '미설정'}")
print(f"  Notion      : {'설정됨' if NOTION_API_KEY else '미설정'}")
print(f"  Sheets      : {'설정됨' if GSHEETS_SPREADSHEET_ID else '미설정'}")
print(f"  GitHub      : {'설정됨' if GITHUB_TOKEN else '미설정'}")
report("config 로딩", True)


# ────────────────────────────────────────────────────
# 2) 더미 eval CSV 생성 (pipeline.py 출력 시뮬레이션)
# ────────────────────────────────────────────────────
print("\n[2] 더미 eval CSV 생성")
TEST_MODEL  = "conv1d"
TEST_EPOCHS = 3
DUMMY_OUTPUT_DIR = os.path.join(ROOT, "_test_output")
os.makedirs(DUMMY_OUTPUT_DIR, exist_ok=True)

ts = time.strftime("%Y%m%d_%H%M%S")
dummy_csv = os.path.join(DUMMY_OUTPUT_DIR, f"{TEST_MODEL}_{ts}.csv")

rows = [
    # group, scenario, detection_rate
    ("Basic", "cog_hdg_mismatch",   85.0),
    ("Basic", "anchored_movement",  78.0),
    ("Basic", "speed_anomaly",      92.0),
    ("Basic", "position_jump",      88.0),
    ("FN",    "fn_scenario_1",      60.0),
    ("FN",    "fn_scenario_2",      55.0),
    ("D",     "d_evasion_1",        70.0),
    ("E",     "e_evasion_1",        65.0),
    ("F",     "f_holdout_1",        72.0),  # holdout
    ("F",     "f_holdout_2",        68.0),  # holdout
    ("G",     "g_holdout_1",        74.0),  # holdout
    ("G",     "g_holdout_2",        71.0),  # holdout
]
with open(dummy_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["group", "scenario", "detection_rate", "fp_rate"])
    for g, s, dr in rows:
        w.writerow([g, s, dr, 1.0])

# 더미 comparison CSV
cmp_csv = os.path.join(DUMMY_OUTPUT_DIR, f"comparison_{ts}.csv")
with open(cmp_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["model", "train_dr", "holdout_dr", "fp_rate"])
    w.writerow([TEST_MODEL, 74.8, 71.2, 1.0])

print(f"  {PASS} 더미 CSV: {dummy_csv}")
report("더미 CSV 생성", True, os.path.basename(dummy_csv))


# ────────────────────────────────────────────────────
# 3) MLflow 실험 기록 (파일 기반, 서버 불필요)
# ────────────────────────────────────────────────────
print("\n[3] MLflow 실험 기록")
try:
    import mlflow
    mlflow_dir = os.path.join(ROOT, "_test_mlruns")
    mlflow.set_tracking_uri(f"file:///{mlflow_dir.replace(os.sep, '/')}")
    mlflow.set_experiment("ais-anomaly-detection-test")

    with mlflow.start_run(run_name=f"{TEST_MODEL}_ep{TEST_EPOCHS}_test") as run:
        mlflow.set_tag("model_name", TEST_MODEL)
        mlflow.log_params({"model": TEST_MODEL, "epochs": TEST_EPOCHS, "seq_len": 10, "n_features": 12})

        # 에포크별 메트릭 시뮬레이션
        for ep in range(1, TEST_EPOCHS + 1):
            mlflow.log_metrics({"train_loss": 0.1 / ep, "val_loss": 0.12 / ep}, step=ep)

        mlflow.log_metrics({"detection_rate": 74.8, "holdout_dr": 71.2, "fp_rate": 1.0})
        mlflow.log_artifact(dummy_csv)

        run_id = run.info.run_id

    report("MLflow 기록", True, f"run_id={run_id[:8]}…  (file://_test_mlruns)")
    print(f"  💡 UI 확인: mlflow ui --backend-store-uri file:///{mlflow_dir.replace(os.sep, '/')}")
except Exception as e:
    report("MLflow 기록", False, str(e))
    run_id = ""


# ────────────────────────────────────────────────────
# 4) Slack 알림
# ────────────────────────────────────────────────────
print("\n[4] Slack 알림")
try:
    from automation.notify import Notifier
    n = Notifier()
    if not SLACK_BOT_TOKEN:
        report("Slack 알림", True, "스킵 (토큰 미설정)")
    else:
        n.training_complete(TEST_MODEL, dr=74.8, holdout=71.2, fp=1.0,
                            elapsed_min=1.0, version="test")
        report("Slack 알림", True, "전송 완료")
except Exception as e:
    report("Slack 알림", False, str(e))


# ────────────────────────────────────────────────────
# 5) Discord 알림
# ────────────────────────────────────────────────────
print("\n[5] Discord 알림")
try:
    from automation.notify import Notifier
    n = Notifier()
    if not DISCORD_WEBHOOK_URL:
        report("Discord 알림", True, "스킵 (웹훅 미설정)")
    else:
        n.training_complete(TEST_MODEL, dr=74.8, holdout=71.2, fp=1.0,
                            elapsed_min=1.0, version="test")
        report("Discord 알림", True, "전송 완료")
except Exception as e:
    report("Discord 알림", False, str(e))


# ────────────────────────────────────────────────────
# 6) Google Sheets 기록
# ────────────────────────────────────────────────────
print("\n[6] Google Sheets 기록")
try:
    from automation.sheets_tracker import SheetsTracker
    if not GSHEETS_SPREADSHEET_ID:
        report("Google Sheets", True, "스킵 (SPREADSHEET_ID 미설정)")
    else:
        s = SheetsTracker()
        if s._disabled:
            report("Google Sheets", False, "연결 실패 (credentials.json 없음 또는 권한 오류)")
        else:
            s.log_result(TEST_MODEL, TEST_EPOCHS, 74.8, 71.2, 1.0, run_id)
            report("Google Sheets", True, "기록 완료 → 🤖 ML 모델 성능 시트")
except Exception as e:
    report("Google Sheets", False, str(e))


# ────────────────────────────────────────────────────
# 7) Notion 페이지 생성
# ────────────────────────────────────────────────────
print("\n[7] Notion 페이지 생성")
try:
    from automation.notion_reporter import NotionReporter
    if not NOTION_API_KEY:
        report("Notion", True, "스킵 (API 키 미설정)")
    else:
        r = NotionReporter()
        page_id = r.create_experiment_page(
            TEST_MODEL, TEST_EPOCHS, 74.8, 71.2, 1.0,
            notes="[테스트 런] 시범 가동 — 실제 학습 없음"
        )
        report("Notion", True, f"page_id={page_id}")
except Exception as e:
    report("Notion", False, str(e))


# ────────────────────────────────────────────────────
# 8) GitHub Release (dry-run)
# ────────────────────────────────────────────────────
print("\n[8] GitHub Release 연결 확인")
try:
    from automation.github_release import GithubReleaser
    if not GITHUB_TOKEN:
        report("GitHub Release", True, "스킵 (토큰 미설정)")
    else:
        r = GithubReleaser()
        latest = r.get_latest_release_tag()
        report("GitHub Release", True, f"최신 릴리즈: {latest}")
except Exception as e:
    report("GitHub Release", False, str(e))


# ────────────────────────────────────────────────────
# 정리
# ────────────────────────────────────────────────────
shutil.rmtree(DUMMY_OUTPUT_DIR, ignore_errors=True)

print("\n" + "="*55)
print("  시범 가동 결과")
print("="*55)
ok_cnt   = sum(1 for _, ok, _ in results if ok)
fail_cnt = sum(1 for _, ok, n in results if not ok and "스킵" not in str(n))
skip_cnt = sum(1 for _, _, n in results if "스킵" in str(n))
for name, ok, note in results:
    icon = PASS if ok else (SKIP if "스킵" in str(note) else FAIL)
    print(f"  {icon}  {name:<30} {note}")

print(f"\n  통과: {ok_cnt}  |  스킵(미설정): {skip_cnt}  |  실패: {fail_cnt}")
print("="*55)
print("\n💡 스킵된 서비스는 automation/.env 에 토큰을 입력하면 활성화됩니다.")
