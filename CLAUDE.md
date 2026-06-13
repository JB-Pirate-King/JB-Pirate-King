# CLAUDE.md — JB-Pirate-King Project Context

## 🔁 세션 시작 프로토콜 (MANDATORY)

**새 세션은 반드시 아래 순서로 시작한다. 사용자 요청 전에 이 단계를 완료하라.**

```powershell
# 1. 부트스트랩 — 현황 즉시 파악
$env:PYTHONUTF8="1"
python ml/automation/bootstrap.py
```

이 명령 하나로:
- Obsidian SSOT(현재 작업·차기 할 일) 요약 출력
- 실행중/완료/정지 run 상태 확인
- `distribute_manifest.json` 감지 시 → 배포 대기 항목 자동 안내

**배포 대기 항목이 있으면 즉시 처리:**
```powershell
python ml/automation/bootstrap.py --distribute
# 출력된 지시에 따라 Sheets/Drive/Notion MCP 작업 수행
```

**세션 중 자주 참조:**
- 상태 파일: `D:\ais_output\ens24_v2\state.json`
- 인수인계: `C:\ObsidianVault\운영\실험\ens24_run_2026-06-04.md`
- 구글 시트: https://docs.google.com/spreadsheets/d/1uSF1FXsMvha24t0LpgNbI20MLumbq4lm1LbBtc14H1U

---

## Pre-Push Checklist

On code/structure change, keep ALL docs in sync. Before push (or when asked to push), check + update first:

1. **README.md / ml/README.md** — reflect changed features, paths, options.
2. **CLAUDE.md (this file)** — update so a fresh session grasps current state.
3. **Notion** — update methodology/results pages via `python ml/integrations/notify.py` (token in `ml/notify_config.json`, gitignored).
4. **Source code comments** — match current behavior of modified functions/classes.

> Rule: a code/structure change isn't "done" until README + CLAUDE.md + Notion reflect it.

> **Docs language: English.** All Markdown docs are written in English.

---

## Project Overview

AIS ship anomaly detection. OpenCPN plugin (C++) + ML pipeline (Python) + local server (Python/Docker).

---

## Directory Structure

