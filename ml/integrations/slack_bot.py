"""
Slack 파이프라인 봇 — 로그 전송 + 버튼 승인 대기
"""
import sys
import threading
import json
import time
import uuid
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


class SlackPipelineBot:
    def __init__(self, bot_token: str, app_token: str, channel: str):
        self.app = App(token=bot_token)
        self.app_token = app_token
        self.channel = channel
        self.log_file = None     # 지정 시 모든 Slack 메시지 text를 파일에도 기록 (orchestrator가 세팅)
        self.current_branch = "" # 파일 로그 라인 접두사용 현재 브랜치 (orchestrator가 갱신)
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

    def _post(self, **kwargs):
        """chat_postMessage 래퍼 — log_file 지정 시 text 를 타임스탬프와 함께 파일에도 기록.
        Slack 전용이던 서술 로그(판정 verdict·지식요약·스테이지 결과)가 영구 파일로 남는다."""
        if self.log_file:
            try:
                br = f"[{self.current_branch}]" if self.current_branch else ""
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}]{br} {kwargs.get('text', '')}\n")
            except Exception:
                pass   # 파일 로깅 실패가 Slack 전송을 막지 않게
        return self.app.client.chat_postMessage(**kwargs)

    STAGE_EMOJI = {
        "전처리": "🔄", "학습": "🧠", "평가": "📊", "피처개선": "🔬",
        "info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"
    }

    # ── Block Kit 헬퍼 (메시지를 표/그리드로 보기 좋게) ──────────────
    @staticmethod
    def _fields(pairs):
        """dict 또는 (k,v) 리스트 → section fields (2열 그리드, 최대 10쌍).
        텍스트 줄나열보다 키-값이 칸으로 정렬돼 한눈에 들어온다."""
        items = list(pairs.items() if isinstance(pairs, dict) else pairs)
        fields = [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in items[:10]]
        return {"type": "section", "fields": fields}

    @staticmethod
    def _context(text):
        """하단 회색 소형 메타 줄 (타임스탬프/행수 등)."""
        return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}

    @staticmethod
    def _now(fmt="%H:%M:%S"):
        return datetime.now().strftime(fmt)

    def log_metrics(self, title: str, pairs, emoji: str = "📈", success: bool = True):
        """지표 묶음을 2열 그리드로 — 탐지율/임계값 등 요약에 사용.
        pairs: dict 또는 (라벨, 값) 리스트."""
        head = {"type": "section",
                "text": {"type": "mrkdwn", "text": f"{emoji} *{title}*"}}
        self._post(
            channel=self.channel, text=title,
            blocks=[head, self._fields(pairs), self._context(f"🕐 {self._now()}")],
        )

    def log(self, message: str, level: str = "info"):
        # 메시지가 이미 이모지/기호로 시작하면 level 이모지를 덧붙이지 않음 (이중 이모지 방지)
        first = message.lstrip()[:1]
        has_lead_emoji = bool(first) and ord(first) > 0x2000
        prefix = "" if has_lead_emoji else self.STAGE_EMOJI.get(level, "ℹ️") + " "
        self._post(
            channel=self.channel,
            text=f"{prefix}{message}"
        )

    def log_run_start(self, branch: str, params: dict):
        """파이프라인 시작 — 헤더 + 파라미터 2열 그리드"""
        self._post(
            channel=self.channel,
            text=f"🚀 파이프라인 시작: {branch}",
            blocks=[
                {"type": "divider"},
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🚀  {branch}  파이프라인 시작"}
                },
                self._fields(params),
                self._context(f"🕐 {self._now('%Y-%m-%d %H:%M:%S')}"),
                {"type": "divider"},
            ]
        )

    def log_stage_start(self, stage: str, detail: str = ""):
        """스테이지 시작 — 구분선 + 헤더로 강하게 끊어 묶음 경계를 만든다."""
        emoji = self.STAGE_EMOJI.get(stage, "▶️")
        blocks = [
            {"type": "divider"},
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji}  {stage}"}},
        ]
        if detail:
            blocks.append(self._context(detail))
        self._post(
            channel=self.channel, text=f"{emoji} [{stage}] 시작", blocks=blocks,
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
                },
                self._context(f"🕐 {self._now()}"),
            ]
        )

    def log_table(self, title: str, rows: list[str], emoji: str = "📋"):
        """코드블록 테이블 전송 (긴 정보용)"""
        body = "\n".join(rows)
        # Slack 코드블록 3000자 제한 안전 처리
        truncated = len(body) > 2800
        if truncated:
            body = body[:2800] + "\n... (생략)"
        self._post(
            channel=self.channel,
            text=title,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn",
                             "text": f"{emoji} *{title}*\n```\n{body}\n```"}
                },
                self._context(f"📑 {len(rows)}행" + (" · 일부 생략" if truncated else "")
                              + f"  ·  🕐 {self._now()}"),
            ]
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


def from_config(config_path: str = "ml/config/pipeline_config.json") -> SlackPipelineBot:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    return SlackPipelineBot(
        bot_token=cfg["slack"]["bot_token"],
        app_token=cfg["slack"]["app_token"],
        channel=cfg["slack"]["channel"]
    )
