"""
단계별 알림 전송 모듈 — Discord Webhook

기본 사용 (간단):
    python notify.py "메시지" "제목(선택)"

상태 카드 사용 (Phase2 등 장기 작업):
    from notify import send_status_card
    send_status_card(
        title="Phase 2 / TASK 2-B 전처리",
        stage="TASK 2-B",
        progress_pct=62,
        eta_str="2h 14m",
        steps=[
            ("✅", "TASK 1 스케일링 비교", "F1 0.876→0.912"),
            ("✅", "2-A 전체월 다운로드", "3600/3600"),
            ("🔄", "2-B 전처리 진행 중", "2841/3600 (79%)"),
            ("⏳", "2-C TranAD+DCdetector 학습", "대기"),
            ("⏳", "2-D eval/시뮬레이션", "대기"),
        ],
        resources={
            "PID": "8144",
            "RAM": "2.4GB",
            "GPU": "RX 9060XT",
            "D드라이브": "1.42TB / 2.0TB (71%)",
        },
        elapsed_str="14h 03m",
        notes="csv.DictReader I/O 병목으로 예상보다 30분 지연",
    )
"""

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "notify_config.json"


def _load_webhook() -> str:
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())
        url = cfg.get("discord_webhook", "")
        if url and not url.startswith("YOUR_"):
            return url
    return os.environ.get("DISCORD_WEBHOOK", "")


def _post(payload: dict) -> bool:
    webhook = _load_webhook()
    if not webhook:
        print(f"[알림 스킵] webhook 미설정")
        return False
    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "JB-Pirate-King/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        print(f"[알림 오류] {e}")
        return False


def send(message: str, title: str = "JB-Pirate-King ML", color: int = 0x00ff88):
    """기본 Discord embed 알림 (단순 텍스트)"""
    now = datetime.now().strftime("%H:%M:%S")
    full_msg = f"[{now}] {message}"
    payload = {
        "embeds": [{
            "title": title,
            "description": full_msg,
            "color": color,
            "footer": {"text": "JB-Pirate-King IDS ML Pipeline"},
        }]
    }
    return _post(payload)


# ════════════════════════════════════════════════════════════════════
# 상태 카드 템플릿 — 장기 작업 전체 그림을 한 번에
# ════════════════════════════════════════════════════════════════════

def _progress_bar(pct: int, width: int = 12) -> str:
    """[████████░░░░] 형식 진행률 막대"""
    pct = max(0, min(100, int(pct)))
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _parse_eta_minutes(eta_str: str) -> float:
    """ETA 문자열을 분 단위로 파싱. 실패 시 None."""
    import re
    s = str(eta_str).lower().strip()
    if s in ("", "?", "-", "n/a", "none"):
        return None
    total = 0.0
    m = re.search(r"(\d+)\s*h", s)
    if m: total += int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*m", s)
    if m: total += int(m.group(1))
    # 그냥 숫자만 있으면 분으로 가정
    if total == 0 and re.match(r"^\d+(\.\d+)?$", s):
        total = float(s)
    return total if total > 0 else None


