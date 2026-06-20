# 채택된 동적 피처 lambda 영속 보관 — orchestrator n_chain 이 채택 시 병합 기록.
# feature_engineer 가 시작 시 exec 로드 (initial_extra 계산에 필수). git 추적.
ADOPTED_FEATURES = {
    "dt_irregularity": ("직전 대비 수신 간격 변동 비율 — dt점프/간헐송출/시간왜곡 타이밍 이상", lambda seq,t: abs(seq[t][_B['dt']] - seq[t-1][_B['dt']]) / max(seq[t-1][_B['dt']], 1e-6) if t>0 else 0.0),
}
