# 약세 시나리오 진단 — 피처 부족이 아니라 "분포 중첩/합성 artifact" (status-코드 FE 중단 권고)

> 2026-06-05 · dcdetect 비지도 FE "Iteration 9 (status 보강)" 사전 진단
> **결론: 큐의 #1 최우선 "status 전용 파생피처 추가" 계획은 막다른 길이며 모델을 악화시킨다.**
> 그리고 이 문제는 FN4 하나가 아니라 **약세 시나리오 전반(FN4·D1·FN3·정박이동)**에 해당한다.
> 재현 스크립트: `ml/experiments/{fn4_status_probe, fn4_probe_matched, status_dist_check, fn4_separability, scenario_overlap_check}.py`
> 검증: 5개 독립 실험 + 4-에이전트 적대적 리뷰(nhead 교란 지적 → 교정 완료).

## TL;DR
- **FN4-status**(비통상 항법상태 코드 조작)는 공통 최약세(dcdetect 7%)다. 팀은 "status 파생피처 추가 후 FE 재실행"(Iter9)을 #1 우선과제로 큐에 올렸다. 그러나 status 후보 피처는 **이미 추가돼 있었고 Greedy가 채택하지 않았다.** 본 진단은 그 이유를 규명한다: **채택하면 모델이 나빠진다(Greedy가 옳았다).**
- **교란제거 A/B**(n_feat=20·nhead=4 고정, 2시드): status 피처를 중립피처와 1:1 교체하면 FN4는 그대로(7.7→6.5, 정체/소폭하락)이고 전체평균 −2.5pp·목적점수 −10.2·**D1-LowSlow 57→36 붕괴**. status-코드 피처엔 FN4 신호가 없고 모델 용량만 잠식한다.
- **근본 원인 = 분포 중첩**: 실제 정상 트래픽에서 공격 '형태'가 흔하다 → 비지도 재구성으로 분리 불가(FP=1%에서 공격을 잡으려면 그만큼 정상도 오탐).
  - **D1-LowSlow: 정상의 20.9%** (저속 선박은 합법적으로 큰 crab각을 가짐)
  - **FN4-status: 정상의 4.4%** (어업7·범주8·조종불능2·조종제한3·압항12는 합법)
  - **FN3-COG경계: 정상의 3.5%** (선회 시 수직 헤딩 발생)
- **합성 artifact 의존**: FN4가 정상과 구별되는 유일 지점은 생성기 규칙성(heading=cog 100% vs 정상 6.7%, sog_change=0, cog_hdg_diff≈0)뿐 — 실제 공격자는 재현 안 함. 즉 FN4·FN2 "탐지"는 **벤치마크 과적합**이지 실공격 탐지가 아니다.

## 권고 (다음 단계 — 큐 수정 제안)
1. **❌ status-코드 파생피처 추가(Iter9 원안) 중단.** 모델 악화 입증. Greedy 미채택은 정상 동작.
2. **시나리오를 '진짜 물리적 모순'으로 재설계** 후에만 FE 적용. 예) status=정박인데 **고속+위치이동**(현행 정박이동은 status=1 & 저속이라 정상과 겹침), 저속인데 **COG 고정+heading 요동**(GPS 스푸핑) 등. 코드 치환·합성 규칙성이 아니라 운동학적 불가능성을 주입해야 비지도 탐지가 의미를 가진다.
3. **분포-중첩 시나리오는 지도/규칙 레이어로**: ModernTCN 지도(FP1 98.8%, ADR-002)나 규칙(정박<1.5kn 등)이 담당. 비지도 dcdetect엔 "탐지 한계(분포 중첩)" 시나리오로 **명시 라벨링**.
4. **드리프트 수정**: 배포 모델 `D:\ais_models\dcdetect`는 20피처(`status_fn4_flag`,`anchor_suspicion`)인데 develop `fe_state.json`/`INITIAL_EXTRA`는 18피처(status 없음). **배포본의 `status_fn4_flag`는 본 진단상 역효과** → 코드를 배포본에 맞추지 말고 status_fn4_flag 제외 셋으로 재정렬 + 재배포 검토.
5. **(논문)** "AIS 비지도 이상탐지의 탐지가능성 한계 — 시나리오↔정상 분포 중첩"으로 정식화하면 한국융합보안학회 기여 가능. FE로 약세 탐지율을 올린 과거 수치 중 일부는 합성 artifact 과적합일 수 있어 검증 필요.

