# CLAUDE.md — JB-Pirate-King Project Context

## Pre-Push Checklist

Whenever code or structure changes, keep ALL docs in sync. Before pushing code or when asked to push, always check and update the following first:

1. **README.md / ml/README.md** — Verify that any changed features, paths, or options are reflected in the docs.
2. **CLAUDE.md (this file)** — Update so a fresh session can immediately understand the current state.
3. **Notion** — Update the methodology/results pages. Use `python ml/integrations/notify.py` helpers (token in `ml/notify_config.json`, gitignored).
4. **Source code comments** — Verify that comments in modified functions/classes match current behavior.

> Rule of thumb: a code/structure change is not "done" until README + CLAUDE.md + Notion reflect it.

---

## Project Overview

AIS anomaly detection system for ships. Consists of an OpenCPN plugin (C++), ML pipeline (Python), and local server (Python/Docker).

---

## Directory Structure

```
JB-Pirate-King/
├── ml/                         # ML pipeline (training & evaluation)
│   ├── core/                   # Core ML logic
│   │   ├── pipeline.py         # Multi-model training/detection rate comparison ★
│   │   ├── preprocess.py       # AIS preprocessing (.csv / .csv.zst / .zip)
│   │   ├── train_benchmark.py  # Unsupervised model training (9 models)
│   │   ├── eval_anomaly.py     # Detection rate / false positive evaluation
│   │   ├── feature_engineer.py # DCdetect feature engineering (Greedy + ONNX export)
│   │   └── patch_plugin.py     # C++ 플러그인 자동 패치 (scaler features → codegen) ★
│   ├── integrations/           # External integrations
│   │   ├── slack_bot.py        # Slack bot (logs, button approval, Claude queries)
│   │   ├── sheets.py           # Google Sheets logging
│   │   ├── notify.py           # Discord webhook + Notion reports
│   │   └── git_manager.py      # Auto branch creation / commit
│   ├── orchestrator.py         # Full pipeline entry point (Slack + Sheets + git)
│   ├── fe_state.json           # FE 시작점 피처 저장 (initial_extra)
│   ├── build_plugin_wsl.sh     # WSL(Ubuntu-24.04) cmake+make package 자동 빌드 ★
│   ├── auto_feat_eng.py        # FE automation loop (dataset build → FE)
│   ├── build_3yr_dataset.py    # 2023–2025 balanced dataset builder
│   └── download_ais.py         # AIS raw data downloader
├── ais_ids_pi/                 # OpenCPN plugin (C++)
│   ├── src/ais_ids.cpp         # Plugin main source
│   ├── include/ais_ml.h        # ML 인터페이스 (AUTO: codegen 마커 포함)
│   └── src/ais_ml.cpp          # ML 추론 구현 (AUTO: codegen 마커 포함)
├── s-c/                        # Local server + GUI
└── aivdm_gen/                  # AIVDM test signal generator
```

---

## Data Paths (Local D Drive)

`--base_dir` defaults to `D:\`; everything lives under it.

```
D:\
├── ais_data\
│   ├── raw\
│   │   ├── 2023\                      # AIS_YYYY_MM_DD.zip (Marine Cadastre, old format)
│   │   ├── 2024\                      # .zip (old format)
│   │   └── 2025\                      # .csv.zst (new format)
│   └── preprocessed\
│       ├── 2025\
│       │   ├── daily\                 # Per-day preprocessed files
│       │   └── ais_preprocessed_2025.csv   # Yearly merged (pipeline default)
│       ├── _3yr_daily\                # Per-day temp files for the 3yr build
│       └── ais_preprocessed_3yr.csv   # 3-year balanced dataset (~10.9 GB)
├── ais_models\
│   └── {name}\                        # per-model dir (e.g. dcdetect\)
│       ├── model_{name}.onnx
│       ├── scaler_{name}.json
│       └── threshold_{name}.txt
└── ais_output\
    ├── pipeline\                      # comparison_TIMESTAMP.{txt,csv}, {model}_TIMESTAMP.csv
    └── feat_eng\ , feat_eng_iter\     # feature-engineering reports (JSON/txt)
