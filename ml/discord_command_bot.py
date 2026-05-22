"""
Discord 명령 봇 — 백그라운드 작업을 디스코드에서 제어
========================================================
디스코드 채널에서 보낸 메시지를 폴링하여 명령 큐에 저장.
phase2_orchestrator.py가 1분마다 큐를 읽어 명령 실행.

지원 명령 (디스코드에서 봇이 멘션된 메시지 또는 채널 메시지):
  !jb status              — 즉시 상태 카드 전송 (현재 단계/진행률/ETA/PID/RAM)
  !jb report              — 더 자세한 보고 (로그 마지막 50줄 + 디스크 + 메모리)
  !jb skip-task2          — TASK 2 전체 건너뛰고 마무리
  !jb skip-task2c         — 앙상블 학습만 건너뛰기 (eval은 진행)
  !jb stop                — 현재 단계 완료 후 우아하게 종료
  !jb force-stop          — 즉시 모든 프로세스 강제 종료
  !jb pause / !jb resume  — 일시정지/재개
  !jb help                — 명령 목록

설정 방법 (1회):
  1) https://discord.com/developers/applications 에서 Application 생성
  2) Bot 탭에서 Bot 생성, 토큰 복사
  3) Privileged Gateway Intents에서 MESSAGE CONTENT INTENT 활성화
  4) OAuth2 → URL Generator → bot 권한(Read Messages/Send Messages) → 서버 초대
  5) notify_config.json 에 다음 키 추가:
       "discord_bot_token": "MTM..."        (봇 토큰)
       "discord_channel_id": "1234567890"   (명령 받을 채널 ID, 채널 우클릭 → ID 복사)

실행:
  python discord_command_bot.py
  (또는 Start-Process로 백그라운드 독립 실행)

설치 필요:
  pip install discord.py
"""

import asyncio
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ML_DIR        = Path(__file__).parent
RESULT_DIR    = Path(r"D:\JB-Pirate-King-ML-Results")
COMMAND_QUEUE = RESULT_DIR / "discord_commands.jsonl"
CONFIG_FILE   = ML_DIR / "notify_config.json"

