"""
Slack 파이프라인 봇 — 로그 전송 + 버튼 승인 대기 + Claude 원격 프롬프트

채널에서 !<질문> 또는 ?<질문> 으로 Claude에게 질문 가능:
  예) !FE 결과 분석해줘
  예) ?현재 탐지율이 낮은 이유가 뭐야
"""
import subprocess
import sys
import threading
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


class SlackPipelineBot:
    def __init__(self, bot_token: str, app_token: str, channel: str):
        self.app = App(token=bot_token)
        self.app_token = app_token
        self.channel = channel
        self._decision = None
        self._event = threading.Event()
        self._active_token = None   # 현재 대기 중인 승인 메시지 토큰 (스테일 클릭 차단용)
        self._setup_handlers()
        self._start_socket()

    @staticmethod
    def _action_token(body) -> str:
        """클릭된 버튼의 value(토큰) 추출."""
        try:
            return body["actions"][0].get("value") or ""
        except (KeyError, IndexError, TypeError):
            return ""

    def _resolve(self, body, ack, label: str, decision: str):
        """버튼 클릭 처리. 활성 토큰과 일치할 때만 결정 반영, 옛 메시지는 무시."""
        ack()
        token = self._action_token(body)
        if self._active_token is None or token != self._active_token:
            # 스테일(이전 단계) 버튼 — 메시지만 만료 표시, 결정 무시
            self._update_buttons(body, "⏱ 만료된 버튼 (현재 단계 아님)")
            return
        self._update_buttons(body, label)
        self._active_token = None
        self._decision = decision
        self._event.set()

    def _setup_handlers(self):
        @self.app.action("approve")
        def handle_approve(ack, body):
            self._resolve(body, ack, "✅ 승인됨", "approve")

        @self.app.action("retry")
        def handle_retry(ack, body):
            self._resolve(body, ack, "🔄 재실행 요청", "retry")

        @self.app.action("stop")
        def handle_stop(ack, body):
            self._resolve(body, ack, "❌ 중단됨", "stop")

        @self.app.action("next_candidate")
        def handle_next_candidate(ack, body):
            self._resolve(body, ack, "▶ 다음 후보", "next")

        @self.app.action("stop_step")
        def handle_stop_step(ack, body):
            self._resolve(body, ack, "■ 스텝 종료", "stop_step")

        @self.app.event("message")
        def handle_message(event, say):
            text = (event.get("text") or "").strip()
            bot_id = event.get("bot_id")
            if bot_id:   # 봇 자신의 메시지 무시
                return
            if not (text.startswith("!") or text.startswith("?")):
                return
            prompt = text.lstrip("!?").strip()
            if not prompt:
                return
            # 컨텍스트 파일 수집 (최신 FE JSON 있으면 포함)
            context = self._collect_context()
            full_prompt = (
                "AIS 이상탐지 ML 파이프라인 운영 중 질문이 들어왔습니다.\n\n"
                f"=== 파이프라인 컨텍스트 ===\n{context}\n\n"
                f"=== 질문 ===\n{prompt}\n\n"
                "한국어로 간결하게 답변해주세요. (300자 이내)"
            )
            say(f"🤖 Claude 분석 중... (`{prompt[:40]}`)")
            try:
                result = subprocess.run(
                    ["claude", "-p", full_prompt, "--output-format", "text"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=120
                )
                answer = result.stdout.strip() if result.returncode == 0 else "(응답 실패)"
            except FileNotFoundError:
                answer = "Claude CLI를 찾을 수 없습니다. `claude` 명령어가 PATH에 있는지 확인하세요."
            except Exception as e:
                answer = f"오류: {e}"
            say(f"🤖 *Claude 답변*\n{answer}")

    def _collect_context(self) -> str:
        """최신 FE JSON + 파이프라인 상태를 컨텍스트 문자열로 반환"""
        lines = []
        for search_dir in ["D:/ais_output/feat_eng_iter", "C:/Users/imcas/JB-Pirate-King/ais_output/feat_eng_iter"]:
            p = Path(search_dir)
            if p.exists():
                jsons = sorted(p.glob("feat_eng_iter*.json"))
                if jsons:
                    with open(jsons[-1], encoding="utf-8") as f:
                        data = json.load(f)
                    lines.append(f"최신 FE 결과 ({jsons[-1].name}):")
                    lines.append(f"  탐지율: {data.get('best_det', '-')}")
                    lines.append(f"  채택 피처: {data.get('best_extra', [])}")
                    lines.append(f"  임계값: {data.get('threshold', '-')}")
                    break
        return "\n".join(lines) if lines else "FE 결과 파일 없음"

    def _update_buttons(self, body, result_text):
        self.app.client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=result_text,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{result_text}* — {datetime.now().strftime('%H:%M:%S')}"}
                }
            ]
        )

    def _start_socket(self):
        """Socket Mode 연결.

        handler.start() 대신 connect() 사용:
          - start()는 메인 스레드에서 signal 핸들러를 설치하고 영구 블록 →
            데몬 스레드에서 돌리려면 signal.signal 을 전역 no-op 으로 패치해야 했고,
            그 패치가 블록 동안 복구되지 않아 메인 프로세스의 Ctrl+C 처리를 망가뜨림.
          - connect()는 논블록으로 소켓만 열고 즉시 반환 (내부 백그라운드 스레드 유지).
            signal 패치 불필요.
        """
        self._handler = SocketModeHandler(self.app, self.app_token)
        self._handler.connect()  # 논블록, signal 미설치

        # 실제 WebSocket 연결 수립 확인 (최대 ~10초 폴링, sleep(3) 추측 제거)
        client = self._handler.client
        deadline = time.time() + 10
        while time.time() < deadline:
            if getattr(client, "is_connected", lambda: False)():
                print("[SlackBot] Socket Mode 연결 완료")
                return
            time.sleep(0.2)
        print("[SlackBot] ⚠ Socket Mode 연결 확인 실패 (계속 진행)")

    STAGE_EMOJI = {
        "전처리": "🔄", "학습": "🧠", "평가": "📊", "피처개선": "🔬",
        "info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"
    }

    def log(self, message: str, level: str = "info"):
        # 메시지가 이미 이모지/기호로 시작하면 level 이모지를 덧붙이지 않음 (이중 이모지 방지)
        first = message.lstrip()[:1]
        has_lead_emoji = bool(first) and ord(first) > 0x2000
        prefix = "" if has_lead_emoji else self.STAGE_EMOJI.get(level, "ℹ️") + " "
        self.app.client.chat_postMessage(
            channel=self.channel,
            text=f"{prefix}{message}"
        )

    def log_run_start(self, branch: str, params: dict):
        """파이프라인 시작 — 굵은 헤더로 구분"""
        lines = "\n".join(f">  • *{k}*: {v}" for k, v in params.items())
        self.app.client.chat_postMessage(
            channel=self.channel,
            text=f"🚀 파이프라인 시작: {branch}",
            blocks=[
                {"type": "divider"},
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🚀  {branch}  파이프라인 시작"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": lines}
                },
                {"type": "divider"},
            ]
        )

    def log_stage_start(self, stage: str, detail: str = ""):
        emoji = self.STAGE_EMOJI.get(stage, "▶️")
        self.app.client.chat_postMessage(
            channel=self.channel,
            text=f"{emoji} [{stage}] 시작",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{emoji}  *[{stage}] 시작*" + (f"\n> {detail}" if detail else "")}
                }
            ]
        )

    def log_stage_result(self, stage: str, lines: list[str], success: bool):
        emoji = self.STAGE_EMOJI.get(stage, "▶️")
        status = "✅ 완료" if success else "❌ 실패"
        body = "\n".join(f">  • {l}" for l in lines)
        self.app.client.chat_postMessage(
            channel=self.channel,
            text=f"{status} [{stage}]",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{emoji}  *[{stage}] {status}*\n{body}"}
                }
            ]
        )

    def log_table(self, title: str, rows: list[str], emoji: str = "📋"):
        """코드블록 테이블 전송 (긴 정보용)"""
        body = "\n".join(rows)
        # Slack 코드블록 3000자 제한 안전 처리
        if len(body) > 2800:
            body = body[:2800] + "\n... (생략)"
        self.app.client.chat_postMessage(
            channel=self.channel,
            text=title,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"{emoji} *{title}*\n```\n{body}\n```"}
            }]
        )

    # 승인 대기 최대 시간 (초). 초과 시 안전하게 'stop' (자동 배포 방지).
    APPROVAL_TIMEOUT = 3600

    def wait_approval(self, stage: str, summary_lines: list[str]) -> str:
        """승인 버튼 메시지 전송 후 클릭 대기. 반환값: 'approve' | 'retry' | 'stop'.
        APPROVAL_TIMEOUT 초 내 응답 없으면 'stop' 반환 (영구 행 방지)."""
        self._decision = None
        self._event.clear()
        token = uuid.uuid4().hex
        self._active_token = token

        body = "\n".join(f">  • {l}" for l in summary_lines)
        self.app.client.chat_postMessage(
            channel=self.channel,
            text=f"[{stage}] 승인 대기 중",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*[{stage}] — 다음 단계를 선택하세요*\n{body}"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ 다음 단계"},
                            "style": "primary",
                            "action_id": "approve",
                            "value": token
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🔄 다시 실행"},
                            "action_id": "retry",
                            "value": token
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ 중단"},
                            "style": "danger",
                            "action_id": "stop",
                            "value": token
                        }
                    ]
                },
                {"type": "divider"},
            ]
        )

        if not self._event.wait(timeout=self.APPROVAL_TIMEOUT):
            self.log(f"⏱ [{stage}] 승인 타임아웃 ({self.APPROVAL_TIMEOUT}s) → stop", "warning")
            return "stop"
        return self._decision


    def wait_candidate_approval(self, candidate_num: int, feat: str, desc: str,
                                det: float, det_gain: float,
                                obj_score: float, obj_gain: float,
                                baseline_det: float) -> str:
        """후보 1개 평가 결과 표시 + 다음 후보 진행 여부 승인.
        반환: 'next' | 'stop_step'
        """
        self._decision = None
        self._event.clear()
        token = uuid.uuid4().hex
        self._active_token = token

        arrow = "▲" if obj_gain > 0 else ("▼" if obj_gain < 0 else "─")
        status_emoji = "✅" if obj_gain >= 3.0 else ("⚠️" if obj_gain > 0 else "❌")
        baseline_obj = obj_score - obj_gain
        self.app.client.chat_postMessage(
            channel=self.channel,
            text=f"후보 #{candidate_num}: {feat}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"🔬 *후보 #{candidate_num}: `{feat}`* — {desc}\n"
                            f">  FP=1% 탐지율: 베이스라인 *{baseline_det:.2f}%* → *{det:.2f}%* ({det_gain:+.2f}pp)\n"
                            f">  목적점수: 베이스라인 *{baseline_obj:.2f}* → *{obj_score:.2f}* ({arrow}{obj_gain:+.2f})  "
                            f"{status_emoji} {'채택 가능 (≥+3.0)' if obj_gain >= 3.0 else '채택 기준 미달 (<+3.0)'}"
                        )
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "▶ 다음 후보"},
                            "style": "primary",
                            "action_id": "next_candidate",
                            "value": token
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "■ 스텝 종료"},
                            "style": "danger",
                            "action_id": "stop_step",
                            "value": token
                        }
                    ]
                }
            ]
        )
        if not self._event.wait(timeout=self.APPROVAL_TIMEOUT):
            self.log(f"⏱ 후보 #{candidate_num} 승인 타임아웃 → stop_step", "warning")
            return "stop_step"
        return self._decision


def from_config(config_path: str = "ml/pipeline_config.json") -> SlackPipelineBot:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    return SlackPipelineBot(
        bot_token=cfg["slack"]["bot_token"],
        app_token=cfg["slack"]["app_token"],
        channel=cfg["slack"]["channel"]
    )
