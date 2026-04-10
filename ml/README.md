# AIS IDS ML 파이프라인

## 개요

실제 수집된 AIS 데이터를 전처리하고 LSTM Autoencoder를 학습하여 이상 AIS 신호를 탐지하는 파이프라인입니다.

## 파일 구성

| 파일 | 설명 |
|---|---|
| `preprocess.py` | AIS CSV 데이터 전처리 스크립트 |
| `train.py` | LSTM Autoencoder 학습 스크립트 |

## 요구사항

```bash
pip install torch tqdm onnx
```

---

## 1. 데이터

[Marine Cadastre](https://hub.marinecadastre.gov/pages/vesseltraffic) 에서 다운로드한 AIS CSV 파일을 사용합니다.

다운로드한 csv 파일을 `preprocess.py`의 `INPUT_FILE`에 경로를 지정하면 됩니다.

---

## 2. 전처리

### 사용법

```bash
python3 preprocess.py
```

### 설정

`preprocess.py` 상단에서 조정 가능

```python
INPUT_FILE   = "ais-2025-12-31.csv"   # 입력 파일 경로
OUTPUT_FILE  = "ais_preprocessed.csv"  # 출력 파일 경로
MIN_SEQ_LEN  = 10                      # MMSI별 최소 데이터 개수
SEQ_BREAK_DT = 3600                    # 시퀀스 분리 기준 시간 간격 (초)
```

### 입출력

| 구분 | 파일 | 설명 |
|---|---|---|
| 입력 | `ais-YYYY-MM-DD.csv` | 원본 AIS 데이터 |
| 출력 | `ais_preprocessed.csv` | 전처리된 데이터 |
| 출력 | `ais_skip_log.csv` | 제거된 MMSI 및 사유 |

### 출력 컬럼

| 컬럼 | 설명 |
|---|---|
| `mmsi` | 선박 고유 식별번호 |
| `base_date_time` | 신호 수신 시각 |
| `latitude` | 위도 |
| `longitude` | 경도 |
| `sog` | 대지속도 (노트) |
| `cog` | 대지침로 (도) |
| `heading` | 선수방향 (도, 511=미수신) |
| `status` | 항법 상태 (0=항해중, 1=정박, 5=계류 등) |
| `vessel_type` | 선종 코드 |
| `dt` | 이전 신호와의 시간 간격 (초) |
| `dist_km` | 이전 위치와의 거리 (km) |
| `cog_hdg_diff` | COG와 HDG의 차이 (도, -1=미정의) |

### 전처리 과정

1. **불필요한 컬럼 제거** — 9개 컬럼만 사용
2. **이상값 필터 (1차)** — 위도/경도 범위 초과, SOG 음수 행 즉시 제거
3. **정렬** — MMSI 숫자 순 → 시간 순
4. **최소 시퀀스 길이 필터** — MMSI별 10개 미만 전체 제거
5. **이상값 필터 (2차)** — MMSI 단위로 이상값 있으면 전체 제거
6. **결측값 처리** — forward fill, 앞값 없으면 기본값
7. **파생 피처 계산** — `dt`, `dist_km`, `cog_hdg_diff`
8. **위치 점프 MMSI 제거** — 최대 속도(50노트) + 20% 여유 초과 시 전체 제거

---

## 3. 학습

### 사용법

```bash
python3 train.py
```

### 설정

`train.py` 상단에서 조정 가능

```python
INPUT_FILE           = "ais_preprocessed.csv"  # 입력 파일
FEATURES             = ["sog", "cog", "dt", "dist_km"]  # 학습 피처
SEQ_LEN              = 10       # 시퀀스 길이
HIDDEN_SIZE          = 64       # LSTM hidden state 크기
NUM_LAYERS           = 2        # LSTM 레이어 수
BATCH_SIZE           = 256      # 배치 크기
EPOCHS               = 10       # 학습 반복 횟수
LR                   = 0.001    # 학습률
THRESHOLD_PERCENTILE = 95       # 이상 판정 임계값 백분위 (상위 5%)
SAMPLE_MMSI          = 500      # 샘플링할 MMSI 수 (None이면 전체)
SEQ_BREAK_DT         = 3600     # 시퀀스 분리 기준 (초)
```

### 입출력

| 구분 | 파일 | 설명 |
|---|---|---|
| 입력 | `ais_preprocessed.csv` | 전처리된 데이터 |
| 출력 | `model.pt` | 학습된 모델 (Python용) |
| 출력 | `model_scripted.pt` | TorchScript 모델 |
| 출력 | `model.onnx` | ONNX 모델 (C++ 추론용) |
| 출력 | `scaler.json` | 정규화 min/max 값 |
| 출력 | `threshold.txt` | 이상 판정 임계값 |

### 모델 구조

```
입력 [batch, 10, 4]
    ↓
Encoder LSTM → hidden state (정상 패턴 압축)
    ↓
Decoder LSTM → 시퀀스 복원
    ↓
Linear → 출력 [batch, 10, 4]

재구성 오차(MSE) > threshold → 이상
```

### 학습 원리

정상 데이터만으로 학습하여 정상 패턴을 기억합니다. 이상 신호가 들어오면 재구성 오차가 커지는 원리로 탐지합니다.

---

## 4. 플러그인 배포

학습 완료 후 아래 3개 파일을 플러그인 `data/` 폴더에 넣으면 됩니다:

```
ais_ids_pi/data/
    model.onnx
    scaler.json
    threshold.txt
```