```
JB-Pirate-King/
├── ml/                         # ML pipeline (training & evaluation)
│   ├── core/                   # Core ML logic
│   │   ├── pipeline.py         # Multi-model training / detection-rate comparison ★
│   │   ├── preprocess.py       # AIS preprocessing (.csv / .csv.zst / .zip)
│   │   ├── train_benchmark.py  # Unsupervised model training (9 models)
│   │   ├── eval_anomaly.py     # Detection-rate / false-positive evaluation
│   │   ├── feature_engineer.py # DCdetect feature engineering (Greedy + ONNX export)
│   │   └── patch_plugin.py     # C++ plugin auto-patch (scaler features → codegen) ★
│   ├── integrations/           # External integrations
│   │   ├── slack_bot.py        # Slack bot (logs, button approval, Claude queries)
│   │   ├── sheets.py           # Google Sheets logging
│   │   ├── notify.py           # Discord webhook + Notion reports
│   │   └── git_manager.py      # Auto branch creation / commit
│   ├── orchestrator.py         # LangGraph orchestrator (reco + per-node harness + gates) ★
│   ├── pipeline_steps.py       # Shared step library (run_cmd, stage_*, _fe_train_eval, parsers) ★
│   ├── reset_sheets.py         # Utility: clear all Google Sheets tabs (keep headers)
│   ├── fe_state.json           # FE starting features (initial_extra)
│   ├── build_plugin_wsl.sh     # WSL (Ubuntu-24.04) cmake+make package auto-build ★
│   ├── auto_feat_eng.py        # FE automation loop (dataset build → FE)
│   ├── build_3yr_dataset.py    # 2023–2025 balanced dataset builder
│   └── download_ais.py         # AIS raw data downloader
├── ais_ids_pi/                 # OpenCPN plugin (C++)
│   ├── src/ais_ids.cpp         # Plugin main source
│   ├── include/ais_ml.h        # ML interface (contains AUTO: codegen markers)
│   └── src/ais_ml.cpp          # ML inference impl (contains AUTO: codegen markers)
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
| `dcdetect` | Dual channel/patch attention contrastive learning ← **MAIN** |
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

### Base features (12, fixed)

`sog, cog, heading, status, dt, dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency, lat_speed, lon_speed`

SEQ_LEN = 10

### FE candidate features (CANDIDATE_FEATURES in feature_engineer.py)

| Feature | Computation |
|---|---|
| `sog_vec_kn` | GPS-derived speed — lat/lon_speed → km/s → knots |
| `lowspeed_crab` | Low-speed crab angle — cog_hdg_diff × max(0, 1-sog/3kn) |
| `cog_change` | COG change — \|COG(t) - COG(t-1)\| (deg) |
| `cog_move_diff` | COG vs actual heading-of-travel diff — error between AIS COG and lat/lon-derived travel angle |
| `dist_speed_err` | Distance/speed mismatch — \|dist_km/dt×3600 - sog×1.852\| |
| `dist_speed_ratio` | Distance/speed ratio — dist_km / (sog×dt converted) |
| `anchor_suspicion` | Anchor suspicion — low-speed + heading-change composite indicator |
| `speed_ratio` | Relative speed change rate — \|sog_change\| / max(sog, 0.5) |
| `anchored_excess_speed` | Excess speed while anchored — status∈{1,5,6} × max(0, sog-1.5kn) |
| `accel` | Acceleration — Δsog/dt (knots/s) |
| `heading_rate` | Heading change rate — Δheading/dt (°/s) |
| `heading_change` | Heading change — \|heading(t) - heading(t-1)\| |
| `vec_sog_diff` | Vector-SOG diff — magnitude diff after vector decomposition |

> The candidate pool is the fixed `CANDIDATE_FEATURES` set in `feature_engineer.py`. New candidates are added by editing that dict directly.

### Current adoption status (as of dcdetect_012)

Base 12 + extra 12 = **24 features**
Extra: `sog_vec_kn, lowspeed_crab, cog_change, cog_move_diff, dist_speed_err, dist_speed_ratio, accel, anchor_suspicion, heading_rate, heading_change, speed_ratio, anchored_excess_speed`
Best detection rate in this run series: dcdetect_011 → 83.5% (FP=1%, 23 features)

> **Official frozen numbers (final presentation)**: dcdetect **20 features (FE Iter8)** —
> FP=1% **91.9%** · FP=5% 97.6% · FP=10% 98.7%. Model backup: GitHub release
> `models/2026-06-05` + `D:\ais_models\dcdetect\`. Do NOT quote older numbers (83.5% etc.)
> in any presentation material. Single source of truth: `team-vault/발표 마스터플랜.md`.

---

## Pipeline Architecture

### Two execution paths

| Path | File | Use |
|---|---|---|
| Simple | `core/pipeline.py` | Train + eval only, no Slack/git. Fast experiments. |
| Full orchestrator | `orchestrator.py` | Slack gates + Sheets + git automation. Real operation. |

### Full orchestrator flow (FE-only branch chaining)

```
[preprocess (once on first branch; skipped with --skip_preprocess)]
   ↓
dcdetect_001: FE (Greedy adopt 1 feature) → retrain/eval adopted set → export → build·commit·release
   ↓ save fe_state, auto-chain
dcdetect_002: previous features + Greedy adopt 1 feature → ...
   ↓
