#!/usr/bin/env python3
# coding: utf-8
"""
notify.py
─────────
Discord 웹훅 + Notion API 알림/보고서 전송 모듈.

설정: ml/notify_config.json (gitignore 처리됨)
  {
    "discord_webhook": "https://discord.com/api/webhooks/...",
    "notion_token": "ntn_...",
    "notion_parent_page_id": "",         # 비우면 통합에 공유된 첫 페이지 자동 탐색
    "notion_version": "2022-06-28"
  }

사용 예:
  from notify import notify_iteration
  notify_iteration(
      title="피처 엔지니어링 Iteration 2",
      summary="채택: accel, hdg_vec_diff | 홀드아웃 75.1% (+1.4pp)",
      report_text="<전체 비교표 텍스트>",
  )

단독 테스트:
  python notify.py --test
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

# Windows 콘솔(cp949)에서 유니코드 출력 보장
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Cloudflare(Discord)는 User-Agent 없는 요청을 1010 코드로 차단
_UA = "Mozilla/5.0 (compatible; JB-Pirate-King-notify/1.0)"

_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify_config.json")


# ── 설정 로드 ──────────────────────────────────────────────────────
def load_config() -> dict:
    if not os.path.exists(_CFG_PATH):
        return {}
    try:
        with open(_CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[notify] 설정 로드 실패: {e}")
        return {}


def _http_json(url: str, payload: dict, headers: dict, method: str = "POST") -> tuple:
    """JSON POST/PATCH 요청. (status_code, response_dict|None) 반환."""
    data = json.dumps(payload).encode("utf-8")
    headers = {**headers, "User-Agent": _UA}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[notify] HTTP {e.code}: {body[:300]}")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, None
    except Exception as e:
        print(f"[notify] 요청 실패: {e}")
        return 0, None


# ── Discord ────────────────────────────────────────────────────────
def send_discord(content: str = "", embeds: Optional[list] = None,
                 cfg: Optional[dict] = None) -> bool:
    """Discord 웹훅으로 메시지 전송. 성공 시 True."""
    cfg = cfg or load_config()
    url = cfg.get("discord_webhook", "").strip()
    if not url:
        print("[notify] discord_webhook 미설정 — 건너뜀")
        return False

    # Discord content 길이 제한 2000자
    if content and len(content) > 1900:
        content = content[:1900] + "\n…(생략)"

    payload: dict = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    status, _ = _http_json(url, payload,
                           {"Content-Type": "application/json"})
    ok = status in (200, 204)
    print(f"[notify] Discord 전송 {'성공' if ok else '실패'} (HTTP {status})")
    return ok


# ── Slack ─────────────────────────────────────────────────────────
def send_slack(text: str, cfg: Optional[dict] = None) -> bool:
    """Slack Bot Token으로 메시지 전송. 성공 시 True.

    설정 키: notify_config.json 의 slack_bot_token, slack_channel
    (채널은 이름 권장 — e.g. "ais-training-alerts")
    """
    cfg = cfg or load_config()
    token   = cfg.get("slack_bot_token", "").strip()
    channel = cfg.get("slack_channel", "").strip()
    if not token or not channel:
        print("[notify] slack_bot_token / slack_channel 미설정 — 건너뜀")
        return False

    # Slack text 제한 3000자
    if len(text) > 2900:
        text = text[:2900] + "\n…(생략)"

    status, resp = _http_json(
        "https://slack.com/api/chat.postMessage",
        {"channel": channel, "text": text},
        {"Content-Type": "application/json",
         "Authorization": f"Bearer {token}"},
    )
    ok = status == 200 and bool(resp and resp.get("ok"))
    if not ok:
        err = resp.get("error", "") if resp else ""
        print(f"[notify] Slack 전송 실패 (HTTP {status}, {err})")
    else:
        print(f"[notify] Slack 전송 성공")
    return ok


# ── 파이프라인 통합 알림 ────────────────────────────────────────────
def notify_pipeline(event: str, detail: str = "", cfg: Optional[dict] = None) -> dict:
    """ML 파이프라인 이벤트를 Discord + Slack 동시 전송.

    event 예시: "🚀 run 시작", "✅ 피처 채택", "🏁 수렴 완료", "🎉 릴리즈 생성"
    detail: 핵심 수치 1-2줄 (탐지율, 피처 수 등)

    반환: {"discord": bool, "slack": bool}
    """
    cfg = cfg or load_config()
    text = f"*[AIS-IDS]* {event}"
    if detail:
        text += f"\n{detail}"

    d_ok = send_discord(content=text, cfg=cfg)
    s_ok = send_slack(text=text, cfg=cfg)
    return {"discord": d_ok, "slack": s_ok}


def discord_embed(title: str, summary: str, fields: Optional[list] = None,
                  color: int = 0x2ECC71) -> dict:
    """Discord 임베드 카드 생성 헬퍼."""
    embed = {
        "title": title[:256],
        "description": summary[:4000],
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "JB-Pirate-King · AIS 이상탐지"},
    }
    if fields:
        embed["fields"] = fields[:25]
    return embed


# ── Notion ─────────────────────────────────────────────────────────
def _notion_headers(cfg: dict) -> dict:
    return {
        "Authorization": f"Bearer {cfg.get('notion_token', '').strip()}",
        "Notion-Version": cfg.get("notion_version", "2022-06-28"),
        "Content-Type": "application/json",
    }


def notion_find_parent(cfg: Optional[dict] = None) -> Optional[str]:
    """통합에 공유된 첫 페이지 ID를 탐색. 설정에 지정돼 있으면 그것을 우선."""
    cfg = cfg or load_config()
    pid = cfg.get("notion_parent_page_id", "").strip()
    if pid:
        return pid
    if not cfg.get("notion_token", "").strip():
        return None
    # search API로 접근 가능한 페이지 탐색
    payload = {"filter": {"value": "page", "property": "object"},
               "page_size": 5}
    status, resp = _http_json("https://api.notion.com/v1/search", payload,
                              _notion_headers(cfg))
    if status == 200 and resp and resp.get("results"):
        return resp["results"][0]["id"]
    return None


def _chunk_text(text: str, size: int = 1900) -> list:
    """Notion rich_text 블록당 2000자 제한 → 청크 분할."""
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


# ── Notion 블록 헬퍼 ────────────────────────────────────────────────
def _nb_txt(s: str, bold: bool = False, color: Optional[str] = None,
            code: bool = False) -> dict:
    ann: dict = {}
    if bold:  ann["bold"]  = True
    if color: ann["color"] = color
    if code:  ann["code"]  = True
    t: dict = {"type": "text", "text": {"content": str(s)[:2000]}}
    if ann: t["annotations"] = ann
    return t

def _nb_h2(s: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [_nb_txt(s)]}}

def _nb_h3(s: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [_nb_txt(s)]}}

def _nb_para(*parts) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": list(parts)}}

def _nb_bullet(*parts) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": list(parts)}}

def _nb_callout(s: str, emoji: str = "📊") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"icon": {"type": "emoji", "emoji": emoji},
                        "rich_text": [_nb_txt(s[:1900])]}}

def _nb_divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}

def _nb_code(s: str, lang: str = "json") -> dict:
    return {"object": "block", "type": "code",
            "code": {"language": lang,
                     "rich_text": [_nb_txt(s[:1900])]}}

def _nb_table(header: list, rows: list) -> dict:
    def trow(cells):
        return {"object": "block", "type": "table_row",
                "table_row": {"cells": [[_nb_txt(str(c))] for c in cells]}}
    return {"object": "block", "type": "table",
            "table": {"table_width": len(header),
                      "has_column_header": True,
                      "has_row_header": False,
                      "children": [trow(header)] + [trow(r) for r in rows]}}


def send_notion_iter_report(
    title: str,
    iter_num: int,
    baseline_det: float,
    best_det: float,
    initial_extra: list,
    best_extra: list,
    newly_adopted: list,
    history: list,
    permutation_importance: list,
    feature_descriptions: Optional[dict] = None,
    json_path: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> bool:
    """피처 엔지니어링 결과를 구조화된 Notion 페이지로 생성."""
    cfg = cfg or load_config()
    if not cfg.get("notion_token", "").strip():
        print("[notify] notion_token 미설정 — 건너뜀")
        return False

    results_page = cfg.get("notion_results_page_id", "").strip()
    parent = results_page or notion_find_parent(cfg)
    if not parent:
        print("[notify] Notion 부모 페이지 없음 — 건너뜀")
        return False

    intra_gain = best_det - baseline_det
    emoji_icon = "✅" if newly_adopted else "➡️"
    removed    = [f for f in initial_extra if f not in best_extra]
    feat_desc  = feature_descriptions or {}

    # ── 요약 콜아웃 ─────────────────────────────────────────────────
    summary_txt = (
        f"탐지율: {baseline_det:.1f}% → {best_det:.1f}%  ({intra_gain:+.1f}pp)\n"
        f"채택 피처: {best_extra}\n"
        f"신규 채택: {newly_adopted or '없음 (수렴)'}"
    )

    # ── Greedy 히스토리 테이블 ───────────────────────────────────────
    hist_rows = []
    for h in history:
        candidate = h.get("candidate", h.get("added", "baseline"))
        det   = h.get("det", 0.0)
        score = h.get("score", 0.0)
        adopted = "✅" if h.get("adopted") else ("베이스라인" if candidate in ("baseline", "베이스라인") else "❌")
        hist_rows.append([candidate, f"{det:.1f}%", f"{score:.3f}", adopted])

    # ── 순열 중요도 테이블 ───────────────────────────────────────────
    perm_rows = [
        [p.get("feature", ""), f"{p.get('importance', 0):+.2f}pp",
         "█" * max(0, int(abs(p.get("importance", 0)) / 1.5))]
        for p in (permutation_importance or [])
    ]

    # ── 피처 계산법 상세 테이블 ─────────────────────────────────────
    all_feats = list(dict.fromkeys(initial_extra + best_extra))
    feat_formula_rows = []
    for f in all_feats:
        tag = "🆕 신규" if f in newly_adopted else ("◆ 기채택" if f in initial_extra else "✅ 채택")
        desc = feat_desc.get(f, "—")
        feat_formula_rows.append([tag, f, desc])

    children: list = [
        _nb_callout(summary_txt, emoji_icon),
        _nb_divider(),

        _nb_h2("📈 탐지율"),
        _nb_table(
            ["구분", "탐지율", "변화"],
            [
                ["베이스라인 (iter 시작)", f"{baseline_det:.1f}%", "—"],
                ["최종 (Greedy 완료)",    f"{best_det:.1f}%",     f"{intra_gain:+.1f}pp"],
            ]
        ),
        _nb_divider(),

        _nb_h2("✨ 채택 피처 변화"),
        _nb_table(
            ["구분", "피처 목록"],
            [
                ["이전 (initial_extra)", ", ".join(initial_extra) or "없음"],
                ["이번 채택 (best_extra)", ", ".join(best_extra) or "없음"],
                ["신규 채택",             ", ".join(newly_adopted) or "없음"],
                ["제거",                  ", ".join(removed) or "없음"],
            ]
        ),
        _nb_divider(),

        _nb_h2("🧮 채택 피처 계산법"),
        _nb_table(
            ["구분", "피처명", "계산 설명"],
            feat_formula_rows or [["—", "—", "—"]]
        ),
        _nb_divider(),

        _nb_h2("🔄 Greedy Forward Selection 히스토리"),
        _nb_table(
            ["후보 피처", "탐지율", "목적 점수", "채택"],
            hist_rows or [["(데이터 없음)", "", "", ""]]
        ),
    ]

    if perm_rows:
        children += [
            _nb_divider(),
            _nb_h2("🔬 순열 중요도 (Permutation Importance)"),
            _nb_para(_nb_txt("피처를 무작위 셔플했을 때 탐지율 하락량. 클수록 모델이 해당 피처에 의존.")),
            _nb_table(["피처", "중요도 (탐지율 하락)", "시각화"], perm_rows),
        ]

    # ── 결과 JSON 파일 전문 업로드 ───────────────────────────────────
    if json_path and __import__("os").path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as jf:
                json_content = jf.read()
            children += [_nb_divider(), _nb_h2("📄 결과 JSON (전문)")]
            for chunk in _chunk_text(json_content, size=1900):
                children.append(_nb_code(chunk, lang="json"))
        except Exception as e:
            print(f"[notify] JSON 읽기 실패: {e}")

    payload = {
        "parent": {"page_id": parent},
        "icon":   {"type": "emoji", "emoji": "📊"},
        "properties": {
            "title": {"title": [{"type": "text",
                                 "text": {"content": title[:200]}}]},
        },
        "children": children[:100],
    }
    status, resp = _http_json("https://api.notion.com/v1/pages", payload,
                              _notion_headers(cfg))
    ok = status == 200
    print(f"[notify] Notion 보고서 {'생성' if ok else '실패'} (HTTP {status})")

    # 블록 100개 초과 시 나머지 append
    if ok and len(children) > 100:
        page_id = resp.get("id") if resp else None
        if page_id:
            for i in range(100, len(children), 100):
                _http_json(
                    f"https://api.notion.com/v1/blocks/{page_id}/children",
                    {"children": children[i:i + 100]},
                    _notion_headers(cfg),
                )
    return ok


def send_notion_report(title: str, summary: str, report_text: str = "",
                       cfg: Optional[dict] = None) -> bool:
    """Notion 부모 페이지 아래에 보고서 하위 페이지 생성 (범용). 성공 시 True."""
    cfg = cfg or load_config()
    if not cfg.get("notion_token", "").strip():
        print("[notify] notion_token 미설정 — 건너뜀")
        return False

    parent = notion_find_parent(cfg)
    if not parent:
        print("[notify] Notion 부모 페이지 없음 — 건너뜀")
        return False

    children: list = [_nb_callout(summary[:1900])]
    for chunk in _chunk_text(report_text):
        children.append(_nb_code(chunk, lang="plain text"))

    payload = {
        "parent": {"page_id": parent},
        "properties": {
            "title": {"title": [{"type": "text",
                                 "text": {"content": title[:200]}}]},
        },
        "children": children[:100],
    }
    status, _ = _http_json("https://api.notion.com/v1/pages", payload,
                           _notion_headers(cfg))
    ok = status == 200
    print(f"[notify] Notion 보고서 {'생성' if ok else '실패'} (HTTP {status})")
    return ok


# ── 통합 진입점 ────────────────────────────────────────────────────
def notify_iteration(title: str, summary: str, report_text: str = "",
                     color: int = 0x2ECC71) -> dict:
    """Discord + Notion 동시 전송. 결과 dict 반환."""
    cfg = load_config()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    full_title = f"{title} · {ts}"

    # Discord: 임베드 카드 + 보고서는 코드블록 첨부(길면 잘림)
    embed = discord_embed(full_title, summary, color=color)
    d_ok = send_discord(embeds=[embed], cfg=cfg)
    if report_text:
        # 보고서 본문은 별도 코드블록 메시지로 (1900자 제한)
        send_discord(content=f"```\n{report_text[:1850]}\n```", cfg=cfg)

    n_ok = send_notion_report(full_title, summary, report_text, cfg=cfg)
    return {"discord": d_ok, "notion": n_ok}


# ── CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="알림 전송 테스트")
    p.add_argument("--test", action="store_true", help="테스트 메시지 전송")
    p.add_argument("--discord", action="store_true", help="Discord만 테스트")
    p.add_argument("--notion", action="store_true", help="Notion만 테스트")
    args = p.parse_args()

    if args.discord or args.test:
        send_discord(content="**[notify.py]** Discord 단독 테스트 메시지 ✅")
    if args.notion or args.test:
        send_notion_report(
            "notify.py 테스트 보고서",
            "Notion 연동 테스트 — 정상 동작 시 이 페이지가 생성됩니다.",
            "샘플 보고서 본문\n=== 라인2 ===\n끝.")
    if not (args.test or args.discord or args.notion):
        p.print_help()