VALID_COMMANDS = {
    "status", "report",
    "skip-task2", "skip-task2c",
    "stop", "force-stop",
    "pause", "resume",
    "help",
    "disk", "ram", "ps",   # 즉시 응답 명령
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def queue_command(cmd: str, args: str = "", user: str = "?"):
    """명령을 큐 파일에 append. 오케스트레이터가 폴링."""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now().isoformat(),
        "cmd": cmd,
        "args": args,
        "user": user,
        "consumed": False,
    }
    with open(COMMAND_QUEUE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def quick_status() -> str:
    """현재 상태를 파일 읽기만으로 즉시 응답 (MCP 불필요)."""
    import shutil, subprocess
    lines = ["📊 **JB-Pirate-King 현재 상태**"]

    # 실행 중인 Python 프로세스 (psutil 사용)
    try:
        import psutil
        proc_lines = []
        for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
            try:
                name = p.info.get("name", "") or ""
                if "python" not in name.lower():
                    continue
                cmdline = " ".join(p.info.get("cmdline") or [])
                rss_mb = (p.info["memory_info"].rss // (1024*1024)) if p.info.get("memory_info") else 0
                for label, key in [
                    ("🔄 오케스트레이터", "phase2_orchestrator"),
                    ("🔄 스케일링비교",   "scaling_compare"),
                    ("🔄 5yr학습",        "scale_5yr"),
                    ("🔄 앙상블학습",     "ensemble_full"),
                    ("🔄 다운로드",       "download_ais"),
                    ("📊 eval",           "eval_all"),
                    ("🤖 Discord봇",      "discord_command_bot"),
                ]:
                    if key in cmdline:
                        proc_lines.append(f"  {label} PID={p.info['pid']} RAM={rss_mb}MB")
                        break
            except Exception:
                continue
        if proc_lines:
            lines.append("\n".join(proc_lines))
        else:
            lines.append("  실행 중인 작업 없음")
    except ImportError:
        lines.append("  (psutil 없음 — pip install psutil)")
    except Exception as e:
        lines.append(f"  프로세스 확인 오류: {e}")

    # phase2 로그 마지막 줄
    log_path = Path(r"D:\JB-Pirate-King-ML-Results\phase2_auto.log")
    if log_path.exists():
        try:
            log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            last = [l for l in log_lines if l.strip()][-3:]
            lines.append("📋 최근 로그:")
            for l in last:
                lines.append(f"  `{l[-80:]}`")
        except Exception:
            pass

    # scale_5yr 진행도
    s5yr = Path(r"D:\JB-Pirate-King-ML-Results\scale_5yr")
    if s5yr.exists():
        onnx_done = len(list(s5yr.glob("model_*.onnx")))
        scalers   = len(list(s5yr.glob("scaler_*.json")))
        lines.append(f"📦 scale_5yr: ONNX {onnx_done}/7, scaler {scalers}/7")

    # 메인 결과 ONNX
    main_dir = Path(r"D:\JB-Pirate-King-ML-Results")
    main_onnx = len(list(main_dir.glob("model_*.onnx")))
    lines.append(f"📦 v3 ONNX: {main_onnx}/7 {'✅' if main_onnx >= 7 else '🔄'}")

    # 디스크
    try:
        free = shutil.disk_usage("D:\\").free / (1024**3)
        lines.append(f"💾 D드라이브: {free:.1f}GB 여유")
    except Exception:
        pass

    return "\n".join(lines)


def quick_response(cmd: str) -> str:
    """봇이 즉시 답할 수 있는 간단 명령. None이면 큐에 위임."""
    import shutil
    if cmd == "status":
        return quick_status()
    if cmd == "report":
        # 간단 report도 즉시 처리
        base = quick_status()
        # 로그 추가 (마지막 15줄)
        log_path = Path(r"D:\JB-Pirate-King-ML-Results\phase2_auto.log")
        if log_path.exists():
            try:
                log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                last15 = [l for l in log_lines if l.strip()][-15:]
                extra = "\n📋 로그 (최근 15줄):\n```\n" + "\n".join(last15) + "\n```"
                return (base + extra)[:1900]  # Discord 2000자 제한
            except Exception:
                pass
        return base
    if cmd == "help":
        return (
            "**JB-Pirate-King 명령 목록**\n"
            "`!jb status`      현재 단계/진행률/ETA 카드\n"
            "`!jb report`      자세한 보고 (로그+자원)\n"
            "`!jb disk`        D드라이브 여유\n"
            "`!jb ram`         RAM 사용량\n"
            "`!jb ps`          실행 중인 Python 프로세스\n"
            "`!jb skip-task2`  TASK 2 건너뛰기\n"
            "`!jb skip-task2c` 앙상블 학습만 건너뛰기\n"
            "`!jb stop`        우아하게 종료 (현 단계 완료 후)\n"
            "`!jb force-stop`  즉시 강제 종료\n"
            "`!jb pause` / `!jb resume`  일시정지/재개"
        )
    if cmd == "disk":
        try:
            free = shutil.disk_usage("D:\\").free / (1024**3)
            total = shutil.disk_usage("D:\\").total / (1024**3)
            return f"💾 D드라이브: {free:.1f}GB 여유 / {total:.1f}GB"
        except Exception as e:
            return f"오류: {e}"
    if cmd == "ram":
        try:
            import psutil
            v = psutil.virtual_memory()
            return f"🧠 RAM: 사용 {v.used/(1024**3):.1f}GB / 총 {v.total/(1024**3):.1f}GB ({v.percent}%)"
        except Exception as e:
            return f"오류: {e}"
    if cmd == "ps":
        try:
            import psutil
            lines = ["🔍 Python 프로세스:"]
            for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
                if p.info["name"] and "python" in p.info["name"].lower():
                    cmd_str = " ".join(p.info.get("cmdline") or [])[:80]
                    rss_mb = p.info["memory_info"].rss / (1024**2)
                    lines.append(f"  PID {p.info['pid']:>5}  RAM {rss_mb:.0f}MB  {cmd_str}")
            return "\n".join(lines)
        except Exception as e:
            return f"오류: {e}"
    return None   # 큐에 위임


def parse_command(text: str):
    """!jb <cmd> [args] 파싱."""
    text = text.strip()
    if not text.startswith("!jb"):
        return None, None
    parts = text[3:].strip().split(maxsplit=1)
    if not parts:
        return None, None
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    if cmd not in VALID_COMMANDS:
        return None, None
    return cmd, args


async def run_bot():
    """discord.py 봇 실행."""
    try:
        import discord
    except ImportError:
        print("[오류] discord.py 없음. 설치: pip install discord.py")
        return

    cfg = load_config()
    token = cfg.get("discord_bot_token", "").strip()
    channel_id_str = str(cfg.get("discord_channel_id", "")).strip()

    if not token or token.startswith("YOUR_"):
        print(f"[오류] notify_config.json에 discord_bot_token 설정 필요")
        print(f"   파일: {CONFIG_FILE}")
        return
    if not channel_id_str.isdigit():
        print(f"[오류] notify_config.json에 discord_channel_id 설정 필요 (숫자)")
        return

    channel_id = int(channel_id_str)
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"[봇 준비완료] {client.user} | 채널 ID {channel_id}")
        ch = client.get_channel(channel_id)
        if ch:
            try:
                await ch.send("🤖 JB-Pirate-King 명령 봇 온라인! `!jb help` 입력")
            except Exception:
                pass

    @client.event
    async def on_message(msg):
        if msg.author.bot or msg.channel.id != channel_id:
            return
        cmd, args = parse_command(msg.content)
        if not cmd:
            return

        # 즉시 답변 가능한 명령
        quick = quick_response(cmd)
        if quick is not None:
            await msg.reply(quick)
            return

        # 그 외는 큐에 등록
        entry = queue_command(cmd, args, user=str(msg.author))
        await msg.reply(
            f"✅ 명령 큐 등록: `!jb {cmd}` "
            f"(오케스트레이터가 1분 내 처리)\n"
            f"by {entry['user']} @ {entry['time'][11:19]}"
        )
        print(f"[큐] {entry}")

    await client.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n봇 종료")