```

### Input Format Support (core/preprocess.py)
- Accepts `.csv`, `.csv.zst` (2025+), and `.zip` (Marine Cadastre ≤2024, one CSV inside; corrupt zips are skipped with a warning).
- Normalizes old vs new column headers: `MMSI/BaseDateTime/LAT/LON` ↔ `mmsi/base_date_time/latitude/longitude`.
- Timestamps parsed with `datetime.fromisoformat`.

### Preprocessing Steps

```bash
# Step 1: Per-day preprocessing
python ml/core/preprocess.py D:\ais_data\raw\2025 --output_dir D:\ais_data\preprocessed\2025\daily

# Step 2: Yearly merge
python ml/core/preprocess.py "D:\ais_data\preprocessed\2025\daily\*_preprocessed.csv" ^
    --output D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
```

---

## ML Models

### Unsupervised (train_benchmark.py)

| Model | Description |
|---|---|
| `dcdetect` | Dual channel/patch attention contrastive learning ← **메인** |
| `usad` | Dual-decoder adversarial autoencoder |
| `tranad` | Transformer self-conditioning reconstruction |
| `conv1d` | 1D Conv Autoencoder |
| `lstm` | LSTM Seq2Seq Autoencoder |
| `tcn` | Dilated Causal Conv Autoencoder |
| `anomtrans` | Anomaly Transformer (association discrepancy) |
| `iforest` | Isolation Forest |
| `ocsvm` | One-Class SVM |

---

## Input Features

### 베이스 피처 (12개, 고정)

`sog, cog, heading, status, dt, dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency, lat_speed, lon_speed`

SEQ_LEN = 10

### FE 후보 피처 (CANDIDATE_FEATURES in feature_engineer.py)

| 피처 | 계산 방법 |
|---|---|
| `sog_vec_kn` | GPS유도 속력 — lat/lon_speed → km/s → knots |
| `lowspeed_crab` | 저속 crab각 — cog_hdg_diff × max(0, 1-sog/3kn) |
| `cog_change` | COG 변화량 — \|COG(t) - COG(t-1)\| (도) |
| `cog_move_diff` | COG vs 실이동방향 차이 — AIS COG와 lat/lon 기반 실이동각 오차 |
| `dist_speed_err` | 거리/속도 불일치 — \|dist_km/dt×3600 - sog×1.852\| |
| `dist_speed_ratio` | 거리/속도 비율 — dist_km / (sog×dt 환산값) |
| `anchor_suspicion` | 정박의심 — 저속+Heading변화 복합 지표 |
| `speed_ratio` | 상대 속도 변화율 — \|sog_change\| / max(sog, 0.5) |
| `anchored_excess_speed` | 정박 중 초과속력 — status∈{1,5,6} × max(0, sog-1.5kn) |
| `accel` | 가속도 — Δsog/dt (knots/s) |
| `heading_rate` | 선수변화율 — Δheading/dt (°/s) |
| `heading_change` | 선수각변화 — \|heading(t) - heading(t-1)\| |
| `vec_sog_diff` | 벡터SOG차이 — 벡터분해 후 크기 차이 |
| `cog_move_diff` | 실이동방향과 COG 차이 |

### 현재 채택 현황 (as of dcdetect_012)

베이스 12개 + 추가 12개 = **24피처**
추가 피처: `sog_vec_kn, lowspeed_crab, cog_change, cog_move_diff, dist_speed_err, dist_speed_ratio, accel, anchor_suspicion, heading_rate, heading_change, speed_ratio, anchored_excess_speed`
최고 탐지율: dcdetect_011 → **83.5% (FP=1%, 23피처)**

---

## Pipeline Architecture

### 두 가지 실행 경로

| 경로 | 파일 | 용도 |
|---|---|---|
| 단순 | `core/pipeline.py` | Slack/git 없이 학습+평가만. 빠른 실험용. |
| 풀 오케스트레이터 | `orchestrator.py` | Slack 게이트 + Sheets + git 자동화. 실제 운용. |

### 풀 오케스트레이터 흐름 (FE-only 브랜치 체이닝)

```
[전처리(첫 브랜치 1회, --skip_preprocess면 생략)]
   ↓
dcdetect_001: FE(Greedy 1피처 채택) → 채택셋 재학습/평가 → export → 빌드·커밋·릴리즈
   ↓ fe_state 저장, 자동 체이닝
dcdetect_002: 이전 피처 + Greedy 1피처 채택 → ...
   ↓
