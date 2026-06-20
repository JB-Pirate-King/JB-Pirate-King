# 채택된 동적 피처 lambda 영속 보관 — orchestrator n_chain 이 채택 시 병합 기록.
# feature_engineer 가 시작 시 exec 로드 (initial_extra 계산에 필수). git 추적.
ADOPTED_FEATURES = {
    "dt_irregularity": ("직전 대비 수신 간격 변동 비율 — dt점프/간헐송출/시간왜곡 타이밍 이상", lambda seq,t: abs(seq[t][_B['dt']] - seq[t-1][_B['dt']]) / max(seq[t-1][_B['dt']], 1e-6) if t>0 else 0.0),
    "speed_consistency_min": ("윈도우 내 속도일관성 최솟값 — 구간 중 가장 모순적인 순간을 포착해 맥락기반 위장 탐지", lambda seq,t: min(r[_B['speed_consistency']] for r in seq[:t+1])),
    "cog_change_reversal": ("연속 COG-HDG 변화의 부호 반전 강도(지그재그) — 인위적 왕복/봇 패턴 포착", lambda seq,t: -(seq[t][_B['cog_hdg_change']]*seq[t-1][_B['cog_hdg_change']]) if t>0 else 0.0),
}
