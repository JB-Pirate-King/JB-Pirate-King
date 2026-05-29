"""
automation/notify.py

Slack + Discord 알림 유틸리티.
성공/실패/결과 요약 메시지를 각 채널에 전송한다.

사용법:
    from automation.notify import Notifier
    n = Notifier()
    n.training_complete("conv1d", dr=68.3, holdout=70.8, fp=1.0, elapsed_min=42)
    n.pipeline_failed("conv1d", "OOM at epoch 5")
"""

import json
import requests
from datetime import datetime
from automation.config import (
    SLACK_BOT_TOKEN,
    SLACK_CHANNEL,
    DISCORD_WEBHOOK_URL,
)

# Discord Docker MCP 로컬 엔드포인트 (port 8085)
# 웹훅 URL이 없을 때 fallback으로 사용
DISCORD_LOCAL_MCP_URL = "http://localhost:8085/send"


# ─── Slack ────────────────────────────────────────────────────────────────────

def _slack_post(text: str, blocks: list | None = None) -> bool:
    if not SLACK_BOT_TOKEN:
        print("[notify] SLACK_BOT_TOKEN 미설정 — Slack 알림 스킵")
        return False

    payload: dict = {"channel": SLACK_CHANNEL, "text": text}
    if blocks:
        payload["blocks"] = blocks

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                 "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=10,
    )
    ok = resp.json().get("ok", False)
    if not ok:
        print(f"[notify] Slack 오류: {resp.json().get('error')}")
    return ok


# ─── Discord ─────────────────────────────────────────────────────────────────
# 우선순위: 1) 표준 웹훅 URL  2) Docker MCP (localhost:8085)

def _discord_post(content: str, embeds: list | None = None) -> bool:
    payload: dict = {"content": content}
    if embeds:
        payload["embeds"] = embeds

    # 1) 표준 Discord 웹훅
    if DISCORD_WEBHOOK_URL:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            return True
        print(f"[notify] Discord 웹훅 오류: {resp.status_code} — Docker MCP 시도")

    # 2) Docker MCP fallback (localhost:8085)
    try:
        resp = requests.post(
            DISCORD_LOCAL_MCP_URL,
            json={"message": content},
            timeout=5,
        )
        if resp.status_code in (200, 204):
            return True
        print(f"[notify] Discord Docker MCP 오류: {resp.status_code} {resp.text}")
        return False
    except requests.ConnectionError:
        print("[notify] Discord 웹훅/Docker MCP 모두 미설정 또는 미실행 — 스킵")
        return False


# ─── Public API ───────────────────────────────────────────────────────────────

class Notifier:
    def training_complete(
        self,
        model: str,
        dr: float,
        holdout: float,
        fp: float,
        elapsed_min: float,
        version: str = "",
    ):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        label = f"v{version} " if version else ""

        # Slack blocks
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"✅ [{model}] 학습 완료 {label}"}},
            {"type": "section",
             "fields": [
                 {"type": "mrkdwn", "text": f"*탐지율 (학습)*\n{dr:.1f}%"},
                 {"type": "mrkdwn", "text": f"*탐지율 (홀드아웃)*\n{holdout:.1f}%"},
                 {"type": "mrkdwn", "text": f"*오탐율*\n{fp:.1f}%"},
                 {"type": "mrkdwn", "text": f"*소요 시간*\n{elapsed_min:.0f}분"},
             ]},
            {"type": "context",
             "elements": [{"type": "mrkdwn", "text": f"완료 시각: {ts}"}]},
        ]
        _slack_post(f"[AIS] {model} 학습 완료 — DR {dr:.1f}% / Holdout {holdout:.1f}%", blocks)

        # Discord embed
        embeds = [{
            "title": f"✅ [{model}] 학습 완료 {label}",
            "color": 0x2ECC71,
            "fields": [
                {"name": "탐지율 (학습)", "value": f"{dr:.1f}%", "inline": True},
                {"name": "탐지율 (홀드아웃)", "value": f"{holdout:.1f}%", "inline": True},
                {"name": "오탐율", "value": f"{fp:.1f}%", "inline": True},
                {"name": "소요 시간", "value": f"{elapsed_min:.0f}분", "inline": True},
            ],
            "footer": {"text": ts},
        }]
        _discord_post(f"AIS 학습 완료: **{model}**", embeds)

    def pipeline_failed(self, model: str, reason: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        _slack_post(
            f"❌ [{model}] 파이프라인 실패",
            blocks=[
                {"type": "header",
                 "text": {"type": "plain_text", "text": f"❌ [{model}] 파이프라인 실패"}},
                {"type": "section",
                 "text": {"type": "mrkdwn", "text": f"*오류 내용*\n```{reason}```"}},
                {"type": "context",
                 "elements": [{"type": "mrkdwn", "text": ts}]},
            ],
        )
        _discord_post(
            f"AIS 파이프라인 실패: **{model}**",
            embeds=[{
                "title": f"❌ [{model}] 파이프라인 실패",
                "color": 0xE74C3C,
                "description": f"```{reason[:1000]}```",
                "footer": {"text": ts},
            }],
        )

    def release_created(self, tag: str, url: str, models: list[str]):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        model_list = ", ".join(models)

        _slack_post(
            f"🚀 릴리즈 {tag} 생성됨",
            blocks=[
                {"type": "header",
                 "text": {"type": "plain_text", "text": f"🚀 GitHub 릴리즈 {tag}"}},
                {"type": "section",
                 "fields": [
                     {"type": "mrkdwn", "text": f"*모델*\n{model_list}"},
                     {"type": "mrkdwn", "text": f"*URL*\n<{url}|릴리즈 보기>"},
                 ]},
                {"type": "context",
                 "elements": [{"type": "mrkdwn", "text": ts}]},
            ],
        )
        _discord_post(
            f"🚀 AIS 릴리즈 **{tag}** 생성됨 — {model_list}\n{url}",
        )

    def data_updated(self, file_count: int, coverage: str):
        msg = f"📦 AIS 데이터 업데이트: {file_count}개 파일, 커버리지 {coverage}"
        _slack_post(msg)
        _discord_post(msg)