... 더 이상 목적점수 +3.0pp 채택 없으면 수렴 → 종료
```

- **베이스 Train/Eval 단계 없음**: 베이스라인 학습+평가는 **FE 내부(`feature_engineer.py`)에서 수행**.
  별도 `stage_train`/`stage_eval`은 제거됨 (중복 + `pipeline.py`의 11GB 중복 스캔 회피).
- **전처리** (`core/preprocess.py`): raw AIS → 파생 피처 → CSV. **첫 브랜치에서만** 1회 (`--skip_preprocess`로 생략).
- **피처 엔지니어링** (`core/feature_engineer.py`, `--max_steps 1`): run당 **Greedy 1피처 채택**.
  - 베이스라인 학습+평가(FP=1/5/10) → 후보 전체 탐색 → 목적점수 +3.0pp 이상 best 1개 채택
  - 채택 시: 그 피처셋으로 **재학습(model_best)** → 순열중요도 + 최종 FP1/5/10 + threshold → 배포 export
  - 이후 플러그인 빌드 → git 커밋 → GitHub 릴리즈 → fe_state 저장 → 새 브랜치 체이닝

### 부가 시스템

- **git**: run(브랜치)마다 `dcdetect_001`, `dcdetect_002`... 자동 생성 → 채택 시 커밋 → **project(upstream) push**
- **GitHub 릴리즈**: 태그 `run/dcdetect_NNN` (prerelease) — commit SHA 기준
- **Google Sheets**: 5개 탭 자동 기록 (아래 섹션 참고)
- **Slack**: 베이스라인 결과 → 후보별 탐지율·목적점수·채택여부 → 재학습/최종평가/threshold 실시간 보고 (`--auto_approve`로 무인)
- **Claude 분석**: FE 완료 후 `claude -p` CLI 호출 → 결과 평가
- **fe_state.json**: 다음 브랜치의 Greedy 시작 피처 저장 (`initial_extra`). 채택 시 자동 갱신.
- **출력**: 지표→Sheets, 모델→브랜치 `ais_ids_pi/data`+릴리즈. FE 중간물은 `ml/.pipeline_tmp/`(gitignore). D드라이브엔 입력/캐시만.

### Slack 메시지 & Claude 분석 / 승인

각 단계는 Slack에 다음 흐름으로 보고된다 (`integrations/slack_bot.py`):

```
📍 [1/2] ■□  *피처 엔지니어링 학습*  →  다음: 파이프라인 종료      ← log_stage_start
📊 베이스라인 (12피처): FP=1% 탐지율 40.6% · 목적점수 77.2          ← fe_progress (실시간)
⚠️ 약세 시나리오 5개 (베이스 탐지율<50%): D1-LowSlow(0%), F3(12%)...
🔬 후보 #1/20 `accel` 평가 중 — 가속도 Δsog/dt
   └ `accel`: 탐지율 43.6% (+3.0pp) · 목적점수 80.5 (+3.3) → ✅ 기준충족(≥+3.0)
🔬 후보 #2/20 `turn_rate` 평가 중 — COG 변화율
   └ `turn_rate`: 탐지율 33.6% (-6.7pp) · 목적점수 62.0 (-11.9) → ⬜ 미달
   ... (후보 20개)
🏆 채택 확정! `accel` — FP=1% 40.6% → 43.6% | 목적점수 +3.3
🔁 채택 피처셋(13개)으로 최종 모델 재학습 시작 (배포본)
🧠 최종 모델 학습 100% — Epoch 1/1 (train=0.019 val=0.005)
  ✅ 최종 모델 학습 완료 — 최적 검증 MSE 0.005150
