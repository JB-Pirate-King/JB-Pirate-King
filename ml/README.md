# ML 파이프라인 — AIS 이상 탐지

선박 AIS 데이터 기반 이상 탐지 파이프라인. DCdetect 모델 중심의 Greedy 피처 엔지니어링으로 배포용 ONNX 모델을 생성한다.

---

## 파일 구조

```
ml/
├── core/                    # ML 핵심 로직
│   ├── pipeline.py          # 멀티모델 학습/탐지율 비교 ★
│   ├── preprocess.py        # AIS 전처리 (.csv / .csv.zst / .zip)
│   ├── train_benchmark.py   # 비지도 모델 학습 (9종)
│   ├── eval_anomaly.py      # 탐지율/오탐율 평가
│   ├── feature_engineer.py  # DCdetect Greedy FE + ONNX export ★
│   └── patch_plugin.py      # C++ 플러그인 자동 패치
├── integrations/            # 외부 연동
│   ├── slack_bot.py         # Slack 봇 (로그, 버튼 승인, Claude 질문)
│   ├── sheets.py            # Google Sheets 기록
│   ├── notify.py            # Discord + Notion 보고
│   └── git_manager.py       # 브랜치 자동 생성/커밋
├── orchestrator.py          # 풀 파이프라인 진입점 ★
├── fe_state.json            # FE 시작점 피처 저장 (initial_extra)
├── auto_feat_eng.py         # FE 자동 반복 루프
├── build_3yr_dataset.py     # 2023–2025 균형 데이터셋 빌더
└── download_ais.py          # AIS 원본 데이터 다운로더
```

---

## 두 가지 실행 경로

| 경로 | 진입점 | 용도 |
|---|---|---|
| 풀 오케스트레이터 | `orchestrator.py` | Slack + Sheets + git 자동화. 실제 운용. |
| 단순 학습+평가 | `core/pipeline.py` | Slack/git 없이 빠른 실험용. |

```bash
# 풀 오케스트레이터 (반드시 -m 플래그)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess

# 자동 승인 모드 (무인 실행)
python -m ml.orchestrator ... --auto_approve

# 단순 학습+평가
python ml/core/pipeline.py --train --eval --models dcdetect tranad conv1d --epochs 10

# FE 단독 실행
python ml/core/feature_engineer.py \
  --input D:\ais_data\preprocessed\ais_preprocessed_3yr.csv \
  --base_dir D:\ --max_mmsi 3000 --epochs 5 \
  --export_dir D:\ais_models\dcdetect
```

---

## 오케스트레이터 흐름

```
[전처리(첫 브랜치 1회, --skip_preprocess면 생략)]
   ↓
dcdetect_001: FE(Greedy 1피처 채택) → 채택셋 재학습/평가/threshold → export
              → C++패치·플러그인빌드·커밋·릴리즈 → fe_state 저장
   ↓ 자동 체이닝
dcdetect_002 → 003 → ... → 채택 없으면 수렴 종료
```

- **베이스 Train/Eval 단계 없음** — 베이스라인 학습+평가는 FE 내부에서 수행 (중복·11GB 중복스캔 제거)
- run(브랜치)당 **Greedy 1피처 채택**(`--max_steps 1`) → 새 브랜치로 체이닝
- 채택 시: 채택셋 **재학습** → 배포 `.onnx`/`scaler.json`/`threshold.txt` export → 커밋 → project(upstream) push
- 출력: 지표→Sheets, 모델→브랜치+릴리즈, FE 중간물→`ml/.pipeline_tmp/`(gitignore)

---

## 데이터 경로 (`--base_dir` 기본: `D:\`)

```
<base_dir>/
├── ais_data/
│   ├── raw/2023/, 2024/   # .zip (Marine Cadastre)
│   │   2025/              # .csv.zst
│   └── preprocessed/
│       ├── 2025/daily/    # 일별 전처리 결과
│       ├── ais_preprocessed_2025.csv
│       └── ais_preprocessed_3yr.csv   # 3년 균형 (~10.9 GB)
├── ais_models/{name}/
│   ├── model_{name}.onnx
│   ├── scaler_{name}.json
│   └── threshold_{name}.txt
└── ais_output/
    ├── pipeline/           # comparison_TIMESTAMP.txt/.csv
    └── feat_eng/, feat_eng_iter/   # FE JSON/txt 결과
```

---

## 전처리 (`core/preprocess.py`)

```bash
# 일별 전처리
python ml/core/preprocess.py D:\ais_data\raw\2025 --output_dir D:\ais_data\preprocessed\2025\daily

# 합산
python ml/core/preprocess.py "D:\ais_data\preprocessed\2025\daily\*_preprocessed.csv" ^
    --output D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
```

---

## 피처 엔지니어링 (`core/feature_engineer.py`)

### 알고리즘 — Greedy Forward Selection

