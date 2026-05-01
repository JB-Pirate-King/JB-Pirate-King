# JB-Pirate-King ML — AIS 이상 탐지 벤치마크

선박 AIS 데이터를 기반으로 한 비지도 이상 탐지 파이프라인.  
9종 알고리즘을 동일 조건에서 학습·평가하고, OR/가중 앙상블을 지원한다.

---

## 파일 구조

```
ml/
├── train_benchmark.py   # 학습: 모델별 .onnx / scaler / threshold 생성
├── eval_anomaly.py      # 평가: 탐지율·오탐율·피처 분석·앙상블 비교
└── README_ml.md         # 이 파일
```

---

## 빠른 시작

```bash
pip install torch onnx onnxruntime tqdm numpy
pip install scikit-learn   # iforest / ocsvm 사용 시

# 1. 학습
python train_benchmark.py --model dcdetect
python train_benchmark.py --model tranad --epochs 50

# 2. 평가
python eval_anomaly.py --model dcdetect
python eval_anomaly.py --weighted dcdetect tranad --weights 0.7 0.3 --target_fp 5.0
```

---

## 공통 설정

`train_benchmark.py` 상단에서 수정한다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `INPUT_FILE` | `ais-2025-12-31_preprocessed.csv` | 학습 데이터 경로 |
| `SEQ_LEN` | `10` | 슬라이딩 윈도우 크기 |
| `N_FEAT` | `12` | 입력 피처 수 |
| `SAMPLE_MMSI` | `10000` | 학습에 사용할 MMSI 수 (0이면 전체) |
| `THRESHOLD_PERCENTILE` | `95` | 임계값 기준 퍼센타일 (95 = 상위 5% 이상 판정) |
| `SEQ_BREAK_DT` | `600` | 이 시간(초) 이상 간격이면 새 세그먼트로 분리 |

하이퍼파라미터는 파일 내 `DEFAULTS` 딕셔너리에서 모델별로 수정한다.

```python
DEFAULTS = {
    "dcdetect": dict(epochs=30, lr=1e-3, batch_size=256, patience=5),
    "tranad":   dict(epochs=50, lr=1e-3, batch_size=256, patience=7),
    ...
}
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

## 지원 모델

### 1. USAD — KDD 2020
> Audibert et al., *"USAD: UnSupervised Anomaly Detection on Multivariate Time Series"*

두 Decoder(G1, G2)가 공유 Encoder를 두고 adversarial 학습.  
G1은 정상 재구성에 집중하고, G2는 G1의 오차를 증폭해 이상 시점 MSE를 더 크게 벌린다.

```
손실:
  L(θE,θG1) = (1/n)·MSE(G1(E(x)), x) + (1-1/n)·MSE(G2(G1(E(x))), x)
  L(θE,θG2) = (1/n)·MSE(G2(E(x)), x) − (1-1/n)·MSE(G2(G1(E(x))), x)
  n = epoch / total_epochs
```

---

### 2. TranAD — VLDB 2022
> Tuli et al., *"TranAD: Deep Transformer Networks for Anomaly Detection in Multivariate Time Series Data"*

두 Transformer Decoder(D1, D2)로 self-conditioning 학습.  
D1이 1차 재구성 → D2가 D1 출력을 조건으로 2차 재구성.  
학습 초반엔 재구성, 후반엔 adversarial 증폭(1-1/n 스케일)으로 이상 시점 MSE를 벌린다.  
window=10 단기 시퀀스를 염두에 두고 설계된 모델.

```
손실 (L1은 enc+D1, L2는 enc+D2 별도 optimizer):
  L1(θenc,θD1) = (1/n)·MSE(o1, x) + (1-1/n)·MSE(o2, x)
  L2(θenc,θD2) = (1/n)·MSE(o1, x) − (1-1/n)·MSE(o2, o1)
