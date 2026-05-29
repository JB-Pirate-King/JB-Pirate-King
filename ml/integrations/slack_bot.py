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
        self._setup_handlers()
        self._start_socket()

    def _setup_handlers(self):
        @self.app.action("approve")
        def handle_approve(ack, body):
            ack()
            self._update_buttons(body, "✅ 승인됨")
            self._decision = "approve"
            self._event.set()

        @self.app.action("retry")
        def handle_retry(ack, body):
            ack()
            self._update_buttons(body, "🔄 재실행 요청")
            self._decision = "retry"
            self._event.set()

        @self.app.action("stop")
        def handle_stop(ack, body):
            ack()
            self._update_buttons(body, "❌ 중단됨")
            self._decision = "stop"
            self._event.set()

        @self.app.action("next_candidate")
        def handle_next_candidate(ack, body):
            ack()
            self._update_buttons(body, "▶ 다음 후보")
            self._decision = "next"
            self._event.set()

        @self.app.action("stop_step")
        def handle_stop_step(ack, body):
            ack()
            self._update_buttons(body, "■ 스텝 종료")
            self._decision = "stop_step"
            self._event.set()

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
        # Socket Mode 는 버튼 승인/원격 질의(!?)에만 필요.
        # app_token(xapp-)이 없으면 메시지 전송(chat_postMessage)만으로 동작.
        # --auto_approve 모드에서는 승인 대기가 없으므로 socket 없이 완전 동작.
        self._socket_ok = False
        tok = (self.app_token or "").strip()
        if not tok or not tok.startswith("xapp-") or "REPLACE" in tok:
            print("[SlackBot] app_token(xapp-) 없음 → Socket Mode 비활성화. "
                  "메시지 전송만 동작(버튼 승인 불가). --auto_approve 사용 권장.")
            return

        import signal as _signal
        try:
            self._handler = SocketModeHandler(self.app, self.app_token)
            self._connected = threading.Event()

            def _run():
                orig = _signal.signal
                _signal.signal = lambda *a, **kw: None
                try:
                    self._handler.start()
                finally:
                    _signal.signal = orig

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            time.sleep(3)  # 소켓 연결 대기
            self._socket_ok = True
            print("[SlackBot] Socket Mode 연결 완료")
        except Exception as e:
            print(f"[SlackBot] Socket Mode 연결 실패 → 메시지 전송만 동작: {e}")

    def _post(self, **kwargs):
        """chat_postMessage 안전 래퍼 — Slack 전송 실패가 파이프라인을 막지 않음.
        (예: 봇이 채널에 미초대(not_in_channel), 네트워크 오류 등)"""
        kwargs.setdefault("channel", self.channel)
        try:
            return self.app.client.chat_postMessage(**kwargs)
        except Exception as e:
            print(f"[SlackBot] 전송 실패(무시): {e}")
            return None

    STAGE_EMOJI = {
        "전처리": "🔄", "학습": "🧠", "평가": "📊", "피처개선": "🔬",
        "info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"
    }

    def log(self, message: str, level: str = "info"):
        emoji = self.STAGE_EMOJI.get(level, "ℹ️")
        self._post(
            channel=self.channel,
            text=f"{emoji} {message}"
        )

    def log_run_start(self, branch: str, params: dict):
        """파이프라인 시작 — 굵은 헤더로 구분"""
        lines = "\n".join(f">  • *{k}*: {v}" for k, v in params.items())
        self._post(
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
        self._post(
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
        self._post(
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
        self._post(
            channel=self.channel,
            text=title,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"{emoji} *{title}*\n```\n{body}\n```"}
            }]
        )

    def wait_approval(self, stage: str, summary_lines: list[str]) -> str:
        """승인 버튼 메시지 전송 후 클릭 대기. 반환값: 'approve' | 'retry' | 'stop'"""
        if not getattr(self, "_socket_ok", False):
            # Socket Mode 미연결 → 버튼 클릭을 받을 수 없음. 무한 대기 방지를 위해
            # 결과만 게시하고 자동 승인 처리. (대화형 승인을 원하면 app_token 설정)
            self.log(f"[{stage}] Socket Mode 미연결 → 자동 진행(approve). "
                     f"대화형 승인하려면 app_token(xapp-) 설정 필요.", "warning")
            return "approve"
        self._decision = None
        self._event.clear()

        body = "\n".join(f">  • {l}" for l in summary_lines)
        self._post(
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
                            "action_id": "approve"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🔄 다시 실행"},
                            "action_id": "retry"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ 중단"},
                            "style": "danger",
                            "action_id": "stop"
                        }
                    ]
                },
                {"type": "divider"},
            ]
        )

        self._event.wait()
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

        arrow = "▲" if obj_gain > 0 else ("▼" if obj_gain < 0 else "─")
        status_emoji = "✅" if obj_gain >= 3.0 else ("⚠️" if obj_gain > 0 else "❌")
        baseline_obj = obj_score - obj_gain
        self._post(
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
                            "action_id": "next_candidate"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "■ 스텝 종료"},
                            "style": "danger",
                            "action_id": "stop_step"
                        }
                    ]
                }
            ]
        )
        self._event.wait()
        return self._decision


def from_config(config_path: str = "ml/pipeline_config.json") -> SlackPipelineBot:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    return SlackPipelineBot(
        bot_token=cfg["slack"]["bot_token"],
        app_token=cfg["slack"]["app_token"],
        channel=cfg["slack"]["channel"]
    )