📊 최종 모델 평가 중 (FP=1%/5%/10% + 시나리오별)...
📈 최종 탐지율 — FP=1%: 43.6% · FP=5%: 58.2% · FP=10%: 67.0%
🎯 배포 임계값(FP=1% 정상 99퍼센타일): 0.00491234
✅/❌ [피처개선 완료] + 후보 평가 결과표 + 피처 중요도표 (log_stage_result / log_table)
```

**Claude 분석** (`claude_analyze` → `claude -p`): 단계 종료 시 호출.
- 프롬프트: `[단계] 결과 분석 / 성공여부·소요시간 / 추가정보(JSON) / 실행출력 마지막 60줄`
  → **3가지를 200자 이내로**: ① 결과 평가(수치 해석) ② 원인·근거 ③ 다음 행동 추천 **`continue`/`retry`/`stop`** + 이유
- Slack 출력: `🤖 *Claude 분석*` + 답변 줄들. (`claude` CLI 없으면 "분석 불가" 표시)

**승인 게이트** (`_wait`) — 브랜치 내 단계별로:
- **게이트 ① FE 평가 후**: Claude 분석 + 후보표와 함께 → "배포 진행?" (✅진행 / 🔄FE재실행 / ❌중단)
- **게이트 ② 플러그인 빌드 후**: 빌드 결과 → "커밋 + 릴리즈 진행?" (✅진행 / ❌중단)
- **게이트 ③ 수렴 시**: 채택 없으면 "파이프라인 종료?" 확인
- `--auto_approve` **ON**: 모든 게이트 자동 통과 (요약에 `❌` 있으면 `stop`). Slack엔 `🤖 [auto_approve] … → approve` 로그만 → 무인 브랜치 체이닝
- **OFF**: 각 게이트마다 Slack 버튼 대기 → 단계별로 결과 보고 진행/중단 결정

---

## Key Pipeline Commands

```bash
# 풀 오케스트레이터 (실제 운용) — 반드시 -m 플래그로 실행
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess

# 자동 승인 (잠자는 동안 실행)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess --auto_approve

# 단순 학습+평가 (실험용)
python ml/core/pipeline.py --train --eval --models dcdetect --epochs 10

# FE 단독 실행
python ml/core/feature_engineer.py \
  --input D:\ais_data\preprocessed\ais_preprocessed_3yr.csv \
  --base_dir D:\ --max_mmsi 3000 --epochs 5 \
  --export_dir D:\ais_models\dcdetect
```

**Claude Code에서 orchestrator 실행 시**: `run_in_background`로 실행. Slack이 보고/승인 처리. 에러 시에만 로그 확인.

orchestrator.py defaults:
- `--model`: `dcdetect`
- `--epochs`: `5`
- `--max_mmsi`: `500` (3yr 캐시 재사용하려면 `3000` 명시)
- `--data_file`: `D:/ais_data/preprocessed/2025/ais_preprocessed_2025.csv`
- `--base_dir`: `D:/`
- `--min_gain`: `3.0` (Greedy 채택 임계 목적점수 향상량)

---

## Feature Engineering (`core/feature_engineer.py`)

Goal: find derived features that raise DCdetect detection rate, then export a deployable model.

### 알고리즘

**Greedy Forward Selection**:
1. 베이스라인 학습 (현재 피처셋) + 평가(FP=1/5/10)
2. 후보 피처 각각 추가 → 학습 → 목적점수 계산 (`전체평균 + 1.0 × 약세평균`)
3. 최고 목적점수 향상이 `--min_gain`(기본 3.0pp) 이상이면 채택
4. 채택된 피처셋으로 **재학습(model_best)** → 순열중요도 + 최종 FP1/5/10 + threshold → 배포 export
5. `--max_steps`로 한 호출당 채택 횟수 제한 (오케스트레이터는 `1` → run당 1피처, 브랜치 체이닝).
   미지정 시 수렴까지 반복.

> 오케스트레이터는 `--max_steps 1`로 호출 → run(브랜치)당 1피처만 채택하고, 채택 시 새 브랜치로
> 체이닝(dcdetect_001→002→...). 단독 실행(`feature_engineer.py` 직접)은 미지정 시 수렴까지 한 번에.

### 평가 기준 (FP = 1%)

- 임계값 = 홀드아웃 정상 시퀀스 점수의 **99퍼센타일** (상위 1%가 오탐됨)
- 탐지율 = 공격 시나리오 시퀀스 중 임계값 초과 비율
- FP=5%, FP=10% 기준도 동시 계산 (95/90퍼센타일 임계값)
- 배포 threshold = 이 FP=1% 임계값과 동일 → 현장에서도 동일한 오탐율 보장

### 출력 JSON 키

| 키 | 설명 |
|---|---|
| `best_extra` | 최종 채택된 추가 피처 목록 |
| `best_det` | FP=1% 최종 탐지율 (%) |
| `det_fp5` | FP=5% 최종 탐지율 (%) |
| `det_fp10` | FP=10% 최종 탐지율 (%) |
| `threshold` | 배포 임계값 (정상 점수 99퍼센타일) |
| `baseline_det` | FE 시작 전 탐지율 (%) |
| `scenario_fp1` | 시나리오별 FP=1% 탐지율 dict |
| `scenario_fp5/fp10` | 시나리오별 FP=5%/10% 탐지율 dict |
| `permutation_importance` | 피처 제거 시 탐지율 하락량 (음수 클수록 중요) |

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--input` | (필수) | 전처리 CSV |
| `--max_mmsi` | 500 | 학습 MMSI 수 상한 |
| `--epochs` | 5 | 에포크 수 |
| `--n_anom` | 200 | 시나리오당 이상 시퀀스 수 |
| `--min_gain` | 3.0 | 채택 최소 목적점수 향상(pp) |
| `--initial_extra` | [] | Greedy 시작 추가 피처 (fe_state.json에서 자동 로드) |
| `--export_dir` | — | 배포용 ONNX/scaler/threshold 저장 경로 |
| `--holdout_file` | — | FP 측정 전용 별도 파일 (학습 데이터와 완전 분리) |