... when no candidate adds ≥ +3.0pp objective score, converge → stop
```

- **No separate base Train/Eval stage**: baseline train+eval runs **inside FE (`feature_engineer.py`)**.
  The separate `stage_train`/`stage_eval` were removed (duplication + avoids `pipeline.py`'s 11 GB redundant scan).
- **Preprocess** (`core/preprocess.py`): raw AIS → derived features → CSV. **Only on the first branch** (skip with `--skip_preprocess`).
- **Feature engineering** (`core/feature_engineer.py`, `--max_steps 1`): **adopt 1 feature per run** via Greedy.
  - baseline train+eval (FP=1/5/10) → scan all candidates → adopt the single best with objective gain ≥ +3.0pp
  - on adoption: **retrain (model_best)** on that feature set → permutation importance + final FP1/5/10 + threshold → export for deployment
  - then plugin build → git commit → GitHub release → save fe_state → chain to a new branch

### Stability fixes (commit 723368a — 11 issues)

Autonomous chaining hardened. Fresh session must know:

- **Branch chaining accumulates in git history** (`git_manager.create_branch`): `dcdetect_NNN` branches **off the previous run branch** (`_002` base = `_001`), not `develop`. First run (or missing previous) branches off `develop`.
- **fe_state.json committed every run** (orchestrator): adoption history in git history, not an uncommitted working-tree file.
- **develop always restored** via `try/finally` in `main()` (exit, user stop, crash).
- **Slack `connect()` (not `start()`)**: avoids a global `signal.signal` monkeypatch leak breaking Ctrl+C; connection polled, not blind `sleep`.
- **Approval timeout** (`SlackPipelineBot.APPROVAL_TIMEOUT`, 3600s) → returns `stop`, no infinite hang.
- **Stale-button guard**: each approval carries a uuid token; old-message clicks ignored.
- **`--max_runs`** (default 50): cap against infinite chaining before convergence.
- **`--build_plugin`** gate (default off): WSL `tar.gz` build opt-in; canonical build is native Linux. Off → only C++ patch + model files committed.
- Subprocesses use `sys.executable` (not bare `python`); `det_rate == 0.0` no longer renders `?`; `run_cmd` buffers output with a bounded `deque`.

### Auxiliary systems

- **git**: each run (branch) auto-creates `dcdetect_001`, `dcdetect_002`... → commits on adoption → pushes to **project (upstream)**.
- **GitHub releases**: tag `run/dcdetect_NNN` (prerelease) — targeted at the commit SHA.
- **Google Sheets**: 5 tabs auto-logged (see section below).
- **Slack**: baseline result → per-candidate detection rate/objective score/adoption → retrain/final-eval/threshold reported live (`--auto_approve` for unattended).
- **Claude analysis**: after FE, calls the `claude -p` CLI to assess results.
- **fe_state.json**: stores the Greedy starting features for the next branch (`initial_extra`). Auto-updated on adoption.
- **Output**: metrics→Sheets, model→branch `ais_ids_pi/data` + release. FE intermediates go to `ml/.pipeline_tmp/` (gitignored). The D drive holds only inputs/cache.

## Orchestrator internals (Slack flow + LangGraph)

Slack 단계별 메시지·승인 흐름, claude 분석, LangGraph 노드·게이트·체크포인터 등 오케스트레이터 내부 상세는 skill **orchestrator-internals** (`.claude/skills/orchestrator-internals/SKILL.md`) 참조. 실행 명령은 아래 Key Pipeline Commands.

---

## Key Pipeline Commands

```bash
# Full orchestrator (real operation) — MUST run with the -m flag
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess

# Auto-approve (run while sleeping)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess --auto_approve

# Quick test (small data, fast convergence cap)
python -m ml.orchestrator --model dcdetect --epochs 1 --max_mmsi 50 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess --auto_approve --max_runs 3

# Simple train+eval (experiments)
python ml/core/pipeline.py --train --eval --models dcdetect --epochs 10

# Standalone FE
python ml/core/feature_engineer.py \
  --input D:\ais_data\preprocessed\ais_preprocessed_3yr.csv \
  --base_dir D:\ --max_mmsi 3000 --epochs 5 \
  --export_dir D:\ais_models\dcdetect