1. 베이스라인 학습 (현재 피처셋으로 DCdetect 학습 + FP=1%/5%/10% 평가)
2. 후보 피처를 하나씩 추가해 목적점수(`전체평균 + 1.0 × 약세평균`) 계산
3. 최고 향상이 `--min_gain`(기본 3.0pp) 이상이면 채택
4. 채택셋으로 재학습(model_best) → 순열중요도 + 최종 FP1/5/10 + threshold → 배포 export
5. `--max_steps`로 호출당 채택 횟수 제한 (오케스트레이터=`1`, 브랜치 체이닝 / 미지정 시 수렴까지)

### FP 평가 기준

| 기준 | 임계값 | 의미 |
|---|---|---|
| FP=1% | 정상 점수 **99**퍼센타일 | 1% 선박이 오탐됨 |
| FP=5% | 정상 점수 **95**퍼센타일 | 5% 오탐 허용 |
| FP=10% | 정상 점수 **90**퍼센타일 | 10% 오탐 허용 |

배포 threshold = FP=1% 임계값 → 현장 오탐율도 1%로 보장

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--input` | (필수) | 전처리 CSV |
| `--max_mmsi` | 500 | 학습 선박 수 상한 |
| `--epochs` | 5 | 에포크 수 |
| `--n_anom` | 200 | 시나리오당 이상 시퀀스 수 |
| `--min_gain` | 3.0 | Greedy 채택 최소 향상 (pp) |
| `--initial_extra` | [] | 시작 추가 피처 (fe_state.json 자동 로드) |
| `--export_dir` | — | 배포용 모델 저장 경로 |
| `--holdout_file` | — | FP 측정 전용 파일 (학습 데이터와 완전 분리) |

### 피처 중요도 해석

`importance_pp`: 해당 피처를 제거(랜덤 셔플)했을 때 탐지율 하락량 (pp)
- **음수가 클수록 중요**: `-20.9pp` = 그 피처 없으면 탐지율 20.9%p 하락
- 양수 = 제거해도 탐지율 유지 (혹은 오히려 약간 상승) → 중요도 낮음

---

## 입력 피처

### 베이스 피처 (12개, 고정)

| 피처 | 설명 |
|---|---|
| `sog` | 선속 (knots, AIS 원본) |
| `cog` | 항로각 (0–360°, AIS 원본) |
| `heading` | 선수방향 (0–360°, AIS 원본) |
| `status` | 운항상태 코드 (0=운항, 1=정박, 5=계류 ...) |
| `dt` | 이전 메시지와의 경과시간 (초) |
| `dist_km` | 연속 위경도 간 이동거리 (km, Haversine) |
| `cog_hdg_diff` | COG-Heading 각도 차이 (0–180°) |
| `sog_change` | 속력 변화량 (현재–이전, knots) |
| `cog_hdg_change` | cog_hdg_diff 변화량 |
| `speed_consistency` | SOG와 거리/시간 속력의 일관성 |
| `lat_speed` | 위도 방향 속도 성분 (°/s) |
| `lon_speed` | 경도 방향 속도 성분 (°/s) |

SEQ_LEN = 10

### FE 채택 현황 (as of dcdetect_012)

베이스 12개 + 추가 12개 = **24피처**
최고 탐지율: dcdetect_011 → **83.5% (FP=1%, 23피처)**

---

## 모델 (`core/train_benchmark.py`)

| 모델 | 설명 |
|---|---|
| `dcdetect` | 채널/패치 이중 어텐션 대조 학습 ← **메인** |
| `usad` | 이중 디코더 adversarial 학습 |
| `tranad` | Transformer self-conditioning 재구성 |
| `conv1d` | 1D 합성곱 오토인코더 |
| `lstm` | LSTM Seq2Seq 오토인코더 |
| `tcn` | Dilated Causal Conv 오토인코더 |
| `anomtrans` | Association Discrepancy |
| `iforest` | Isolation Forest |
| `ocsvm` | One-Class SVM |

---

## 평가 시나리오 (32종)

| 그룹 | 설명 |
|---|---|
| Basic (4종) | COG/HDG 불일치, 정박이동, 속도이상, 위치점프 |
| FN (4종) | 규칙 탐지기 회피 |
| D (4종) | ML 모델 1차 회피 |
| E (5종) | ML 모델 2차 회피 |
| F (7종) | 고급 공격 |
| G (7종) | 신규 시나리오 |

---

## Google Sheets 탭 구조

| 탭 | 내용 |
|---|---|
| `dcdetect` | run별 단계 상세 로그 (det_change, n_features, adopted, threshold, elapsed_s) |
| `실행요약` | run 1줄 요약 (fe_baseline, fe_det_fp1/fp5/fp10, fe_threshold, fe_features) |
| `상세로그` | 모든 단계 raw 로그 |
| `시나리오결과` | 시나리오별 탐지율 (FP=1% 기준, 32종) |
| `피처중요도` | 피처별 순열 중요도 (importance_pp, 음수 클수록 중요) |

자세한 컬럼 설명은 `CLAUDE.md` 참고.

---

## 플러그인 배포

```
ais_ids_pi/data/
    model.onnx        ← model_dcdetect.onnx 복사
    scaler.json       ← scaler_dcdetect.json 복사
    threshold.txt     ← threshold_dcdetect.txt 복사
```

빌드/배포 절차: `CLAUDE.md` → Plugin Build & Deploy 섹션 참고.