```

---

### 3. Conv1D Autoencoder — 2011
> Masci et al., *"Stacked Convolutional Auto-Encoders for Hierarchical Feature Extraction"* (ICANN 2011) 기반 시계열 변형

1D 합성곱으로 시퀀스의 지역적 패턴을 추출.  
kernel_size=3, same padding으로 시퀀스 길이를 유지하며 채널을 압축·복원.  
LSTM보다 빠르고 ONNX 변환이 안정적이며, 방향·속도의 급격한 단기 변화 탐지에 강하다.

```
Encoder: (B,T,F) → Conv1d(F→64) → BN → ReLU → Conv1d(64→32) → BN → ReLU
Decoder: ConvTranspose1d(32→64) → ReLU → ConvTranspose1d(64→F) → (B,T,F)
```

---

### 4. LSTM Autoencoder — 2015
> Srivastava et al., *"Unsupervised Learning of Video Representations using LSTMs"* (ICML 2015) Seq2Seq 구조 적용

Encoder LSTM이 시퀀스를 hidden state로 압축 → Decoder LSTM이 step-by-step으로 재구성.  
정상 패턴 학습 후 이상 시점에서 재구성 오차가 커지는 원리 이용.  
시퀀스 길이=10처럼 짧은 경우 장기 의존성 이점이 제한적이며 Conv1D/TCN 대비 성능이 낮을 수 있다.

---

### 5. TCN Autoencoder — 2018
> Bai et al., *"An Empirical Evaluation of Generic Convolutional and Recurrent Networks for Sequence Modeling"* (arXiv 2018)

Dilated Causal Convolution으로 receptive field를 지수적으로 확장.  
dilation=[1,2,4]이면 최대 7스텝 과거를 참조 가능.  
각 TCNBlock은 residual 연결로 gradient 소실을 방지.  
seq=10에서 Conv1D AE보다 다양한 시간 스케일 패턴 포착에 유리하다.

```
Encoder: Input Proj → TCNBlock(d=1) → TCNBlock(d=2) → TCNBlock(d=4)
Decoder: TCNBlock(d=4) → TCNBlock(d=2) → TCNBlock(d=1) → Output Proj
TCNBlock: Conv1d×2 + BN + ReLU + Dropout + residual
```

---

### 6. Anomaly Transformer — NeurIPS 2022
> Xu et al., *"Anomaly Transformer: Time Series Anomaly Detection with Association Discrepancy"*

**Association Discrepancy** 개념을 도입:  
- 정상 구간: 어텐션이 인접 시점에 집중 → Gaussian prior와 유사  
- 이상 구간: 어텐션이 분산되거나 편중 → prior와 크게 차이남  

이 KL 발산을 손실에 포함해 학습, 이상 시점의 재구성 오차가 더 커지게 유도.

```
loss = MSE(recon, x) − λ · (KL(prior‖series) + KL(series‖prior))
ONNX 추론: 재구성만 반환 (KL은 학습 전용)
```

---

### 7. DCdetector — KDD 2023
> Yang et al., *"DCdetector: Dual Attention Contrastive Representation Learning for Time Series Anomaly Detection"*

피처 간 관계(Channel-wise)와 시간 패턴(Patch-wise) 두 관점을 동시에 학습.  
정교하게 위장된 이상(Feature Smoothing, Shadow Vessel 등)처럼 단일 차원 분석으로 놓치기 쉬운 패턴 포착에 강하다.

```
1. Channel-wise Attention: Multi-head Attn(head=n_feat) → 피처 간 상관 패턴
2. Patchify: (B,T,F) → (B,n_patches, patch_size×F)  — seq=10, patch=2 → 5 patches
3. Patch-wise Attention: patch embedding → Multi-head Attn → 구간 간 패턴
4. Decoder: Linear → reshape → (B,T,F)
```

---

### 8/9. IsolationForest / OneClassSVM — 2008 / 2001

**IsolationForest** (Liu et al., ICDM 2008):  
랜덤 트리로 샘플을 고립시키는 횟수로 이상도 측정. 고립이 빠를수록 이상.  
트리 앙상블이라 빠르고 고차원에 강하다.

**One-Class SVM** (Schölkopf et al., NIPS 2001):  
정상 데이터의 결정 경계를 RBF 커널로 학습. 경계 밖 샘플을 이상으로 판정.  
데이터가 많으면 학습이 느리다.

**적용 방식 (sklearn-guided AE)**:  
`(B,T,F) → flatten → (B,T×F)` 벡터로 sklearn 학습 후,  
이상으로 판별된 하위 10% 샘플을 제거하고 나머지로 FlattenAE(MLP AE) 학습.  
ONNX는 FlattenAE 그대로 export해 eval_anomaly.py와 호환.

---

## 학습 명령어

```bash
# 단일 모델
python train_benchmark.py --model dcdetect
python train_benchmark.py --model tranad --epochs 100 --lr 0.0005
python train_benchmark.py --model conv1d --batch_size 512

# 전체 9개 순차 학습
python train_benchmark.py --model all --epochs 30

# 빠른 테스트 (SAMPLE_MMSI=100 으로 변경 후)
python train_benchmark.py --model all --epochs 5
```

출력 파일 (모델별):

```
model_{name}.onnx       eval_anomaly.py 호환 ONNX 모델
scaler_{name}.json      Min-Max 스케일러 파라미터
threshold_{name}.txt    이상 판정 임계값
```

---

## 평가 명령어

```bash
# 단일 모델 전체 분석 (탐지율 + 상관행렬 + 재구성 오차 + Permutation Importance)
python eval_anomaly.py --model dcdetect

