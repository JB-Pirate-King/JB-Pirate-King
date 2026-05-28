"""
automation/notion_reporter.py

학습 완료 시 Notion Database에 실험 결과 페이지를 자동 생성한다.

Notion Database 필수 속성:
  - Name (title)        : 모델명 + 타임스탬프
  - Model (select)      : conv1d / tranad / dcdetect ...
  - Status (select)     : Training / Evaluating / Done / Failed
  - Train DR (number)   : 탐지율 (학습)
  - Holdout DR (number) : 탐지율 (홀드아웃)
  - FP Rate (number)    : 오탐율
  - Epochs (number)     : 학습 에포크 수
  - Date (date)         : 실험 날짜
  - Release (url)       : GitHub 릴리즈 URL

사용법:
    from automation.notion_reporter import NotionReporter
    r = NotionReporter()
    r.create_experiment_page(
        model="conv1d", epochs=10,
        train_dr=68.3, holdout_dr=70.8, fp_rate=1.0,
        release_url="https://github.com/heahgo/JB-Pirate-King/releases/tag/v0.1.0",
        notes="3-year balanced dataset, SEQ_LEN=10",
    )
"""

from datetime import datetime, timezone
from notion_client import Client
from automation.config import NOTION_API_KEY, NOTION_DATABASE_ID


class NotionReporter:
    def __init__(self):
        if not NOTION_API_KEY or not NOTION_DATABASE_ID:
            print("[notion] NOTION_API_KEY 또는 NOTION_DATABASE_ID 미설정 — Notion 스킵")
            self._disabled = True
            return
        self._disabled = False
        self._client = Client(auth=NOTION_API_KEY)

    def create_experiment_page(
        self,
        model: str,
        epochs: int,
        train_dr: float,
        holdout_dr: float,
        fp_rate: float,
        release_url: str = "",
        notes: str = "",
        status: str = "Done",
    ) -> str | None:
        if self._disabled:
            return None

        ts = datetime.now(timezone.utc).isoformat()
        title = f"[{model}] ep{epochs} — {datetime.now().strftime('%Y-%m-%d')}"

        properties: dict = {
            "Name":       {"title":  [{"text": {"content": title}}]},
            "Model":      {"select": {"name": model}},
            "Status":     {"select": {"name": status}},
            "Train DR":   {"number": round(train_dr, 2)},
            "Holdout DR": {"number": round(holdout_dr, 2)},
            "FP Rate":    {"number": round(fp_rate, 2)},
            "Epochs":     {"number": epochs},
            "Date":       {"date": {"start": ts}},
        }
        if release_url:
            properties["Release"] = {"url": release_url}

        children = []
        if notes:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": notes}}]
                },
            })

        result_table = (
            f"| 항목 | 값 |\n"
            f"| --- | --- |\n"
            f"| 모델 | {model} |\n"
            f"| 에포크 | {epochs} |\n"
            f"| 탐지율 (학습) | {train_dr:.1f}% |\n"
            f"| 탐지율 (홀드아웃) | {holdout_dr:.1f}% |\n"
            f"| 오탐율 | {fp_rate:.1f}% |\n"
        )
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": result_table}}]
            },
        })

        resp = self._client.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties,
            children=children,
        )
        page_id = resp["id"]
        print(f"[notion] 실험 페이지 생성됨: {page_id}")
        return page_id

    def update_status(self, page_id: str, status: str):
        if self._disabled:
            return
        self._client.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": status}}},
        )