---

## Google Sheets 탭 구조

Google Sheets에 5개 탭 자동 기록. 설정은 `ml/pipeline_config.json` (gitignored).

### 1. `dcdetect` 탭 — run별 상세 로그

| 컬럼 | 의미 |
|---|---|
| `branch` | 브랜치명 (▶ dcdetect_001 형식으로 run 구분) |
| `timestamp` | 기록 시각 |
| `stage` | 단계명 (RUN START / 피처 엔지니어링 학습 / RUN DONE) |
| `status` | 완료/실패/진행 중 |
| `det_change` | 탐지율 변화 (베이스라인→최종, 예: `56.6%→81.8%(+25.3pp)`) |
| `n_features` | 이번 run 최종 총 피처 수 |
| `adopted` | 이번 run에서 신규 채택된 피처명 |
| `threshold` | FP=1% 배포 임계값 |
| `elapsed_s` | 소요 시간 (초) |

### 2. `실행요약` 탭 — run 1줄 요약

| 컬럼 | 의미 |
|---|---|
| `timestamp` | run 시작 시각 |
| `branch` | 브랜치명 |
| `model` | 모델명 (dcdetect) |
| `epochs` | 학습 에포크 수 |
| `max_mmsi` | 학습에 사용한 선박 수 상한 |
| `data_file` | 학습 데이터 파일 경로 |
| `fe_steps` | 이번 run에서 새로 채택된 피처 수 |
| `fe_baseline` | FE 시작 전 탐지율 FP=1% (%) |
| `fe_det_fp1` | FE 최종 탐지율 FP=1% (%) |
| `fe_det_fp5` | FE 최종 탐지율 FP=5% (%) |
| `fe_det_fp10` | FE 최종 탐지율 FP=10% (%) |
| `fe_n_feat` | 최종 총 피처 수 |
| `fe_features` | 이번 run 누적 채택 피처 전체 목록 |
| `fe_threshold` | 배포 임계값 (FP=1% 정상 점수 99퍼센타일) |
| `notes` | 상태 (완료 / 수렴 완료) |

### 3. `상세로그` 탭 — 모든 단계 raw 로그

| 컬럼 | 의미 |
|---|---|
| `timestamp` | 기록 시각 |
| `branch` | 브랜치명 |
| `stage` | 단계명 |
| `status` | 완료/실패 |
| `det_rate` | FP=1% 탐지율 |
| `n_features` | 피처 수 |
| `threshold` | 임계값 |
| `elapsed_sec` | 소요 시간 (초) |
| `notes` | 채택 피처 등 메모 |

### 4. `시나리오결과` 탭 — 시나리오별 탐지율

| 컬럼 | 의미 |
|---|---|
| `timestamp` | 기록 시각 |
| `branch` | 브랜치명 |
| `model` | 모델명 |
| `fp_target` | FP 기준 (`FP=1%`) |
| `scenario` | 시나리오명 (Basic1, D2, FN3, F1, G2 ...) |
| `det_rate` | 해당 시나리오 탐지율 (%) |

### 5. `피처중요도` 탭 — 순열 중요도

| 컬럼 | 의미 |
|---|---|
| `timestamp` | 기록 시각 |
| `branch` | 브랜치명 |
| `fe_step` | FE 단계 (Step 1) |
| `feature` | 피처명 |
| `importance_pp` | 해당 피처 제거 시 탐지율 하락량 (pp). **음수가 클수록 중요.** 예: -20.9 = 제거 시 탐지율 20.9%p 하락 |
| `description` | 피처 설명 |

---

## Evaluation Scenarios (32 total)

