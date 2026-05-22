r"""
Google Drive 업로드 도우미
==========================
Phase 2 완료 후 호출되는 마커 기반 업로드.

배경:
  - 백그라운드 Python 프로세스는 MCP 도구(Claude 세션 도구)에 접근 불가
  - 따라서 "업로드 대상 목록"을 마커 파일로 남기고
  - 사용자가 Claude 세션을 열면 (이 채팅) MCP로 실제 업로드 수행

마커 파일: D:\JB-Pirate-King-ML-Results\UPLOAD_MANIFEST.txt
  각 줄에 업로드할 파일의 절대경로 기재.

사용 (오케스트레이터 내부):
  from gdrive_upload_helper import register_for_upload
  register_for_upload([Path("D:/.../model_tranad.onnx"), ...])

이후 Claude 세션에서:
  "구글 드라이브 업로드해줘" → MCP로 일괄 업로드
"""

import os
from pathlib import Path
from datetime import datetime

UPLOAD_MARKER = Path(r"D:\JB-Pirate-King-ML-Results\UPLOAD_MANIFEST.txt")
UPLOAD_DONE   = Path(r"D:\JB-Pirate-King-ML-Results\UPLOAD_DONE.flag")

# 업로드 대상 기본 패턴 (큰 산출물)
DEFAULT_TARGETS = [
    # 앙상블 모델
    r"D:\JB-Pirate-King-ML-Results\ensemble_full\model_*.onnx",
    r"D:\JB-Pirate-King-ML-Results\ensemble_full\model_*.pt",
    r"D:\JB-Pirate-King-ML-Results\ensemble_full\scaler_*.json",
    r"D:\JB-Pirate-King-ML-Results\ensemble_full\threshold_*.txt",
    r"D:\JB-Pirate-King-ML-Results\ensemble_full\eval_summary.txt",
    # v3 학습 결과
    r"D:\JB-Pirate-King-ML-Results\model_*.onnx",
    r"D:\JB-Pirate-King-ML-Results\eval_summary.txt",
    r"D:\JB-Pirate-King-ML-Results\scaling_compare_result.txt",
    # 스케일링 비교 결과
    r"D:\JB-Pirate-King-ML-Results\scale_5yr\model_*.onnx",
    r"D:\JB-Pirate-King-ML-Results\scale_5yr\eval_summary.txt",
    # 최종 로그
    r"D:\JB-Pirate-King-ML-Results\pipeline_v3.log",
    r"D:\JB-Pirate-King-ML-Results\phase2_auto.log",
    r"D:\JB-Pirate-King-ML-Results\train_full_v3.log",
]

# 크기 임계값: 깃에 못 올릴 정도(>50MB)면 무조건 업로드 대상
GIT_SIZE_LIMIT_MB = 50


def collect_upload_targets(extra_patterns: list = None) -> list:
    """업로드 대상 수집. (절대경로, 크기MB) 목록 반환."""
    import glob
    patterns = list(DEFAULT_TARGETS)
    if extra_patterns:
        patterns.extend(extra_patterns)

    found = {}
    for pat in patterns:
        for fpath in glob.glob(pat):
            p = Path(fpath)
            if p.is_file() and p.stat().st_size > 0:
                found[str(p.resolve())] = p.stat().st_size / (1024**2)

    # 깃 한도 초과 파일만, 또는 핵심 결과 파일은 무조건 포함
    targets = []
    for path, size_mb in sorted(found.items(), key=lambda x: -x[1]):
        is_core = any(k in path.lower() for k in
                      ("eval_summary", "scaling_compare", "best_ensemble"))
        if size_mb >= GIT_SIZE_LIMIT_MB or is_core:
            targets.append((path, size_mb))
    return targets


def write_manifest(targets: list = None) -> Path:
    """업로드 매니페스트 파일 생성."""
    if targets is None:
        targets = collect_upload_targets()

    UPLOAD_MARKER.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# JB-Pirate-King 업로드 매니페스트",
        f"# 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# 총 {len(targets)}개 파일",
        f"# 형식: <크기MB> <절대경로>",
        f"",
    ]
    total_mb = 0
    for path, size_mb in targets:
        lines.append(f"{size_mb:8.1f}MB  {path}")
        total_mb += size_mb
    lines.append("")
    lines.append(f"# 총 용량: {total_mb:.1f}MB")

    UPLOAD_MARKER.write_text("\n".join(lines), encoding="utf-8")
    return UPLOAD_MARKER


def mark_upload_ready():
    """Phase 2 완료 시 호출. 매니페스트 생성 + Discord 알림."""
    targets = collect_upload_targets()
    write_manifest(targets)

    total_mb = sum(s for _, s in targets)
    msg = (
        f"📦 업로드 준비 완료!\n"
        f"파일 {len(targets)}개 / 총 {total_mb:.1f}MB\n"
        f"매니페스트: {UPLOAD_MARKER}\n\n"
        f"Claude 세션 열고 '구글 드라이브 업로드해줘'라고 말하면\n"
        f"MCP로 일괄 업로드합니다."
    )
    print(msg)
    try:
        from notify import send_status_card
        steps = [("📤", Path(p).name, f"{s:.1f}MB") for p, s in targets[:6]]
        send_status_card(
            title="JB-Pirate-King | 업로드 준비 완료",
            stage="GDrive 업로드 대기",
            progress_pct=100,
            eta_str="-",
            steps=steps,
            resources={
                "파일 수":  str(len(targets)),
                "총 용량":  f"{total_mb:.1f}MB",
                "매니페스트": str(UPLOAD_MARKER),
            },
            elapsed_str="-",
            notes="Claude 세션에서 '구글 드라이브 업로드해줘'라고 요청하세요",
        )
    except Exception:
        try:
            from notify import send
            send(msg, "JB | 업로드 준비 완료")
        except Exception:
            pass


def mark_upload_done(uploaded_files: list, drive_folder_url: str = ""):
    """MCP 업로드 완료 시 (이 세션에서) 호출. 완료 플래그 생성."""
    lines = [
        f"# 업로드 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# 폴더: {drive_folder_url}",
        f"# 파일 {len(uploaded_files)}개",
        f"",
    ]
    for f in uploaded_files:
        lines.append(str(f))
    UPLOAD_DONE.write_text("\n".join(lines), encoding="utf-8")
    print(f"업로드 완료 플래그: {UPLOAD_DONE}")


if __name__ == "__main__":
    # CLI: 현재 시점의 업로드 매니페스트 출력
    targets = collect_upload_targets()
    print(f"\n업로드 대상: {len(targets)}개")
    for path, size_mb in targets:
        print(f"  {size_mb:8.1f}MB  {Path(path).name}")
    total = sum(s for _, s in targets)
    print(f"\n총 용량: {total:.1f}MB")
    write_manifest(targets)
    print(f"매니페스트 저장: {UPLOAD_MARKER}")
