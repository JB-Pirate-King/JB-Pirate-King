# ML 파이프라인 — AIS 이상 탐지

선박 AIS 데이터 기반 이상 탐지 파이프라인. 비지도 9종 + 지도 학습 5종 모델을 지원한다.

---

## 파일 구조

```
ml/
├── preprocess.py         # AIS CSV 전처리
├── train_benchmark.py    # 비지도 모델 학습 (9종)
├── train_supervised.py   # 지도 학습 모델 학습 (5종)
└── eval_anomaly.py       # 탐지율/오탐율 평가
```

---

## 빠른 시작

```bash
pip install torch onnx onnxruntime tqdm numpy
pip install scikit-learn   # iforest / ocsvm 사용 시

# 전처리
python preprocess.py

# 비지도 학습
python train_benchmark.py --model dcdetect

# 지도 학습
python train_supervised.py --model moderntcn
python train_supervised.py --model all --max_mmsi 300

# 평가
python eval_anomaly.py --model sup_moderntcn
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
```

출력: `model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`

---

## 지도 학습 모델 (`train_supervised.py`)

정상/이상 이진 분류 (BCEWithLogitsLoss). 정상은 CSV 실데이터, 이상은 합성 시나리오 사용.  
이상 시나리오 중 F1~F7(고급 공격)은 학습에서 제외하고 평가 전용 홀드아웃으로 사용한다.

| 모델 | 설명 |
|---|---|
| `patchtst` | PatchTST — 패치 토큰화 + Transformer + CLS 분류 헤드 |
| `itrans` | iTransformer — 피처=토큰, 시간=임베딩 차원으로 전치 후 어텐션 |
| `tsmixer` | TSMixer — 시간 축 MLP + 피처 축 MLP 교차 적용 |
| `moderntcn` | ModernTCN — ConvNeXt 스타일 대형 커널 Depthwise Conv |
| `mamba` | Mamba SSM — 선택적 상태 공간 모델 (ONNX 호환 for-loop 구현) |

```bash
python train_supervised.py --model moderntcn
python train_supervised.py --model all
python train_supervised.py --model all --max_mmsi 300 --n_anom 1000
```

주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--model` | `all` | 학습할 모델 (all / patchtst / itrans / tsmixer / moderntcn / mamba) |
| `--epochs` | 30 | 학습 에포크 수 |
| `--n_anom` | 500 | 시나리오당 이상 시퀀스 생성 수 |
| `--n_normal` | 15000 | 정상 시퀀스 최대 수 |
| `--max_mmsi` | 전체 | 학습에 사용할 최대 MMSI 수 |

출력: `model_sup_{name}.onnx`, `threshold_sup_{name}.txt`  
스케일러는 `scaler_dcdetect.json`을 공유 사용.

---

## 평가 (`eval_anomaly.py`)

24개 이상 시나리오(학습 시나리오 17개 + 홀드아웃 7개)에 대한 탐지율/오탐율 측정.

```bash
# 단일 모델
python eval_anomaly.py --model sup_moderntcn
python eval_anomaly.py --model dcdetect

# 앙상블
python eval_anomaly.py --ensemble conv1d tranad
python eval_anomaly.py --weighted dcdetect tranad --weights 0.7 0.3 --target_fp 5.0
```

시나리오 그룹:

| 그룹 | 설명 |
|---|---|
| 기본 (4종) | COG/HDG 불일치, 정박이동, 속도이상, 위치점프 |
| FN (4종) | 기존 규칙 탐지기 회피 설계 이상 |
| D (4종) | ML 모델 1차 회피 시도 (LowSlow, GradDrift 등) |
| E (5종) | ML 모델 2차 회피 시도 (Smooth, Shadow 등) |
| F (7종, 홀드아웃) | 고급 공격 — 학습 미포함, 평가 전용 |

---

## 출력 파일

| 파일 | 설명 |
|---|---|
| `model_{name}.onnx` | 비지도 ONNX 모델 |
| `model_sup_{name}.onnx` | 지도 학습 ONNX 모델 |
| `scaler_{name}.json` | Min-Max 스케일러 |
| `threshold_{name}.txt` | 이상 판정 임계값 |
| `eval_result_{name}.txt` | 평가 결과 |

---

## 플러그인 배포

학습 완료 후 아래 파일을 플러그인 `data/` 폴더에 넣는다.

```
ais_ids_pi/data/
    model.onnx        (또는 model_sup_moderntcn.onnx → model.onnx로 복사)
    scaler.json       (scaler_dcdetect.json → scaler.json으로 복사)
    threshold.txt     (threshold_sup_moderntcn.txt → threshold.txt로 복사)
```
