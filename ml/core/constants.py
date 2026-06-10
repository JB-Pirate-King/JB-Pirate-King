"""공유 상수 — 모델 입력 피처 정의 + 시퀀스 길이의 단일 출처(single source of truth).

이 파일의 BASE_FEATURES 를 바꾸면 전처리/학습/평가/플러그인 패치가 한 곳에서 따라간다.
이전에는 feature_engineer.py(BASE_FEATURES) / patch_plugin.py(BASE_FEATURES) /
orchestrator.py(BASE_FEATURES) / train_benchmark.py(FEATURES) 에 같은 12개 목록이
각각 복제돼 있어, 하나만 고치면 나머지와 어긋나 C++ ML_FEATURE_COUNT 와 불일치할
위험이 있었다. 그 복제를 여기로 통합한다.

임포트 (실행 방식이 셋이라 triple-import 권장):
    try:
        from ml.core.constants import BASE_FEATURES, SEQ_LEN, BASE_INDEX
    except ModuleNotFoundError:
        try:                                         # cwd=ml (CI 등)
            from core.constants import BASE_FEATURES, SEQ_LEN, BASE_INDEX
        except ModuleNotFoundError:                  # `python ml/core/xxx.py` 스크립트 실행
            from constants import BASE_FEATURES, SEQ_LEN, BASE_INDEX
"""

# 기본 입력 피처 12개. 순서 = 모델 입력 채널 순서이므로 임의 재배열 금지
# (바꾸려면 학습된 스케일러/모델·C++ PushFeature 순서까지 마이그레이션 필요).
BASE_FEATURES = [
    "sog", "cog", "heading", "status", "dt", "dist_km",
    "cog_hdg_diff", "sog_change", "cog_hdg_change",
    "speed_consistency", "lat_speed", "lon_speed",
]

# 시퀀스 길이(타임스텝 수). eval_anomaly 는 모델별로 런타임 override 가능.
SEQ_LEN = 10

# 피처 이름 → 인덱스 (lambda 피처가 seq[t][BASE_INDEX["sog"]] 형태로 참조)
BASE_INDEX = {name: i for i, name in enumerate(BASE_FEATURES)}