def send_status_card(
    title: str,
    stage: str = "",
    progress_pct: int = 0,
    eta_str: str = "?",
    steps: list = None,
    resources: dict = None,
    elapsed_str: str = "?",
    notes: str = "",
    color: int = None,
    session_summary: str = "",
    # ── 친화적 포맷 필드 (이 값이 있으면 새 템플릿 사용) ──
    completed: list = None,      # ["완료된 작업1", "완료된 작업2", ...]
    doing_now: str = "",         # 지금 하는 작업 평어 설명 (개행 포함 가능)
    next_up: list = None,        # ["다음 작업1", "다음 작업2"]
    time_breakdown: dict = None, # {"남은 다운로드": "약 30시간", "학습+평가": "약 5시간"}
):
    """
    한눈에 보이는 통합 상태 카드.

    Args:
        title: 카드 제목
        stage: 현재 단계 요약 (기존 호환용)
        progress_pct: 전체 진행률 (0-100)
        eta_str: 잔여 예상 시간 (예: "2h 14m")
        steps: [(아이콘, 단계명, 상세), ...] 최대 7개 (기존 호환용)
        resources: {"키": "값", ...} 리소스 정보
        elapsed_str: 경과 시간
        notes: 분석/메모
        color: 임베드 색상 (None → 진행률 기반 자동)
        session_summary: 세션 요약
        completed: 완료된 작업 목록 → 친화적 템플릿 활성화
        doing_now: 지금 하는 작업 설명 (평어체) → 친화적 템플릿 활성화
        next_up: 다음 작업 목록
        time_breakdown: 앞으로 남은 시간 항목별 분류
    """
    from datetime import timedelta
    steps     = steps or []
    resources = resources or {}

    # 자동 색상: 진행률 기반
    if color is None:
        if   progress_pct >= 100: color = 0xffd700  # 금색
        elif progress_pct >= 75:  color = 0x00ff88  # 초록
        elif progress_pct >= 25:  color = 0xf39c12  # 주황
        else:                     color = 0x3498db  # 파랑

    now_dt = datetime.now()
    now = now_dt.strftime("%H:%M:%S")
    bar = _progress_bar(progress_pct)

    # ETA → 종료 예상 시각 자동 계산
    eta_min = _parse_eta_minutes(eta_str)
    if eta_min is not None and eta_min > 0:
        finish_dt = now_dt + timedelta(minutes=eta_min)
        if eta_min < 60 * 24:
            finish_str = finish_dt.strftime("%H:%M")
        else:
            finish_str = finish_dt.strftime("%m/%d %H:%M")
        eta_display = f"{eta_str} (완료 예상: {finish_str})"
    else:
        eta_display = eta_str if eta_str not in ("?", "") else "산출 중"

    # ════════════════════════════════════════
    # 친화적 템플릿: completed/doing_now/next_up 중 하나라도 있으면 사용
    # ════════════════════════════════════════
    if completed is not None or doing_now or next_up is not None:
        lines = []
        lines.append("```")
        lines.append(f"[{now}]")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📊 전체 진행률: [{bar}] {progress_pct}%")

        if completed:
            lines.append("")
            lines.append("✅ 완료한 작업:")
            for c in completed:
                lines.append(f"   • {c}")

        if doing_now:
            lines.append("")
            lines.append("🔄 지금 하는 작업:")
            for dl in doing_now.split("\n"):
                if dl.strip():
                    lines.append(f"   {dl}")

        if next_up:
            lines.append("")
            lines.append("⏳ 다음 작업:")
            for n in next_up:
                lines.append(f"   • {n}")

        if time_breakdown:
            lines.append("")
            lines.append("🕒 앞으로 남은 시간:")
            for k, v in time_breakdown.items():
                lines.append(f"   • {k}: {v}")
        elif eta_str not in ("?", "-", ""):
            lines.append("")
            lines.append(f"🕒 예상 잔여: {eta_display}")

        if resources:
            lines.append("")
            items = list(resources.items())
            parts = [f"{k}: {v}" for k, v in items]
            # 2개씩 한 줄에
            for i in range(0, len(parts), 2):
                chunk = "  |  ".join(parts[i:i+2])
                lines.append(f"📦 {chunk}")

        if notes:
            lines.append("")
            lines.append(f"📝 {notes}")

        if session_summary:
            lines.append("")
            lines.append("🧠 분석:")
            for sl in session_summary.split("\n")[:4]:
                if sl.strip():
                    lines.append(f"   {sl}")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("```")

    else:
        # ════════════════════════════════════════
        # 기존 템플릿 (steps 기반 — 하위 호환)
        # ════════════════════════════════════════
        lines = []
        lines.append("```")
        lines.append(f"[{now}]  현재: {stage if stage else title}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📊 진행률 [{bar}] {progress_pct}%")
        lines.append(f"🕒 ETA  {eta_display}")
        lines.append(f"⏱️  경과 {elapsed_str}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if steps:
            for ic, name, detail in steps[:7]:
                d = f"  {detail}" if detail else ""
                lines.append(f"{ic} {name}{d}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if resources:
            items = list(resources.items())
            for i in range(0, len(items), 2):
                chunk = items[i:i+2]
                line = "  ".join(f"📦 {k}: {v}" for k, v in chunk)
                lines.append(line)
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if notes:
            lines.append(f"📝 {notes}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if session_summary:
            lines.append("🧠 세션 분석:")
            for s_line in session_summary.split("\n")[:6]:
                lines.append(f"   {s_line}")

        lines.append("```")

    description = "\n".join(lines)

    # Discord 임베드 description은 4096자 제한
    if len(description) > 4000:
        description = description[:3950] + "\n... (truncated)\n```"

    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": "JB-Pirate-King IDS ML Pipeline"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }]
    }
    return _post(payload)


# 단계별 알림 색상
COLORS = {
    "start":    0x3498db,   # 파란색  — 시작
    "done":     0x00ff88,   # 초록색  — 완료
    "error":    0xff4444,   # 빨간색  — 오류
    "train":    0xf39c12,   # 주황색  — 학습 중
    "finish":   0xffd700,   # 금색    — 전체 완료
    "warn":     0xff8800,   # 진주황  — 경고
}


if __name__ == "__main__":
    # CLI: 기본은 send(), --card 옵션이면 테스트 카드 보냄
    if len(sys.argv) > 1 and sys.argv[1] == "--card":
        send_status_card(
            title="JB-Pirate-King | 카드 테스트",
            stage="DEMO",
            progress_pct=62,
            eta_str="1h 30m",
            steps=[
                ("✅", "TASK 1 완료", "F1 0.876"),
                ("🔄", "TASK 2-B 전처리", "1800/2900"),
                ("⏳", "TASK 2-C 학습", "대기"),
            ],
            resources={
                "PID": "8144",
                "RAM": "2.4GB",
                "D드라이브": "1.42TB free",
            },
            elapsed_str="4h 12m",
            notes="pandas 최적화로 1패스 25분 예상 (이전 5h)",
        )
        print("카드 테스트 전송")
    else:
        msg   = sys.argv[1] if len(sys.argv) > 1 else "테스트 알림"
        title = sys.argv[2] if len(sys.argv) > 2 else "JB-Pirate-King ML"
        ok = send(msg, title)
        print("전송 완료" if ok else "전송 실패 (webhook 확인 필요)")