```

**When running the orchestrator from Claude Code**: launch with `run_in_background`. Slack handles reporting/approval. Check logs only on error. Do **not** issue git commits in this repo while a test is running (shared working tree → collision).

orchestrator.py defaults:
- `--model`: `dcdetect`
- `--epochs`: `5`
- `--max_mmsi`: `500` (specify `3000` to reuse the 3yr cache)
- `--data_file`: `D:/ais_data/preprocessed/2025/ais_preprocessed_2025.csv`
- `--base_dir`: `D:/`
- `--min_gain`: `3.0` (Greedy adoption threshold, objective-score gain)
- `--max_runs`: `50` (branch-chaining safety cap — prevents infinite looping before convergence)
- `--build_plugin`: off (when set, builds the tar.gz via WSL. **Default off** — canonical build is native Linux. When off, only the C++ patch + model files are committed)
- `--scan_ratio`: `1.0` (candidate-scan training subsample ratio, e.g. `0.4`. Baseline + all candidates train on the same seeded subsample for fair ranking; the adopted best is retrained on **full** data. ~2–3× faster scan, best pick usually unchanged.)
- `--n_anom`: unset → equals `max_mmsi` (anomaly sequences per scenario; larger = less sampling noise in detection rates)
- `--overall_tol`: `1.0` (adoption regression guard — reject a candidate whose objective rose but whose overall FP=1% detection drops > this many pp)

### Objective function (stability)

The Greedy objective is `mean_detection + 1.0 × weak_mean`, but detection per scenario is the **average over FP=1%/5%/10%** (`_combine_multifp`), not FP=1% alone. FP=1% is an extreme-tail (99th pct) threshold metric → tiny model changes cause large detection swings (the "들쑥날쑥"), which drives winner's curse and adopt-then-regress. Averaging across FP levels smooths the objective. A **regression guard** (`--overall_tol`) additionally rejects any adoption that improves the weak-weighted objective but drops overall FP=1% detection by more than the tolerance. Combined with per-call reseeding (`train_recon_model`), these stabilize selection.

### Branch chaining behavior (important)

- `dcdetect_NNN` branches **off the previous run branch** (e.g. `_002` base = `_001`).
  The first run (or when the previous branch is missing) branches off `develop`.
- Adoption history is stored in `ml/fe_state.json` and **committed every run** → accumulates in git history (no reliance on an uncommitted working-tree file).
- The main loop **restores `develop`** in a `finally` block on any path (normal / stop / crash).

---

## Feature Engineering

Greedy 1-feature 채택 알고리즘, FP=1% 평가 기준, 출력 JSON 키, 주요 옵션은 skill **feature-engineering** (`.claude/skills/feature-engineering/SKILL.md`) 참조.

---

## Google Sheets Tab Structure

5탭 자동 로깅(per-model 탭 prefix, 단일 마스터 시트 내 모델별 분리). 상세 탭·컬럼 스키마는 skill **sheets-logging** (`.claude/skills/sheets-logging/SKILL.md`) 참조. 설정: `ml/pipeline_config.json`(gitignored).

---

## Evaluation Scenarios (32 total)

| Group | Description |
|---|---|
| Basic (4) | COG/HDG mismatch, anchored movement, speed anomaly, position jump |
| FN (4) | Designed to evade rule-based detectors |
| D (4) | ML model evasion attempt, 1st gen |
| E (5) | ML model evasion attempt, 2nd gen |
| F (7) | Advanced attacks |
| G (7) | Novel scenarios |

---

## Model File Path Rules / Plugin Patch & Build

모델 파일 경로·런타임 로드 위치·배포 리네임, C++ `AUTO:` 패치 마커·`patch_plugin.py`, 네이티브 리눅스 빌드/배포는 skill **plugin-build** (`.claude/skills/plugin-build/SKILL.md`) 참조. (플러그인 빌드/배포는 네이티브 리눅스 전용.)

---

## Branch Strategy

- `main`: stable releases
- `develop`: main integration branch — work here
- `dcdetect_NNN`: per-run auto-created branch (created by the orchestrator, committed after FE; `_NNN` chains off `_NNN-1`)

---

## Release & Version Management

자동 run 릴리스(prerelease)·수동 안정 릴리스·버전 스킴·이력은 skill **release-management** (`.claude/skills/release-management/SKILL.md`) 참조.

---

## Environment

Two distinct environments — keep them separate:

**ML training (Windows)**
- Python 3.14 (Windows)
- Console encoding: cp949 — `sys.stdout.reconfigure(encoding='utf-8')` applied in pipeline.py
- GPU: Intel Arc B390 (iGPU, shared memory) — no CUDA, training on CPU

**Plugin build/deploy (native Linux)**
- Ubuntu 24.04 (noble). Windows/WSL is NOT the build target.
```
