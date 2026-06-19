# 채택된 동적 피처 lambda 영속 보관 — orchestrator n_chain 이 채택 시 병합 기록.
# feature_engineer 가 시작 시 exec 로드 (initial_extra 계산에 필수). git 추적.
ADOPTED_FEATURES = {
    "sog_accel": ("속도변화량/시간간격 = 가속도 — 순간 속도급증/단계적 위장 포착", lambda seq,t: seq[t][_B["sog_change"]]/max(seq[t][_B["dt"]],1e-6)),
    "pos_sog_ratio": ("위치기반 이동속도크기 / 보고 SOG — 저속 위장 시 보고속도와 실제이동 불일치", lambda seq,t: ((seq[t][_B['lat_speed']]**2 + seq[t][_B['lon_speed']]**2)**0.5) / max(seq[t][_B['sog']], 1e-6)),
}
