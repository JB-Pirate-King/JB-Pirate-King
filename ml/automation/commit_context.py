#!/usr/bin/env python3
# coding: utf-8
"""
commit_context.py — git 커밋 이력을 에이전트용 컨텍스트 파일로 저장
==================================================================
목적
  팀(나 + heahgo)과 GitHub PR 머지로 쌓이는 커밋 이력을, 에이전트가
  매 세션 저렴하게 읽을 수 있는 마크다운 한 장으로 정리한다.
  - bootstrap.py 가 세션 시작마다 자동 호출 -> 항상 최신 상태 유지
  - 출력 파일은 git 에서 파생된 캐시이므로 .gitignore (커밋하지 않음).
    git 자체가 원본이라, 어느 머신에서든 git log 로 동일 내용이 재생성된다.

사용법
  python ml/automation/commit_context.py                # 캐시 갱신
  python ml/automation/commit_context.py --recent 12 --total 120
  python ml/automation/commit_context.py --print        # 갱신 후 요약 출력
"""
from __future__ import annotations
import os, sys, subprocess, argparse
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR   = os.path.join(REPO_ROOT, "ml", "automation", "context")
OUT_FILE  = os.path.join(OUT_DIR, "commit_log.md")


def _git(args: list[str]) -> str:
    try:
        r = subprocess.run(["git", "-C", REPO_ROOT, *args],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()
    except Exception:
        return ""


def generate(recent: int = 15, total: int = 150) -> dict:
    """commit_log.md 를 갱신하고 요약 dict 를 반환한다."""
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]) or "-"
    head   = _git(["rev-parse", "--short", "HEAD"]) or "-"

    # 전체 한 줄 목록(머지 포함 -> PR 경계가 보인다), 최신순
    oneline = _git(["log", f"-{total}",
                    "--pretty=format:- %ad `%h` %s (%an)", "--date=short"])
    oneline_lines = [l for l in oneline.split("\n") if l.strip()]

    # 최근 N개 상세(머지 제외, 변경 파일 포함)
    detailed = _git(["log", f"-{recent}", "--no-merges", "--stat",
                     "--date=short",
                     "--pretty=format:%n### %ad `%h` %s%n- author: %an%n"])

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Commit context (auto-generated)\n\n")
        f.write(f"> 생성: {datetime.now():%Y-%m-%d %H:%M}  ·  branch `{branch}`  ·  HEAD `{head}`\n")
        f.write("> git 에서 파생된 캐시. 직접 수정하지 말 것 — commit_context.py 가 재생성한다.\n\n")
        f.write(f"## 최근 {recent}개 커밋 (변경 파일 포함)\n")
        f.write((detailed or "(없음)") + "\n\n")
        f.write(f"## 최근 {total}개 커밋 요약 (머지/PR 포함, 최신순)\n\n")
        f.write((oneline or "(없음)") + "\n")

    return {"branch": branch, "head": head,
            "recent_oneline": oneline_lines, "out_file": OUT_FILE,
            "count": len(oneline_lines)}


def main():
    ap = argparse.ArgumentParser(description="git 커밋 이력 -> 에이전트용 컨텍스트 파일")
    ap.add_argument("--recent", type=int, default=15, help="상세 표기할 최근 커밋 수")
    ap.add_argument("--total",  type=int, default=150, help="요약 목록 커밋 수")
    ap.add_argument("--print", dest="do_print", action="store_true", help="갱신 후 요약 출력")
    args = ap.parse_args()

    info = generate(args.recent, args.total)
    rel = os.path.relpath(info["out_file"], REPO_ROOT)
    print(f"commit_log.md 갱신: {info['count']}개 커밋  ·  branch {info['branch']}  ·  -> {rel}")
    if args.do_print:
        for line in info["recent_oneline"][:10]:
            print("  " + line)


if __name__ == "__main__":
    main()
