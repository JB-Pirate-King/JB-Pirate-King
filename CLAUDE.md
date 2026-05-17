# CLAUDE.md — JB-Pirate-King 프로젝트 컨텍스트

## 프로젝트 개요

선박 AIS 신호 기반 이상 탐지 시스템. OpenCPN 플러그인(C++), ML 파이프라인(Python), 로컬 서버(Python/Docker)로 구성.

---

## 디렉토리 구조

```
JB-Pirate-King/
├── ml/                   # ML 파이프라인 (학습 · 평가)
│   ├── pipeline.py       # 멀티모델 학습/탐지율 비교 파이프라인 ★
│   ├── preprocess.py     # AIS CSV 전처리 (.csv.zst 지원)
│   ├── train_benchmark.py# 비지도 모델 학습 (9종)
│   ├── eval_anomaly.py   # 탐지율/오탐율 평가
│   ├── compare_models.py # 모델 비교 도구
│   ├── run_pipeline.py   # 파이프라인 실행 스크립트
│   ├── download_ais.py   # AIS 데이터 다운로드
│   ├── eval_rule_gen.py  # 룰 기반 평가 생성
│   └── deploy/           # 배포용 모델/스케일러/임계값
├── ais_ids_pi/           # OpenCPN 플러그인 (C++)
│   └── src/ais_ids.cpp   # 플러그인 메인 소스
├── s-c/                  # 로컬 서버 + GUI
├── aivdm_gen/            # AIVDM 테스트 신호 생성기
└── scripts/
```

---

## 데이터 경로 (로컬 D 드라이브)

```
D:\ais_data\
├── raw\
│   └── 2025\             # 원본 AIS .csv.zst 파일 (172개)
└── preprocessed\
    └── 2025\
        └── ais_preprocessed_2025.csv  # 전처리 완료 파일 (870 MB)
```

---

## ML 모델

### 비지도 (train_benchmark.py)
| 모델 | 설명 |
|---|---|
| `usad` | 이중 디코더 adversarial |
| `tranad` | Transformer self-conditioning |
| `conv1d` | 1D Conv Autoencoder |
| `lstm` | LSTM Seq2Seq |
| `tcn` | Dilated Causal Conv |
| `anomtrans` | Anomaly Transformer |
| `dcdetect` | 채널/패치 이중 어텐션 |
| `iforest` | Isolation Forest |
| `ocsvm` | One-Class SVM |

### 지도 (train_supervised.py — develop에서 제거됨)
patchtst, itrans, tsmixer, moderntcn, mamba

---

## 입력 피처 (12개)

`sog, cog, heading, status, dt, dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency, lat_speed, lon_speed`

SEQ_LEN = 10

---

## 파이프라인 주요 명령

```bash
# 전처리 (연도별)
python ml/preprocess.py D:\ais_data\raw\2025 --output D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv

# 멀티모델 학습 + 평가
python ml/pipeline.py --train --eval --models conv1d tranad dcdetect --epochs 10 --n_anom 200 --fp_targets 1

# 이미 학습된 모델 건너뛰기
python ml/pipeline.py --train --eval --models conv1d tranad dcdetect --skip_trained

# 평가만
python ml/pipeline.py --eval --models conv1d dcdetect
```

pipeline.py 기본 경로:
- `--raw_data`: `D:\ais_data\raw\2025`
- `--data_file`: `D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv`

---

## 평가 시나리오 (32개)

| 그룹 | 설명 |
|---|---|
| 기본 (4종) | COG/HDG 불일치, 정박이동, 속도이상, 위치점프 |
| FN (4종) | 기존 룰 탐지기 회피 |
| D (4종) | ML 모델 1차 회피 |
| E (5종) | ML 모델 2차 회피 |
| F (7종, 홀드아웃) | 고급 공격 — 학습 미포함 |
| G (7종, 홀드아웃) | 신규 홀드아웃 |

---

## 모델 파일 경로 규칙

- 비지도 모델: `ml/model_{name}.onnx`, `ml/scaler_{name}.json`, `ml/threshold_{name}.txt`
- 배포용: `ml/deploy/model.onnx`, `ml/deploy/scaler.json`, `ml/deploy/threshold.txt`
- 플러그인: `ais_ids_pi/data/model.onnx`, `scaler.json`, `threshold.txt`

---

## 브랜치 전략

- `main`: 안정 릴리즈
- `develop`: 개발 통합 브랜치 ← 주로 여기에 작업
- `claude/*`: Claude Code 작업 브랜치 (작업 후 develop으로 머지)

---

## 환경

- Python 3.14 (Windows)
- 콘솔 인코딩: cp949 → pipeline.py에 `sys.stdout.reconfigure(encoding='utf-8')` 적용
- GPU: Intel Arc B390 (iGPU, 공유 메모리) — CUDA 없음, torch-directml 미설치
- 학습은 CPU로 실행 중
