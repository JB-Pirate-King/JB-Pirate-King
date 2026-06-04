#!/usr/bin/env python3
# coding: utf-8
"""
bootstrap.py — 새 세션 시작 시 현황 즉시 파악 스크립트
=======================================================
세션 시작 또는 `python ml/automation/bootstrap.py` 실행 시:
  1. Obsidian SSOT (현재 작업 + 작업 큐) 요약 출력
  2. 실행 중인 run(ens24, FE) 상태 확인
  3. distribute_manifest.json 감지 → 배포 대기 항목 알림
  4. 다음 할 일 명확히 출력

사용법
  python ml/automation/bootstrap.py          # 상태 요약 + 알림
  python ml/automation/bootstrap.py --full   # 전체 출력
  python ml/automation/bootstrap.py --distribute  # 매니페스트 처리 지시
"""
from __future__ import annotations
import json, os, sys, glob, time
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VAULT       = r"C:\ObsidianVault"
ENS_OUT     = r"D:\ais_output"
MODELS_DIR  = r"D:\ais_models"
REPO_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEP = "━" * 55

def _read(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return ""

def _j(path: str) -> dict:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


# ── 1. Obsidian SSOT 요약 ─────────────────────────────────────────
def show_obsidian():
    print(f"\n{SEP}")
    print("📖  OBSIDIAN SSOT 현황")
    print(SEP)

    # 현재 작업
    wip = os.path.join(VAULT, "운영", "_Working", "현재 작업.md")
    txt = _read(wip)
    if txt:
        # status 줄과 최근 완료/차기 섹션 추출
        for line in txt.split("\n"):
            if line.startswith("status:") or line.startswith("updated:"):
                print(f"  {line.strip()}")
        # 차기 작업 섹션
        in_next = False
        for line in txt.split("\n"):
            if "차기 작업" in line or "▶" in line:
                in_next = True
            if in_next:
                if line.strip() and not line.startswith("---"):
                    print(f"  {line.rstrip()}")
                if in_next and line == "" and "▶" not in line:
                    in_next = False
    else:
        print("  (현재 작업.md 없음)")

    # 다음 세션 지시
    handoff = os.path.join(VAULT, "운영", "_Working", "다음 세션 작업 지시.md")
    if os.path.exists(handoff):
        txt = _read(handoff)
        lines = [l for l in txt.split("\n") if l.startswith("## ") or l.startswith("- ")][:8]
        print("\n  📋 인수인계 지시서:")
        for l in lines:
            print(f"    {l.rstrip()}")


# ── 2. 실행중 run 상태 ───────────────────────────────────────────
def show_runs():
    print(f"\n{SEP}")
    print("🔄  RUN 상태")
    print(SEP)

    found = False
    for state_path in glob.glob(os.path.join(ENS_OUT, "ens24*/state.json")):
        d = _j(state_path)
        if not d:
            continue
        found = True
        done = os.path.exists(state_path.replace("state.json", "DONE.flag"))
        age  = time.time() - os.path.getmtime(state_path)
        stat = "✅ 완료" if done else ("⚡ 실행중" if age < 300 else "⏸️ 정지(재개 필요)")
        elapsed = str(timedelta(seconds=int(time.time() - d.get("t_start", time.time()))))
        best = d.get("best", {})
        print(f"  [{stat}] {d.get('run_id','-')}")
        print(f"    진행: {d.get('progress',0):.0f}%  |  단계: {d.get('stage_label','-')}")
        print(f"    경과: {elapsed}  |  최고: {best.get('label','-')} TPR={best.get('tpr_fp1','-')}%")
        if not done and age > 300:
            tag = state_path.split("ens24")[1].split("/")[0].strip("_\\")
            print(f"    👉 재개 명령: python ml/automation/ens24.py --tag {tag or 'v2'} --models conv1d --force ...")

    # FE 결과
    for fe_path in glob.glob(os.path.join(REPO_ROOT, "ml", ".pipeline_tmp", "fe_result_*.json")):
        d = _j(fe_path)
        if d:
            found = True
            print(f"  ✅ FE 결과: {os.path.basename(fe_path)}")
            print(f"     탐지율 FP1={d.get('best_det','-')}%  |  채택피처: {d.get('best_extra',[])}")

    if not found:
        print("  (실행중/완료 run 없음)")


# ── 3. distribute_manifest 감지 ──────────────────────────────────
def show_pending_distribute():
    manifests = glob.glob(os.path.join(ENS_OUT, "ens24*/distribute_manifest.json"))
    if not manifests:
        return

    print(f"\n{SEP}")
    print("📤  배포 대기 항목 발견 (MCP 처리 필요)")
    print(SEP)
    for mp in manifests:
        d = _j(mp)
        pending = d.get("pending", [])
        if not pending:
            continue
        print(f"  📄 {mp}")
        print(f"     run: {d.get('run_id','-')}  TPR={d.get('tpr_fp1','-')}%")
        print(f"     대기 항목: {', '.join(pending)}")
        print()
        print("  👉 Claude Code에서 처리할 명령:")
        print("     python ml/automation/bootstrap.py --distribute")
        print()
        print(f"     또는 직접 처리:")
        if "sheets_update" in pending:
            print(f"       - Sheets 업데이트: sheets_id={d.get('sheets_id')}")
            print(f"         결과: tpr={d.get('tpr_fp1')} pr_auc={d.get('pr_auc')} model={d.get('model')}")
        if "drive_upload" in pending:
            print(f"       - Drive 업로드: folder_id={d.get('drive_folder_id')}")
            print(f"         파일: {d.get('final_report')} + {d.get('stage2_results')}")
        if "notion_update" in pending:
            print(f"       - Notion: 주차별 메모 갱신 필요")


# ── 4. 모델 현황 ─────────────────────────────────────────────────
def show_models():
    print(f"\n{SEP}")
    print("🤖  배포 모델 현황")
    print(SEP)
    for mdir in glob.glob(os.path.join(MODELS_DIR, "*")):
        name = os.path.basename(mdir)
        onnx = os.path.join(mdir, f"model_{name}.onnx")
        thr  = os.path.join(mdir, f"threshold_{name}.txt")
        if os.path.exists(onnx):
            mtime = datetime.fromtimestamp(os.path.getmtime(onnx)).strftime("%Y-%m-%d")
            thr_val = open(thr).read().strip()[:15] if os.path.exists(thr) else "-"
            size = os.path.getsize(onnx) // 1024
            print(f"  [{mtime}] {name:<15} {size:>6}KB  threshold={thr_val}")


# ── 5. 다음 할 일 요약 ───────────────────────────────────────────
def show_next_actions():
    print(f"\n{SEP}")
    print("▶  다음 할 일")
    print(SEP)

    # 매니페스트 대기 항목 우선
    manifests = glob.glob(os.path.join(ENS_OUT, "ens24*/distribute_manifest.json"))
    for mp in manifests:
        d = _j(mp)
        if d.get("pending"):
            print(f"  🔴 [즉시] 배포 대기: {', '.join(d['pending'])}")
            print(f"       → bootstrap.py --distribute 실행")

    # Obsidian 작업 큐
    queue = os.path.join(VAULT, "운영", "06 작업 큐 (Next Actions).md")
    txt = _read(queue)
    in_next = False
    count = 0
    for line in txt.split("\n"):
        if "지금/다음" in line:
            in_next = True
            continue
        if in_next and line.startswith("## "):
            break
        if in_next and "- [ ]" in line and count < 3:
            print(f"  ⬜ {line.strip()[6:]}")
            count += 1
        if in_next and "- [~]" in line and count < 3:
            print(f"  🔵 {line.strip()[6:]} (진행중)")
            count += 1


# ── distribute 처리 (--distribute 플래그) ────────────────────────
def handle_distribute():
    """매니페스트를 읽고 Claude Code가 실행할 MCP 작업 지시를 출력."""
    manifests = glob.glob(os.path.join(ENS_OUT, "ens24*/distribute_manifest.json"))
    if not manifests:
        print("처리할 매니페스트 없음.")
        return

    for mp in manifests:
        d = _j(mp)
        if not d.get("pending"):
            continue

        print(f"\n{'='*55}")
        print(f"배포 지시서: {os.path.basename(os.path.dirname(mp))}")
        print(f"{'='*55}")
        print(f"run_id: {d.get('run_id')}")
        print(f"모델: {d.get('model')} / {d.get('method')}")
        print(f"TPR@FP1: {d.get('tpr_fp1')}%  CV: {d.get('cv_mean')}±{d.get('cv_std')}%")
        print()

        pending = d.get("pending", [])

        if "sheets_update" in pending:
            print("📊 [Sheets 업데이트 필요]")
            print(f"   시트 ID: {d.get('sheets_id')}")
            print(f"   탭: 🤖ML모델성능, 시나리오결과, 피처중요도")
            print(f"   데이터: tpr={d.get('tpr_fp1')} fpr={d.get('fpr')} "
                  f"pr_auc={d.get('pr_auc')} roc_auc={d.get('roc_auc')} f1={d.get('f1')}")
            print(f"   시나리오: {os.path.join(os.path.dirname(mp), 'stage2_results.json')}")
            print()

        if "drive_upload" in pending:
            print("☁️  [Drive 업로드 필요]")
            print(f"   폴더 ID: {d.get('drive_folder_id')}")
            print(f"   파일:")
            print(f"     - {d.get('final_report')}")
            print(f"     - {d.get('stage2_results')}")
            print(f"   GitHub Release(모델): {d.get('github_release','-')}")
            print()

        if "notion_update" in pending:
            print("📝 [Notion 업데이트 필요]")
            print(f"   주차별 메모에 이번 주 run 결과 추가")
            print()

        print("✅ 처리 완료 후 pending 필드를 []로 비워주세요:")
        print(f"   {mp}")


# ── main ─────────────────────────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="세션 부트스트랩 / 현황 요약")
    ap.add_argument("--full", action="store_true", help="전체 상세 출력")
    ap.add_argument("--distribute", action="store_true", help="매니페스트 처리 지시")
    args = ap.parse_args()

    if args.distribute:
        handle_distribute()
        return

    print(f"\n{'='*55}")
    print(f"  🚢 JB-Pirate-King 세션 부트스트랩  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*55}")

    show_obsidian()
    show_runs()
    show_pending_distribute()
    if args.full:
        show_models()
    show_next_actions()

    print(f"\n{SEP}")
    print("💡  상세 인수인계: C:\\ObsidianVault\\운영\\실험\\ens24_run_2026-06-04.md")
    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
