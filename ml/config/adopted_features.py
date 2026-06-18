# 채택된 동적 피처 lambda 영속 보관 — orchestrator n_chain 이 채택 시 병합 기록.
# feature_engineer 가 시작 시 exec 로드 (initial_extra 계산에 필수). git 추적.
ADOPTED_FEATURES = {
    "anchor_motion": ("정박/계류 상태(status 1,5)인데 이동거리 큼 — 정박이동·status 위장 직접 포착", lambda seq,t: (seq[t][_B['dist_km']] if seq[t][_B['status']] in (1.0,5.0) else 0.0)),
    "kinematic_speed_gap": ("거리/시간으로 계산한 실제속도와 보고 sog의 괴리 — 위장이 sog만 조작하고 위치는 못 맞출 때 커짐", lambda seq,t: abs(seq[t][_B['dist_km']]/max(seq[t][_B['dt']],1e-6) - seq[t][_B['sog']])),
    "heading_micro_jitter": ("sog_change가 작은데 heading만 미세 변동 — 매끄러운 정상모방(Mimicry) 속 기수방향 떨림 노출", lambda seq,t: abs(seq[t][_B['heading']]-seq[t-1][_B['heading']]) / (1.0+abs(seq[t][_B['sog_change']])) if t>0 else 0.0),
}
