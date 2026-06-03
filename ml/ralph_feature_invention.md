# Ralph 임무: AIS 파생 피처 자율 발명

너는 dcdetect 이상탐지 모델의 탐지율을 올릴 **새 파생 피처를 발명**한다.
기존 `CANDIDATE_FEATURES` 풀(고정)에서 고르는 게 아니라, **풀에 없는 새 피처 코드를 작성**한다.

## 매 iteration 절차 (정확히 따를 것)

1. **과거 기록 확인**: `ml/.ralph_fe_log.md` 를 읽어 이미 시도한 가설/피처를 파악.
   같은 아이디어 반복 금지. 없으면 이번이 첫 iter.

2. **약세 시나리오 선정**: 가장 최근 FE 출력(`ml/.pipeline_tmp/` 의 `feat_eng_iter*.json`
   또는 직접 실행 출력)에서 탐지율 <50% 시나리오 1개 선택.
   기록이 없으면 아래 알려진 약세를 타겟: `D1-LowSlow`, `FN3-COG경계`, `G3-PhantomHDG`.

3. **물리 가설 수립**: 그 공격이 정상 항행과 어떻게 물리적으로 다른지 1~2문장 가설.
   (예: "저속에선 heading-course 불일치가 정상이라 선형 피처로 안 잡힘 → 저속 가중 필요")

4. **피처 1개 코드 작성**: `ml/core/feature_engineer.py` 의 `CANDIDATE_FEATURES` dict 에
   `(설명, lambda seq, t: ...)` 형식으로 **딱 1개** 추가. 규칙:
   - 컬럼 접근은 `seq[t][_B["sog"]]` 처럼 `_B[...]` 인덱싱. BASE 12피처: sog, cog, heading,
     status, dt, dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency,
     lat_speed, lon_speed
   - 이전 행은 `seq[t-1]`. 반드시 `if t > 0 else 0.0` 가드.
   - 0 나눗셈 방지(`max(x, 1e-6)`), 센티넬값 가드(`cog_hdg_diff < 0` 은 heading 불가).
   - 순수함수(부작용 없음). 기존 피처와 **수식이 실질적으로 다른** 새 신호일 것.
   - 주석에 타겟 시나리오 + 물리 근거 명시.

5. **검증**: 아래 명령으로 FE 단독 실행 (작은 데이터, 빠름):
   ```
   python ml/core/feature_engineer.py \
     --input D:/ais_data/preprocessed/ais_preprocessed_3yr.csv \
     --base_dir D:/ --max_mmsi 50 --epochs 1 --max_steps 1 \
     --out_json ml/.ralph_tmp.json
   ```
   출력의 후보 평가표에서 **네가 추가한 피처 줄**의 목적점수 gain(`▲/▼` 뒤 숫자)을 읽어라.

6. **판정 + 처리**:
   - gain **≥ +3.0pp**: 성공. 피처 유지. `git add ml/core/feature_engineer.py && git commit`
     (메시지: `feat(fe): ralph 발명 <피처명> obj+<gain>`). 채택 카운트 +1.
   - **0 ~ +3.0pp**: 미미. 피처는 풀에 남기되 채택 카운트엔 미반영. 로그에 "marginal" 기록.
   - **< 0pp**: 해로움. 방금 추가한 lambda를 **제거(되돌림)**. 로그에 실패 가설 기록.

7. **로그 갱신**: `ml/.ralph_fe_log.md` 에 한 줄 추가:
   `iter N | <피처명> | 타겟=<시나리오> | gain=<숫자>pp | <성공/marginal/실패> | 가설=<요약>`

## 완료 조건

`git log --oneline | grep -c "ralph 발명"` 으로 **성공 채택 3개 누적**되면:
최종 요약(채택 3개 이름·gain·타겟)을 출력하고 정확히 다음을 출력:

`<promise>RALPH_FE_DONE</promise>`

## 금지

- 거짓 promise 출력 금지 (3개 진짜 채택 전엔 절대 금지).
- orchestrator.py 실행 금지 (git 브랜치 체이닝 충돌). FE 단독만.
- 한 iter에 피처 2개 이상 추가 금지. 정확히 1개.
- 데이터 파일·CANDIDATE_FEATURES 외 구조 변경 금지.
