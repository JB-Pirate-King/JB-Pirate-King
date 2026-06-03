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
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.stdout.strip()


def get_next_run_num(model: str) -> int:
    """기존 브랜치에서 다음 번호 계산. dcdetect_001 → 다음은 002.

    원격에만 있는 run 브랜치 번호를 놓치지 않도록 best-effort fetch 선행
    (오프라인/실패 시 무시하고 로컬+캐시된 원격 ref 로 계산)."""
    subprocess.run(
        ["git", "fetch", "--quiet", PUSH_REMOTE, "--prune"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    branches = _run(["git", "branch", "-a"]).splitlines()
    pattern = re.compile(rf"^\s*(?:remotes/(?:origin|upstream)/)?{re.escape(model)}_(\d+)$")
    nums = []
    for b in branches:
        m = pattern.match(b)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def _local_branches() -> list[str]:
    """로컬 브랜치명 목록 (정확 매칭용, 현재 브랜치 '* ' 마커 제거)."""
    out = _run(["git", "branch", "--format=%(refname:short)"])
    return [b.strip() for b in out.splitlines() if b.strip()]


def create_branch(model: str, run_num: int, base: str = None) -> str:
    """브랜치 생성, 체크아웃, project(upstream) 푸시. 반환값: 브랜치명.

    base 미지정 시: 직전 run 브랜치(model_{run_num-1:03d})가 있으면 그 위에,
    없으면 develop(없으면 현재)에서 분기 → run 간 커밋 누적이 git 히스토리로 이어짐.
    """
    branch  = f"{model}_{run_num:03d}"
    current = _run(["git", "branch", "--show-current"])
    locals_ = _local_branches()

    if base is None:
        prev = f"{model}_{run_num - 1:03d}"
        if run_num > 1 and prev in locals_:
            base = prev
        elif "develop" in locals_:
            base = "develop"
        else:
            base = current
    _run(["git", "checkout", "-b", branch, base])

    result = subprocess.run(
        ["git", "push", "-u", PUSH_REMOTE, branch],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
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
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode == 0:
        print(f"커밋 완료: {message}")
        push = subprocess.run(
            ["git", "push"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if push.returncode == 0:
            print(f"푸시 완료: {branch or _run(['git', 'branch', '--show-current'])}")
        else:
            print(f"푸시 실패: {push.stderr.strip()}")
    else:
        print(f"커밋 실패 또는 변경사항 없음: {result.stderr.strip()}")


def checkout(branch: str):
    _run(["git", "checkout", branch])
