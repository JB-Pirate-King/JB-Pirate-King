# ML 파이프라인 — AIS 이상 탐지

선박 AIS 데이터 기반 이상 탐지 파이프라인.  
2016~2025년 전체 기간 데이터를 자동 수집·전처리하여 비지도 앙상블 모델을 학습하고 평가한다.

---

## 디렉터리 구조

```
ml/
├── README.md
├── SELF_CHECK.md
│
├── # ── 데이터 수집 ──────────────────────────────
├── download_ais.py              # 단일 날짜 AIS CSV 다운로드 (marinecadastre.gov)
├── download_ais_allmonths.py    # 2016-2025 전 기간 자동 다운로드 (스트리밍/병렬)
├── download_watchdog.py         # 다운로드 자동복구 워치독 (크래시 재시작 + Discord 하트비트)
│
├── # ── 전처리 ───────────────────────────────────
├── preprocess.py                # AIS CSV → 12개 피처 정규화 (단일 파일)
├── parallel_preprocess.py       # 병렬 전처리 (다중 파일)
│
├── # ── 학습 ─────────────────────────────────────
├── train_benchmark.py           # 비지도 앙상블 학습 (TrANAD 등 9종)
├── train_supervised.py          # 지도 학습 (PatchTST / Mamba 등 5종)
├── train_runner.py              # 학습 러너 유틸리티
│
├── # ── 평가 ─────────────────────────────────────
├── eval_all.py                  # 전체 모델 일괄 평가 (ROC AUC / Bootstrap CI)
├── eval_anomaly.py              # 24개 이상 시나리오별 탐지율/오탐율 측정
├── scaling_compare.py           # 소규모/5년/11년 3-way 탐지율 비교
│
├── # ── 오케스트레이션 ───────────────────────────
├── watch_and_run_2c.py          # cache.pt 생성 감지 → 학습 → eval_all 자동 실행
├── phase2_orchestrator.py       # Phase2 전체 파이프라인 오케스트레이터
├── finish_2c.py                 # 2C 단계 마무리 처리
│
├── # ── 알림 / 유틸리티 ──────────────────────────
├── notify.py                    # Discord 상태 카드 알림
├── discord_command_bot.py       # Discord 명령 봇 (원격 파이프라인 제어)
├── gdrive_upload_helper.py      # Google Drive 자동 업로드
├── check_dml.py                 # DirectML GPU 가용성 확인
│
├── scripts/                     # PowerShell 자동화 스크립트
│   ├── run_pipeline.ps1
│   ├── run_pipeline_v2.ps1
│   ├── run_pipeline_v3.ps1
│   └── run_phase2_auto.ps1
│
├── data/                        # (gitignore) 학습 중간 산출물
└── output/                      # (gitignore) 모델/스케일러/임계값
```

---

## 전체 파이프라인

```
[marinecadastre.gov]
        │  download_ais_allmonths.py --stream --workers N
        ▼
[D:\AIS\YYYY\ais-YYYY-MM-DD.csv.zst]
        │  preprocess.py  (streaming: 다운→전처리→raw삭제)
        ▼
[D:\JB-Pirate-King-AIS\preprocessed_all\*_preprocessed.csv]
        │  train_benchmark.py --model tranad --cache ...
        ▼
[Pass 1: MMSI 수집] → [Pass 2: 6,000 MMSI 샘플 로드] → [cache.pt]
        │  GPU 학습 (DirectML / AMD Radeon RX 9060 XT)
        ▼
[D:\JB-Pirate-King-ML-Results\ensemble_full\model_tranad.onnx]
        │  eval_all.py  (테스트셋: 시간순 후미 10%)
        ▼
[eval_result_*.txt  +  Discord 최종 알림]
```

---

## 빠른 시작

### 1. 의존성 설치

```bash
pip install torch torch-directml onnx onnxruntime
pip install pandas pyarrow scikit-learn numpy requests
```

### 2. 설정 파일 생성

```json
// ml/notify_config.json  (gitignore 처리됨 — 직접 생성)
{
  "discord_webhook": "https://discord.com/api/webhooks/...",
  "discord_bot_token": "...",
  "discord_channel_id": "..."
}
```

### 3. 데이터 수집 + 전처리 (워치독 사용 권장)

```bash
# 워치독: 크래시 자동 재시작 + 20분 Discord 하트비트
python -u download_watchdog.py --workers 10 --max-restarts 10 --heartbeat-min 20

# 직접 실행
python download_ais_allmonths.py --stream --workers 6 --disk-guard-gb 80
```

### 4. 학습

```bash
# 비지도 앙상블 (TrANAD — GPU 필요)
python train_benchmark.py \
  --model tranad \
  --input  D:\JB-Pirate-King-AIS\preprocessed_all \
  --output D:\JB-Pirate-King-ML-Results\ensemble_full \
  --cache  D:\JB-Pirate-King-ML-Results\ensemble_full\train_data_cache.pt \
  --threshold-pct 99

# 지도 학습
python train_supervised.py --model moderntcn
```

