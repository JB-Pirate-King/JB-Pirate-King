# ML 파이프라인 — AIS 이상 탐지

선박 AIS 데이터 기반 이상 탐지 파이프라인. 비지도 9종 모델을 지원한다.

---

## 파일 구조

```
ml/
├── core/                    # ML 핵심 로직
│   ├── pipeline.py          # 멀티모델 학습/탐지율 비교 파이프라인 ★
│   ├── preprocess.py        # AIS 전처리 (.csv / .csv.zst / .zip, 구·신형 컬럼 포맷 호환)
│   ├── train_benchmark.py   # 비지도 모델 학습 (9종)
│   ├── eval_anomaly.py      # 탐지율/오탐율 평가
│   └── feature_engineer.py  # DCdetect 피처 엔지니어링 (Greedy Forward Selection + ONNX export)
├── integrations/            # 외부 연동
│   ├── slack_bot.py         # Slack 봇 (로그 전송, 버튼 승인, Claude 원격 질문)
│   ├── sheets.py            # Google Sheets 기록
│   ├── notify.py            # Discord 웹훅 + Notion 보고
│   └── git_manager.py       # 브랜치 자동 생성/커밋
├── orchestrator.py          # 풀 파이프라인 진입점 (Slack 승인 게이트 + MLflow + git)
├── auto_feat_eng.py         # 피처 엔지니어링 자동 반복 루프 (데이터셋 빌드 → FE 반복)
├── build_3yr_dataset.py     # 2023–2025 균형 통합 데이터셋 빌더
└── download_ais.py          # AIS 원본 데이터 다운로더
```

---

## 두 가지 실행 경로

| 경로 | 진입점 | 용도 |
|---|---|---|
| 풀 오케스트레이터 | `orchestrator.py` | Slack 승인 게이트 + Sheets + MLflow + git 자동화. 실제 운용. |
| 단순 학습+평가 | `core/pipeline.py` | Slack/git 없이 학습+평가만. 빠른 실험용. |

```bash
# 풀 오케스트레이터 (반드시 -m 플래그)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess

# 단순 학습+평가
python ml/core/pipeline.py --train --eval --models dcdetect tranad conv1d --epochs 10

# 평가만
python ml/core/pipeline.py --eval --models dcdetect
```

---

## 디렉터리 구조 (데이터/모델)