| Group | Description |
|---|---|
| Basic (4) | COG/HDG mismatch, anchored movement, speed anomaly, position jump |
| FN (4) | Designed to evade rule-based detectors |
| D (4) | ML model evasion attempt 1st gen |
| E (5) | ML model evasion attempt 2nd gen |
| F (7) | Advanced attacks |
| G (7) | Novel scenarios |

---

## Model File Path Rules

- Trained (per model): `D:\ais_models\{name}\model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`
- Plugin: `ais_ids_pi/data/model.onnx`, `scaler.json`, `threshold.txt`

---

## Plugin Auto-Patch & Build

FE 피처 채택 시 orchestrator가 자동 실행. 수동 실행 시:

```bash
# 1. C++ 코드 패치 (dry_run으로 먼저 확인)
python ml/core/patch_plugin.py --scaler D:/ais_models/dcdetect/scaler_dcdetect.json --dry_run
python ml/core/patch_plugin.py --scaler D:/ais_models/dcdetect/scaler_dcdetect.json

# 2. Linux 빌드 (native Linux만 지원)
./local-build-package.sh   # from ais_ids_pi/
# 결과: ais_ids_pi-<version>-ubuntu-x86_64-24.04-noble.tar.gz
```

**AUTO: 마커 위치** (C++ 자동 패치 구간):
- `ais_ml.h`: `[AUTO:feat_block]` (ML_FEATURE_COUNT + 피처 주석), `[AUTO:push_decl]`
- `ais_ml.cpp`: `[AUTO:push_impl]`
- `ais_ids.cpp`: `[AUTO:extra_feats]`, `[AUTO:push_calls]`

---

## Plugin Build & Deploy (native Linux ONLY)

**The OpenCPN plugin is built and deployed on native Linux. Windows is used only for ML model training.**

- Target: Ubuntu 24.04 (noble)
- `ais_ids_pi/opencpn-libs/` is a git submodule. Before first build: `git submodule update --init --recursive`
- ONNX Runtime bundled at `ais_ids_pi/onnxruntime/{include,lib}`
- Build command (from `ais_ids_pi/`): `./local-build-package.sh`
- C++ feature count hardcoded: `ML_FEATURE_COUNT` in `ais_ids_pi/include/ais_ml.h`. Must match deployed model.

---

## Branch Strategy

- `main`: stable releases
- `develop`: main integration branch — work here
- `dcdetect_NNN`: run별 자동 생성 브랜치 (orchestrator가 생성, FE 완료 후 커밋)

---

## Release & Version Management

### Automated Run Releases (prerelease)

orchestrator가 FE 완료 시 자동 생성:
- 태그: `run/dcdetect_NNN` (prerelease)
- target: commit SHA (브랜치명 사용 시 422 오류)
- 첨부: 모델 3파일 (`model_dcdetect.onnx`, `scaler_dcdetect.json`, `threshold_dcdetect.txt`)
- 플러그인 tar.gz는 Linux에서만 빌드 가능 — 수동 첨부

### Stable Releases (수동)

```bash
git checkout main && git merge develop
git tag v1.0.0 && git push origin main --tags
gh release create v1.0.0 \
  --title "v1.0.0 — dcdetect 24피처" \
  --notes "..."
```

### Version Scheme

| Bump | When |
|---|---|
| **major** | 피처 수/인터페이스 변경 (12→N), SEQ_LEN 변경 |
| **minor** | 신규 모델, 평가 시나리오 추가, 탐지율 대폭 향상 |
| **patch** | 임계값 재조정, 버그 수정, 동일 구조 재학습 |

### Version History

| Version | Date | Notes |
|---|---|---|
| v0.1.0 | — | Initial release (conv1d, tranad, dcdetect, 1-day data) |
| v0.2.0 | 2026-05-22 | dcdetect 12피처, 3yr 데이터 |
| run/dcdetect_001~012 | 2026-05-29 | Greedy FE 자동화 runs (prerelease, 13~24피처) |

---

## Environment

Two distinct environments — keep them separate:

**ML training (Windows)**
- Python 3.14 (Windows)
- Console encoding: cp949 — `sys.stdout.reconfigure(encoding='utf-8')` applied in pipeline.py
- GPU: Intel Arc B390 (iGPU, shared memory) — no CUDA, training on CPU

**Plugin build/deploy (native Linux)**
- Ubuntu 24.04 (noble). Windows/WSL is NOT the build target.
