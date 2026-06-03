"""일회용: Google Sheets 모든 파이프라인 탭의 데이터 행을 비움 (헤더만 유지)."""
import sys
from ml.integrations.sheets import (
    PipelineSheets, from_config, FIXED_TABS, MODEL_HEADERS,
)

MODEL = sys.argv[1] if len(sys.argv) > 1 else "dcdetect"

ps = from_config()
sh = ps.sh

targets = list(FIXED_TABS.keys()) + [MODEL]
for title in targets:
    try:
        ws = sh.worksheet(title)
    except Exception:
        print(f"  (없음) {title}")
        continue
    header = ws.row_values(1)
    ws.clear()
    if header:
        ws.update("A1", [header])
    print(f"  비움: {title} (헤더 {len(header)}열 유지)")

print("Sheets 리셋 완료")
