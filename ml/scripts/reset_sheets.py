"""일회용: 마스터 시트의 모델별 탭 데이터를 비움 (헤더 유지).

사용:
  python -m ml.scripts.reset_sheets            # 허브에 등록된 모든 모델 탭 비움
  python -m ml.scripts.reset_sheets conv1d     # 특정 모델 탭만 비움
"""
import sys
from ml.integrations.sheets import from_config, FIXED_TABS

ps = from_config()
only = sys.argv[1] if len(sys.argv) > 1 else None

# 허브(모델목록)에 등록된 모델 목록
models = [only] if only else sorted(m for m in ps._inited if m)
if not models:
    print("등록된 모델 없음 (허브 비어있음)")
    sys.exit(0)

m = ps.master
for model in models:
    print(f"[{model}]")
    # 모델별 탭: {model}_{base} (고정 4탭) + {model} (상세)
    for title in [f"{model}_{b}" for b in FIXED_TABS] + [model]:
        try:
            ws = m.worksheet(title)
        except Exception:
            continue
        header = ws.row_values(1)
        ws.clear()
        if header:
            ws.update(values=[header], range_name="A1")
        print(f"  비움: {title}")

print("Sheets 리셋 완료")