# 분석 항목 선택
python eval_anomaly.py --model tranad --corr    # 피처 상관행렬만
python eval_anomaly.py --model tranad --recon   # 재구성 오차 분해만
python eval_anomaly.py --model tranad --perm    # Permutation Importance만

# OR 앙상블 (개별 임계값 기준, 오탐율 상승)
python eval_anomaly.py --ensemble conv1d tranad
python eval_anomaly.py --ensemble dcdetect tranad conv1d

# 가중 앙상블 (목표 오탐율 자동 맞춤)
python eval_anomaly.py --weighted dcdetect tranad --weights 0.7 0.3 --target_fp 5.0
python eval_anomaly.py --weighted dcdetect tranad conv1d --weights 0.6 0.2 0.2
```

---

## 평가 분석 항목

### 분석 1: 탐지율/오탐율 테이블
24개 이상 시나리오에 대한 탐지율과 임계값별(오탐율 1~10%) 스윕 테이블 출력.

| 시나리오 그룹 | 설명 |
|---|---|
| 기본 (정상, COG/HDG불일치, 정박이동 등) | 규칙 기반으로 정의 가능한 명확한 이상 |
| FN (False Negative 유발) | 기존 규칙 탐지기가 놓치도록 설계된 이상 |
| D (ML 우회 v1) | LowSlow, Temporal, GradDrift 등 ML 모델 회피 시도 |
| E (ML 우회 v2) | Smooth, Desync, Shadow 등 더 정교한 위장 |
| F (고급 공격) | FeatSmooth, TrajStitch, AISGap 등 구조적 공격 |

### 분석 2: 피처 간 상관행렬 (Pearson)
12개 피처 간 선형 상관관계. |r| ≥ 0.5 쌍 별도 출력.

### 분석 3: 시나리오별 재구성 오차 분해
시나리오별, 피처별 MSE를 히트맵으로 시각화.  
어떤 피처가 어떤 이상 패턴을 잡는지 파악 가능.

### 분석 4: Permutation Importance
- **4-A (정상 기준)**: 피처 셔플 시 정상 재구성 MSE 변화 → 모델이 어떤 피처로 정상을 학습하는지
- **4-B (이상 기준)**: 피처 셔플 시 이상 탐지 MSE 변화 → 실제 이상 탐지에 기여하는 피처

---

## 앙상블 전략

### OR 앙상블
두 모델 중 하나라도 임계값을 초과하면 이상 판정.  
오탐율이 올라가므로 `1-(1-fp1)×(1-fp2)` 사전 계산 권장.

### 가중 앙상블 (권장)
```
score = w1·MSE(model1) + w2·MSE(model2) + ...
```
정상 데이터의 `score` 분포에서 `target_fp` 퍼센타일로 임계값 자동 설정.  
오탐율 5%를 정확히 맞출 수 있다.

**벤치마크 결과 기준 최적 조합** (에포크 5 빠른 테스트):

| 조합 | 오탐율 | 특징 |
|---|---|---|
| DCdetect 단독 | 5% | E5-Shadow, F1-FeatSmooth 강함 |
| DCdetect + TranAD (0.7/0.3) | 5% | D4-Mimicry 보완, 전반적 균형 |
| DCdetect + TranAD + Conv1D (0.6/0.2/0.2) | 5% | FN3-COG경계 추가 커버 |

> 풀 학습(30~50 에포크) 후 결과가 달라질 수 있으므로 재벤치마크 권장.

---

## 출력 파일

| 파일 | 생성 위치 | 설명 |
|---|---|---|
| `model_{name}.onnx` | `ml/` | ONNX 모델 (OpenCPN 플러그인 연동) |
| `scaler_{name}.json` | `ml/` | Min-Max 스케일러 |
| `threshold_{name}.txt` | `ml/` | 이상 판정 임계값 |
| `eval_result_{name}.txt` | `ml/` | 평가 결과 전체 (터미널 + 파일 동시 출력) |
| `feature_correlation.png` | `ml/` | 피처 상관행렬 히트맵 |
| `reconstruction_error.png` | `ml/` | 시나리오별 재구성 오차 히트맵 |
| `permutation_importance.png` | `ml/` | Permutation Importance 막대 그래프 |
| `threshold_weighted_ensemble.txt` | `ml/` | 가중 앙상블 임계값 |