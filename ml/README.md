# ML 파이프라인 — AIS 이상 탐지

선박 AIS 데이터 기반 이상 탐지 파이프라인. 비지도 9종 모델을 지원한다.

---

## 파일 구조

```
ml/
├── pipeline.py           # 멀티모델 학습/탐지율 비교 파이프라인 ★
├── preprocess.py         # AIS CSV 전처리
├── train_benchmark.py    # 비지도 모델 학습 (9종)
├── eval_anomaly.py       # 탐지율/오탐율 평가
├── feature_engineer.py   # DCdetect 피처 엔지니어링 (Greedy Forward Selection + ONNX export)
├── auto_feat_eng.py      # 피처 엔지니어링 자동 반복 루프 (데이터셋 빌드 → FE 반복)
├── build_3yr_dataset.py  # 2023–2025 균형 통합 데이터셋 빌더
└── notify.py             # Discord 웹훅 + Notion 보고 (notify_config.json, gitignore)
```

---

## 디렉터리 구조

`--base_dir`로 출력 루트를 지정한다 (기본값: `D:\`).

```
<base_dir>/
├── ais_data/
│   ├── raw/2025/                           # 원본 .csv.zst
│   └── preprocessed/2025/
│       ├── daily/                          # 일별 전처리 결과
│       └── ais_preprocessed_2025.csv       # 연도 합산본 (pipeline 기본값)
├── ais_models/
│   └── {name}/                             # 모델별 서브디렉터리
│       ├── model_{name}.onnx
│       ├── scaler_{name}.json
│       └── threshold_{name}.txt
└── ais_output/
    ├── pipeline/
    │   ├── comparison_TIMESTAMP.txt
    │   ├── comparison_TIMESTAMP.csv        # 통합 CSV (전체 모델)
    │   └── {model}_TIMESTAMP.csv           # 모델별 개별 CSV
    └── feat_eng/
        ├── feat_eng_TIMESTAMP.txt          # 피처 엔지니어링 결과 보고서
        └── feat_eng_TIMESTAMP.json         # 결과 JSON
```

### 전처리 실행 순서

```bash
# 1단계: 일별 전처리 (raw → daily/)
python preprocess.py <raw_dir> --output_dir <preprocessed_dir>/daily

# 2단계: 합산 (daily/ → 연도 합산본)
python preprocess.py "<preprocessed_dir>/daily/*_preprocessed.csv" ^
    --output <preprocessed_dir>/ais_preprocessed_2025.csv
```

> 새 날짜 데이터가 추가되면 해당 날짜만 1단계 재실행 후 2단계로 합산.

---

## 멀티모델 비교 파이프라인 (`pipeline.py`) ★

여러 모델을 한 번에 학습하고, 동일 시나리오에서 탐지율을 비교한다.

```bash
# 전체 파이프라인 (전처리 → 학습 → 비교)
python pipeline.py --preprocess --train --eval --models dcdetect tranad conv1d

# 이미 전처리된 파일이 있으면 학습 + 평가만
python pipeline.py --train --eval --models usad tranad dcdetect

# 기존 모델 평가만 (재학습 없음)
python pipeline.py --eval --models usad tranad dcdetect

# 전체 비지도 9개 모델
python pipeline.py --train --eval --unsup

# 이미 학습된 모델은 건너뛰기
python pipeline.py --train --eval --models usad tranad conv1d --skip_trained

# FP 목표값·이상 시퀀스 수 조정
python pipeline.py --eval --models dcdetect tranad --fp_targets 1 5 10 --n_anom 1000

# 출력 루트 변경
python pipeline.py --train --eval --models conv1d --base_dir E:\
```

주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--preprocess` | — | 원본 CSV → 전처리 실행 |
| `--train` | — | 지정 모델 학습 |
| `--eval` | — | 탐지율 비교 평가 |
| `--base_dir` | `D:\` | 출력 기본 경로 — 모델·결과가 이 아래에 저장됨 |
| `--raw_data` | `<base_dir>/ais_data/raw/2025` | 원본 AIS CSV 폴더 |
| `--data_file` | `<base_dir>/ais_data/preprocessed/2025/ais_preprocessed_2025.csv` | 전처리 결과 파일 |
| `--models` | — | 비교 모델 목록 |
| `--unsup` | — | 비지도 9개 모델 전체 |
| `--epochs` | 모델별 | 학습 에포크 수 |
| `--n_anom` | 500 | 평가용 시나리오당 이상 시퀀스 수 |
| `--fp_targets` | 1 5 10 | 비교 기준 오탐율 목표값(%) |
| `--skip_trained` | — | 이미 학습된 모델 건너뜀 |

출력:
- `<base_dir>/ais_output/pipeline/comparison_TIMESTAMP.txt` — 텍스트 비교 테이블
- `<base_dir>/ais_output/pipeline/comparison_TIMESTAMP.csv` — 통합 CSV 결과
- `<base_dir>/ais_output/pipeline/{model}_TIMESTAMP.csv` — 모델별 개별 CSV

---

## 피처 엔지니어링 (`feature_engineer.py` + `auto_feat_eng.py`)

DCdetect를 기준으로 파생 피처를 하나씩 추가하며 탐지율이 향상될 때만 채택하는
**Greedy Forward Selection** + 채택셋 **Permutation Importance**를 수행한다 (FP 1% 임계 기준).
선택이 끝나면 최적 피처셋으로 학습한 모델을 **배포용 ONNX/scaler/threshold로 export**한다.

```bash
# 단일 패스
python feature_engineer.py \
  --input  D:\ais_data\preprocessed\ais_preprocessed_3yr.csv \
  --base_dir D:\ --max_mmsi 3000 --epochs 5 --n_anom 150 \
  --max_feat 18 --export_dir D:\ais_models\dcdetect

# 자동 반복 루프 (데이터셋 이미 빌드된 경우). 실시간 로그는 PYTHONUNBUFFERED=1
python auto_feat_eng.py --no_wait --skip_build
```

`feature_engineer.py` 주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--input` | (필수) | 전처리 CSV 경로 |
| `--base_dir` | `C:\Users\imcas` | 결과(`ais_output/feat_eng`) 저장 루트 |
| `--max_mmsi` | 500 | 학습에 사용할 MMSI 수 |
| `--epochs` | 5 | 에폭 수 |
| `--n_anom` | 200 | 시나리오당 이상 시퀀스 수 |
| `--min_gain` | 3.0 | 피처 채택 최소 향상폭(pp) |
| `--weak_floor` / `--weak_weight` | 50.0 / 1.0 | 약세 시나리오를 목적함수에서 가중 |
| `--max_feat` | 없음 | 총 피처 수 상한 (16이면 nhead=8 유지) |
| `--initial_extra` | 코드값 | Greedy 시작 추가 피처셋 |
| `--export_dir` | 없음 | 최적 모델을 `model_dcdetect.onnx`/`scaler_dcdetect.json`/`threshold_dcdetect.txt`로 저장 |
| `--out_json` | 자동 | 결과 JSON 경로 |

- 후보 파생 피처는 코드의 `CANDIDATE_FEATURES`에 ~19종 정의됨 (cog_change, turn_rate, heading_rate, accel, vec_sog_diff, heading_change, lowspeed_crab, hdg_perp_score 등).
- **검증 결과**: 16피처 = 12 base + `accel, heading_rate, vec_sog_diff, heading_change` 가 최적(~88.8%). 그 이상은 향상 없음.
- **scaler의 `features` 배열이 피처 순서의 기준** — 플러그인 C++ `ML_FEATURE_COUNT`/`PushFeature`가 이 순서와 일치해야 함.

### 시퀀스 캐시
`load_raw_seqs`는 첫 로드 시 시퀀스를 `<input>.s<max_mmsi>_seed<SEED>_…seqs.pkl`로 캐시한다.
이후 iteration/실행은 대용량 CSV(예: 3년치 ~10.9GB)를 재파싱하지 않고 캐시를 재사용한다 (SEED 고정 → 결정적).

### 3년 균형 데이터셋 (`build_3yr_dataset.py`)
- 출력: `D:\ais_data\preprocessed\ais_preprocessed_3yr.csv`
- 단일 일자/계절 편향(confirmation bias) 방지를 위해 2023–2025 전 기간에서 월별로 고르게 추출.
- MMSI 샘플링: 각 MMSI를 주요 `YYYY-MM`로 버킷팅 → `max_mmsi`를 활성 월에 균등 배분 (버킷 내 무작위, SEED 고정).

`auto_feat_eng.py`는 다운로드 감지 → 데이터셋 빌드 → FE를 `--max_iter`회 반복(이전 best를 다음 `--initial_extra`로 연결, 신규 채택 없으면 종료)하며 매 iteration Discord·Notion 보고를 보낸다.

출력: `<base_dir>/ais_output/feat_eng/feat_eng_TIMESTAMP.txt/.json`

---

## 빠른 시작

```bash
pip install torch onnx onnxruntime tqdm numpy
pip install scikit-learn   # iforest / ocsvm 사용 시
pip install zstandard      # .csv.zst 원본 파일 처리 시

# 전처리 (일별 저장 후 합산)
python preprocess.py <raw_dir> --output_dir <preprocessed_dir>/daily
python preprocess.py "<preprocessed_dir>/daily/*_preprocessed.csv" --output <preprocessed_dir>/ais_preprocessed_2025.csv

# 비지도 학습 (단독)
python train_benchmark.py --model dcdetect
python train_benchmark.py --model dcdetect --output_dir <base_dir>/ais_models

# 평가
python eval_anomaly.py --model dcdetect
```

---

## 입력 피처 (12개)

| 피처 | 설명 |
|---|---|
| `sog` | 대지 속력 (knot) |
| `cog` | 대지 침로 (도) |
| `heading` | 선수 방위 (도) |
| `status` | 항법 상태 코드 |
| `dt` | 이전 메시지와의 시간 간격 (초) |
| `dist_km` | 이전 위치와의 거리 (km) |
| `cog_hdg_diff` | COG와 Heading 차이 (도) |
| `sog_change` | 속력 변화량 |
| `cog_hdg_change` | COG-Heading 차이 변화량 |
| `speed_consistency` | 속력과 이동거리 일관성 비율 |
| `lat_speed` | 위도 방향 이동 속도 (deg/s) |
| `lon_speed` | 경도 방향 이동 속도 (deg/s) |

---

## 비지도 모델 (`train_benchmark.py`)

정상 데이터만으로 학습 → 재구성 오차(MSE)로 이상 판정.

| 모델 | 설명 |
|---|---|
| `usad` | UnSupervised Anomaly Detection — 이중 디코더 adversarial 학습 |
| `tranad` | TranAD — Transformer 기반 self-conditioning 재구성 |
| `conv1d` | Conv1D Autoencoder — 1D 합성곱 시계열 재구성 |
| `lstm` | LSTM Autoencoder — Seq2Seq 재구성 |
| `tcn` | TCN Autoencoder — Dilated Causal Conv 재구성 |
| `anomtrans` | Anomaly Transformer — Association Discrepancy 기반 |
| `dcdetect` | DCDetector — 채널/패치 이중 어텐션 대조 학습 |
| `iforest` | Isolation Forest — 랜덤 트리 고립 기반 |
| `ocsvm` | One-Class SVM — RBF 커널 결정 경계 기반 |

```bash
python train_benchmark.py --model dcdetect
python train_benchmark.py --model all --epochs 30
python train_benchmark.py --model dcdetect --output_dir <base_dir>/ais_models
```

출력: `<base_dir>/ais_models/model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`

---

## 평가 (`eval_anomaly.py`)

32개 이상 시나리오에 대한 탐지율/오탐율 측정.

```bash
python eval_anomaly.py --model dcdetect
python eval_anomaly.py --model conv1d
```

시나리오 그룹:

| 그룹 | 설명 |
|---|---|
| 기본 (4종) | COG/HDG 불일치, 정박이동, 속도이상, 위치점프 |
| FN (4종) | 기존 규칙 탐지기 회피 설계 이상 |
| D (4종) | ML 모델 1차 회피 시도 (LowSlow, GradDrift 등) |
| E (5종) | ML 모델 2차 회피 시도 (Smooth, Shadow 등) |
| F (7종, 홀드아웃) | 고급 공격 — 학습 미포함, 평가 전용 |
| G (7종, 홀드아웃) | 신규 홀드아웃 시나리오 |

---

## 출력 파일

| 파일 | 설명 |
|---|---|
| `model_{name}.onnx` | 비지도 ONNX 모델 |
| `scaler_{name}.json` | Min-Max 스케일러 |
| `threshold_{name}.txt` | 이상 판정 임계값 |
| `eval_result_{name}.txt` | 평가 결과 |

---

## 플러그인 배포

학습 완료 후 아래 파일을 플러그인 `data/` 폴더에 넣는다.

```
ais_ids_pi/data/
    model.onnx        (model_{name}.onnx → model.onnx 로 복사)
    scaler.json       (scaler_{name}.json → scaler.json 로 복사)
    threshold.txt     (threshold_{name}.txt → threshold.txt 로 복사)
```

---

## 릴리즈 & 버전 관리

`v{major}.{minor}.{patch}` 규칙:

| 올리는 경우 | 예시 |
|---|---|
| **major** — 입력 피처 변경, SEQ_LEN 변경, 모델 인터페이스 파괴적 변경 | v1→v2 |
| **minor** — 새 모델 추가, 새 평가 시나리오 추가 | v1.0→v1.1 |
| **patch** — 임계값 재조정, 버그픽스, 동일 구조 재학습 | v1.0.0→v1.0.1 |

릴리즈 첨부 파일:
- `model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`
- `comparison_TIMESTAMP.txt/.csv` — 비교 결과
- `{model}_TIMESTAMP.csv` — 모델별 개별 CSV

```bash
# 태그 생성 후 릴리즈
git tag v1.0.0 && git push origin main --tags
gh release create v1.0.0 <파일들...> --title "v1.0.0" --notes "..."

# 다른 PC에서 다운로드
gh release download v1.0.0 --dir <base_dir>/ais_models
```

자세한 릴리즈 절차 및 버전 히스토리는 `CLAUDE.md` 참고.
