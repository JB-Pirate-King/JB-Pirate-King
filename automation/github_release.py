"""
automation/github_release.py

GitHub Release를 자동 생성하고 모델 파일을 첨부한다.

사용법:
    from automation.github_release import GithubReleaser

    r = GithubReleaser()
    url = r.create_release(
        tag="v0.2.0",
        title="v0.2.0 — conv1d / tranad / dcdetect",
        models=["conv1d", "tranad"],
        comparison_txt=r"D:\ais_output\pipeline\comparison_20251014.txt",
        comparison_csv=r"D:\ais_output\pipeline\comparison_20251014.csv",
        notes={
            "conv1d":   {"train_dr": 68.3, "holdout_dr": 70.8},
            "tranad":   {"train_dr": 35.4, "holdout_dr": 61.0},
        },
        target_branch="main",
    )
"""

import os
from github import Github, GithubException
from automation.config import GITHUB_TOKEN, GITHUB_REPO, MODELS_DIR


def _model_files(model: str, base_dir: str) -> list[str]:
    model_dir = os.path.join(base_dir, model)
    candidates = [
        os.path.join(model_dir, f"model_{model}.onnx"),
        os.path.join(model_dir, f"scaler_{model}.json"),
        os.path.join(model_dir, f"threshold_{model}.txt"),
        # 루트 레벨 fallback
        os.path.join(base_dir, f"model_{model}.onnx"),
        os.path.join(base_dir, f"scaler_{model}.json"),
        os.path.join(base_dir, f"threshold_{model}.txt"),
    ]
    return [p for p in candidates if os.path.exists(p)]


def _build_release_notes(models: list[str], notes: dict) -> str:
    lines = ["## Models", ""]
    for m in models:
        lines.append(f"- `{m}`")
    lines += ["", "## Performance (FP ≈ 1%)", ""]
    for m in models:
        if m in notes:
            d = notes[m]
            lines.append(
                f"- **{m}**: 학습 {d.get('train_dr', '?')}% / 홀드아웃 {d.get('holdout_dr', '?')}%"
            )
    lines += [
        "",
        "## Plugin Deploy",
        "```",
        "# 학습 후 플러그인에 모델 배포",
        "cp model_{name}.onnx ais_ids_pi/data/model.onnx",
        "cp scaler_{name}.json ais_ids_pi/data/scaler.json",
        "cp threshold_{name}.txt ais_ids_pi/data/threshold.txt",
        "```",
    ]
    return "\n".join(lines)


class GithubReleaser:
    def __init__(self):
        if not GITHUB_TOKEN:
            print("[github] GITHUB_TOKEN 미설정 — GitHub 릴리즈 스킵")
            self._disabled = True
            return
        self._disabled = False
        self._repo = Github(GITHUB_TOKEN).get_repo(GITHUB_REPO)

    def create_release(
        self,
        tag: str,
        title: str,
        models: list[str],
        notes: dict | None = None,
        comparison_txt: str | None = None,
        comparison_csv: str | None = None,
        target_branch: str = "main",
    ) -> str | None:
        if self._disabled:
            return None

        body = _build_release_notes(models, notes or {})

        try:
            release = self._repo.create_git_release(
                tag=tag,
                name=title,
                message=body,
                target_commitish=target_branch,
                draft=False,
                prerelease=False,
            )
        except GithubException as e:
            print(f"[github] 릴리즈 생성 실패: {e}")
            return None

        # 모델 파일 첨부
        for model in models:
            for path in _model_files(model, MODELS_DIR):
                fname = os.path.basename(path)
                with open(path, "rb") as f:
                    release.upload_asset(path=path, name=fname)
                print(f"[github] 첨부: {fname}")

        # 비교 리포트 첨부
        for path in filter(None, [comparison_txt, comparison_csv]):
            if os.path.exists(path):
                fname = os.path.basename(path)
                with open(path, "rb") as f:
                    release.upload_asset(path=path, name=fname)
                print(f"[github] 첨부: {fname}")

        print(f"[github] 릴리즈 생성 완료: {release.html_url}")
        return release.html_url

    def get_latest_release_tag(self) -> str | None:
        if self._disabled:
            return None
        try:
            return self._repo.get_latest_release().tag_name
        except GithubException:
            return None