### 5. 전자동 파이프라인 (권장)

```bash
# cache.pt 생성 감지 후 학습 → 평가 → Discord 알림 자동 실행
python watch_and_run_2c.py
```

---

## 입력 피처 (12개)

| 피처 | 설명 |
|---|---|
| `sog` | 대지 속력 (knot) |
| `cog` | 대지 침로 (°) |
| `heading` | 선수 방위 (°) |
| `status` | 항법 상태 코드 |
| `dt` | 직전 메시지와의 시간 간격 (초) |
| `dist_km` | 직전 위치와의 이동 거리 (km) |
| `cog_hdg_diff` | COG-Heading 차이 (°) |
| `sog_change` | 속력 변화량 |
| `cog_hdg_change` | COG-Heading 차이 변화량 |
| `speed_consistency` | 속력 대비 이동거리 일관성 |
| `lat_speed` | 위도 방향 이동 속도 (deg/s) |
| `lon_speed` | 경도 방향 이동 속도 (deg/s) |

---

## 비지도 모델 (`train_benchmark.py`)

정상 데이터만으로 학습 → 재구성 오차(MSE)로 이상 판정.

| 모델 | 설명 |
|---|---|
| `tranad` | TranAD — Transformer self-conditioning 재구성 |
| `usad` | USAD — 이중 디코더 adversarial 학습 |
| `conv1d` | Conv1D Autoencoder |
| `lstm` | LSTM Autoencoder (Seq2Seq) |
| `tcn` | TCN Autoencoder (Dilated Causal Conv) |
| `anomtrans` | Anomaly Transformer (Association Discrepancy) |
| `dcdetect` | DCDetector (채널/패치 이중 어텐션 대조 학습) |
| `iforest` | Isolation Forest |
| `ocsvm` | One-Class SVM (RBF 커널) |

주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--model` | `tranad` | 학습 모델 (쉼표 구분 복수 지정 가능) |
| `--input` | `./data` | 전처리 CSV 디렉터리 |
| `--output` | `./output` | 모델 출력 디렉터리 |
| `--cache` | — | `.pt` 캐시 경로 (지정 시 재실행 빠름) |
| `--threshold-pct` | `99` | 이상 임계값 백분위 (FPR ≈ 1%) |
| `--epochs` | `50` | 학습 에포크 수 |

---

## 지도 학습 모델 (`train_supervised.py`)

정상/이상 이진 분류. 이상은 합성 시나리오 사용.

| 모델 | 설명 |
|---|---|
| `patchtst` | PatchTST (패치 토큰 + Transformer) |
| `itrans` | iTransformer (피처=토큰 전치 어텐션) |
| `tsmixer` | TSMixer (시간/피처 축 MLP 교차) |
| `moderntcn` | ModernTCN (ConvNeXt 스타일 Depthwise Conv) |
| `mamba` | Mamba SSM (선택적 상태 공간 모델) |

---

## 평가 (`eval_all.py` / `eval_anomaly.py`)

- **데이터 분할**: 전체 파일을 시간순 정렬 후 후미 10%를 테스트셋으로 분리 (누수 없음)
- **지표**: ROC AUC, FPR 1% 동작점 탐지율, Bootstrap 95% CI
- **이상 시나리오**: 24종 (기본 4 / FN 4 / D 4 / E 5 / F 7 홀드아웃)

```bash
python eval_all.py \
  --input  D:\JB-Pirate-King-AIS\preprocessed_all \
  --output D:\JB-Pirate-King-ML-Results\ensemble_full \
  --test-files D:\JB-Pirate-King-ML-Results\ensemble_full\test_files.json
```

---

## 출력 파일

| 파일 | 설명 |
|---|---|
| `model_{name}.onnx` | 비지도 ONNX 모델 |
| `model_sup_{name}.onnx` | 지도 학습 ONNX 모델 |
| `scaler_{name}.json` | Min-Max 스케일러 파라미터 |
| `threshold_{name}.txt` | 이상 판정 임계값 |
| `train_data_cache.pt` | 데이터 로딩 캐시 (재실행 시 Pass 1/2 생략) |
| `test_files.json` | 테스트셋 파일 목록 (시간순 후미 분리) |
| `eval_result_{name}.json` | 평가 결과 (AUC / DR / CI) |

---

## 하드웨어 요건

| 항목 | 최소 | 권장 |
|---|---|---|
| GPU | DirectML 지원 | AMD Radeon RX 9060 XT 이상 |
| RAM | 16 GB | 32 GB |
| 저장공간 | 500 GB | 2 TB (2016-2025 전체 전처리 시) |
| Python | 3.10+ | 3.11 |
