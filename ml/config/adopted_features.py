# 채택된 동적 피처 lambda 영속 보관 — orchestrator n_chain 이 채택 시 병합 기록.
# feature_engineer 가 시작 시 exec 로드 (initial_extra 계산에 필수). git 추적.
ADOPTED_FEATURES = {
    "anchor_motion": ("정박/계류 상태(status 1,5)인데 이동거리 큼 — 정박이동·status 위장 직접 포착", lambda seq,t: (seq[t][_B['dist_km']] if seq[t][_B['status']] in (1.0,5.0) else 0.0)),
    "kinematic_speed_gap": ("거리/시간으로 계산한 실제속도와 보고 sog의 괴리 — 위장이 sog만 조작하고 위치는 못 맞출 때 커짐", lambda seq,t: abs(seq[t][_B['dist_km']]/max(seq[t][_B['dt']],1e-6) - seq[t][_B['sog']])),
}
