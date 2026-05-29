"""
파이프라인 Git 브랜치 관리 — iter마다 dcdetect_001, dcdetect_002 ... 생성

브랜치/커밋은 project 저장소(upstream remote = JB-Pirate-King/JB-Pirate-King)로 push.
"""
import subprocess
import sys
import re

sys.stdout.reconfigure(encoding="utf-8")

# 모델별 브랜치를 push 할 대상 remote (project 저장소)
PUSH_REMOTE = "upstream"


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


def get_next_run_num(model: str) -> int:
    """기존 브랜치에서 다음 번호 계산. dcdetect_001 → 다음은 002"""
    branches = _run(["git", "branch", "-a"]).splitlines()
    pattern = re.compile(rf"^\s*(?:remotes/(?:origin|upstream)/)?{re.escape(model)}_(\d+)$")
    nums = []
    for b in branches:
        m = pattern.match(b)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def create_branch(model: str, run_num: int) -> str:
    """브랜치 생성, 체크아웃, project(upstream) 푸시. 반환값: 브랜치명"""
    branch = f"{model}_{run_num:03d}"
    current = _run(["git", "branch", "--show-current"])

    base = "develop" if "develop" in _run(["git", "branch"]) else current
    _run(["git", "checkout", "-b", branch, base])

    result = subprocess.run(
        ["git", "push", "-u", PUSH_REMOTE, branch],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"브랜치 생성 + 푸시: {branch} (base: {base})")
    else:
        print(f"브랜치 생성: {branch} (base: {base}) — 푸시 실패: {result.stderr.strip()}")
    return branch


def commit_results(files: list[str], message: str, branch: str = None):
    """결과 파일 커밋 후 project(upstream) 푸시 (-u 로 설정된 추적 브랜치)"""
    if branch:
        current = _run(["git", "branch", "--show-current"])
        if current != branch:
            _run(["git", "checkout", branch])

    for f in files:
        _run(["git", "add", f])

    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"커밋 완료: {message}")
        push = subprocess.run(
            ["git", "push"],
            capture_output=True, text=True
        )
        if push.returncode == 0:
            print(f"푸시 완료: {branch or _run(['git', 'branch', '--show-current'])}")
        else:
            print(f"푸시 실패: {push.stderr.strip()}")
    else:
        print(f"커밋 실패 또는 변경사항 없음: {result.stderr.strip()}")


def checkout(branch: str):
    _run(["git", "checkout", branch])


def current_branch() -> str:
    return _run(["git", "branch", "--show-current"])