## 증거

### A. 교란제거 A/B — status 피처 효과 격리 (n_feat=20·nhead=4 고정, 시드 42·123 평균)
`fn4_probe_matched.py` · 중립피처(dist_speed_err/cog_change)를 status 피처로 1:1 교체.
| 변형 | status수 | 전체 FP1 | FN4 | D1-LowSlow | 목적점수 |
|---|---|---|---|---|---|
| C0 (중립2) | 0 | 91.0 | 7.7 | 57.0 | 136.3 |
| C1 (status1) | 1 | 90.8 | 7.5 | 50.5 | 134.3 |
| C2 (status2) | 2 | **88.5** | **6.5** | **36.3** | **126.1** |
→ nhead 고정에도 FN4 무개선·전체/목적/ D1 회귀. status 피처는 순손해. (FN4 시드편차 ±2.5pp = 노이즈 수준)

### B. 최초 probe (nhead 혼재, 참고) — `fn4_status_probe.py`
V0 base6(18,nhead2) FN4 7.0% → V3 status블록(22,nhead2) FN4 0%, 전체 88.7→85.7, FN3·F7·D4·정박·D1 동반 회귀. (V1/V2는 19피처 nhead=1 교란 → A의 교란제거판으로 대체 확인)

### C. 정상 status 분포 — `status_dist_check.py` (홀드아웃 139,693)
전 구간 비통상 status(=FN4 형태) 정상 시퀀스 **4.39%**. fishing(7)1.1%·sailing(8)1.4%·not-under-cmd(2)0.45%·restricted(3)0.43%·pushing(12)1.0%. status15(undefined)=36%, 코드의 "통상={0,1,5}" 가정 부정확.

### D. 시나리오↔정상 중첩 — `scenario_overlap_check.py`
| 시나리오 형태 | 정상 부합 | 비지도 탐지 |
|---|---|---|
| D1-LowSlow (저속 & cog_hdg_diff>50) | **20.91%** | 불가 |
| FN4-status (전구간 비통상) | 4.39% | 불가 |
| FN3-COG경계 (cog_hdg_diff 85~99) | 3.49% | 불가 |
| anchor-move (status∈{1,5} & sog>1.5) | 2.43% | 경계 |

### E. FN4 분리가능성 — `fn4_separability.py` (정상-비통상 6,130 vs FN4합성 1,000)
heading≈cog: 정상 6.7% vs **FN4 100%** · cog_hdg_diff 22.5°±42 vs **0.5°±0.3** · sog_change 0.15±0.41 vs **0.000** · speed_consistency 0.99±0.30 vs **1.000**.
→ status 외 구별신호는 전부 합성 규칙성. 실제 status-spoof(정상 운동 유지)는 이 신호로 못 잡음.

## 메커니즘 (왜 status 피처가 FN4를 더 못 잡게 하나)
학습데이터의 4.4%가 비통상-status 정상이므로, 모델은 status 피처를 넣으면 "비통상 status=정상"으로 학습 → FN4를 더 잘 재구성 → 오차 하락 → FP=1% 임계 아래로. (probe threshold V0 0.00128 → V3 0.00040 하락이 이를 뒷받침)

## 한계
- 단일 날짜(2017-08-02) 기반. 분포율은 변동 가능하나 4~21%는 충분히 커서 결론 불변.
- A/B 2시드, FE 실제 운용(5 epoch·다후보)과 epoch=4 차이. 방향성·분포증거는 학습설정과 무관하게 성립.

## 산출물
- 스크립트 5종(`ml/experiments/`), 결과 JSON `ml/.pipeline_tmp/fn4_probe_*.json`·`fn4_matched_*.json`.
- 적대적 리뷰 4-에이전트(워크플로) — nhead 교란 지적 반영 완료.