`--base_dir`로 출력 루트를 지정한다 (기본값: `D:\`).

```
<base_dir>/
├── ais_data/
│   ├── raw/
│   │   ├── 2023/, 2024/       # .zip (Marine Cadastre)
│   │   └── 2025/              # .csv.zst
│   └── preprocessed/
│       ├── 2025/daily/        # 일별 전처리 결과
│       ├── ais_preprocessed_2025.csv
│       └── ais_preprocessed_3yr.csv   # 3년 균형 데이터셋 (~10.9 GB)
├── ais_models/
│   └── {name}/
│       ├── model_{name}.onnx
│       ├── scaler_{name}.json
│       └── threshold_{name}.txt
└── ais_output/
    ├── pipeline/
    │   ├── comparison_TIMESTAMP.txt/.csv
    │   └── {model}_TIMESTAMP.csv
    └── feat_eng/, feat_eng_iter/
```

---

## 전처리 (`core/preprocess.py`)

- `.csv`, `.csv.zst`(2025+), `.zip`(Marine Cadastre ≤2024) 자동 처리.
- 구·신형 컬럼명 자동 정규화: `MMSI/BaseDateTime/LAT/LON` ↔ `mmsi/base_date_time/latitude/longitude`.

```bash
# 1단계: 일별 전처리
python ml/core/preprocess.py D:\ais_data\raw\2025 --output_dir D:\ais_data\preprocessed\2025\daily

# 2단계: 합산
python ml/core/preprocess.py "D:\ais_data\preprocessed\2025\daily\*_preprocessed.csv" ^
    --output D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
```

---

## 멀티모델 비교 파이프라인 (`core/pipeline.py`)

```bash
python ml/core/pipeline.py --train --eval --models dcdetect tranad conv1d
python ml/core/pipeline.py --eval --models dcdetect tranad --fp_targets 1 5 10 --n_anom 1000
python ml/core/pipeline.py --train --eval --models conv1d --base_dir E:\
```

주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--train` / `--eval` | — | 학습 / 평가 |
| `--models` | — | 모델 목록 |
| `--unsup` | — | 비지도 9개 전체 |
| `--base_dir` | `D:\` | 출력 기본 경로 |
| `--data_file` | `D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv` | 전처리 파일 |
| `--epochs` | 모델별 | 에포크 수 |
| `--fp_targets` | `1 5 10` | 비교 기준 오탐율(%) |
| `--skip_trained` | — | 이미 학습된 모델 건너뜀 |

---

## 피처 엔지니어링 (`core/feature_engineer.py` + `auto_feat_eng.py`)

**Greedy Forward Selection** — 후보 피처를 하나씩 추가해 목적점수(전체평균 + 1.0 × 약세평균)가 +3.0pp 이상 향상될 때만 채택. 최적 피처셋으로 **배포용 ONNX/scaler/threshold export**.

> **FP 기준 = 1%, 홀드아웃 실제 정상 시퀀스 대상.** `load_raw_seqs`가 MMSI 단위로 train / eval-normal 분리 (`--eval_ratio` 기본 0.2, 월별 균등 + 학습 선박 완전 분리). `evaluate()`는 eval-normal 점수의 99퍼센타일을 임계값으로 사용.

```bash
# 단일 패스
python ml/core/feature_engineer.py \
  --input D:\ais_data\preprocessed\ais_preprocessed_3yr.csv \
  --base_dir D:\ --max_mmsi 3000 --epochs 5 --n_anom 150 \
  --export_dir D:\ais_models\dcdetect

# 자동 반복 루프
python ml/auto_feat_eng.py --no_wait --skip_build
```

`feature_engineer.py` 주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--input` | (필수) | 전처리 CSV |
| `--max_mmsi` | 500 | 학습 MMSI 수 |
| `--epochs` | 5 | 에포크 수 |
| `--n_anom` | 200 | 시나리오당 이상 시퀀스 수 |
| `--min_gain` | 3.0 | 채택 최소 향상폭(pp) |
| `--weak_floor` / `--weak_weight` | 50.0 / 1.0 | 약세 시나리오 가중 |
| `--max_feat` | — | 총 피처 수 상한 |
| `--initial_extra` | 코드 기본값 | Greedy 시작 추가 피처셋 |
| `--export_dir` | — | `model_dcdetect.onnx` / `scaler_dcdetect.json` / `threshold_dcdetect.txt` 저장 경로 |

- **검증 결과**: 16피처 = 12 base + `accel, heading_rate, vec_sog_diff, heading_change` 가 최적(~88.8%).
- **scaler의 `features` 배열이 피처 순서의 기준** — 플러그인 `ML_FEATURE_COUNT`/`PushFeature`가 이 순서와 일치해야 함.
- **시퀀스 캐시**: 첫 로드 시 `.holdout.pkl`로 저장, 이후 재파싱 없이 재사용 (SEED 고정, 결정적).

### 3년 균형 데이터셋 (`build_3yr_dataset.py`)

2023–2025 전 기간에서 월별 균등 추출. `auto_feat_eng.py`가 자동 호출하거나 단독 실행 가능.

---

## 모델 (`core/train_benchmark.py`)

| 모델 | 설명 |
|---|---|
| `usad` | 이중 디코더 adversarial 학습 |
| `tranad` | Transformer 기반 self-conditioning 재구성 |
| `conv1d` | 1D 합성곱 오토인코더 |
| `lstm` | LSTM Seq2Seq 오토인코더 |
| `tcn` | Dilated Causal Conv 오토인코더 |
| `anomtrans` | Association Discrepancy 기반 |
| `dcdetect` | 채널/패치 이중 어텐션 대조 학습 |
| `iforest` | Isolation Forest |
| `ocsvm` | One-Class SVM |

임계값은 홀드아웃 실제 정상 시퀀스의 99퍼센타일 (FP=1% 기준).

---

## 입력 피처 (12개)

`sog, cog, heading, status, dt, dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency, lat_speed, lon_speed` — SEQ_LEN = 10

---

## 평가 시나리오 (32종, `core/eval_anomaly.py`)

| 그룹 | 설명 |
|---|---|
| 기본 (4종) | COG/HDG 불일치, 정박이동, 속도이상, 위치점프 |
| FN (4종) | 규칙 탐지기 회피 설계 |
| D (4종) | ML 모델 1차 회피 시도 |
| E (5종) | ML 모델 2차 회피 시도 |
| F (7종, 홀드아웃) | 고급 공격 — 학습 미포함 |
| G (7종, 홀드아웃) | 신규 홀드아웃 시나리오 |

---

## 플러그인 배포

```
ais_ids_pi/data/
    model.onnx        ← model_dcdetect.onnx 복사
    scaler.json       ← scaler_dcdetect.json 복사
    threshold.txt     ← threshold_dcdetect.txt 복사
```

자세한 빌드/배포 절차 및 버전 히스토리는 `CLAUDE.md` 참고.
