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
3. **Notion** — update methodology/results pages via `python ml/integrations/notify.py` (token in `ml/config/notify_config.json`, gitignored).
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
│   │   ├── slack_bot.py        # Slack bot (logs, button approval)
│   │   ├── sheets.py           # Google Sheets logging
│   │   ├── notify.py           # Discord webhook + Notion reports
│   │   └── git_manager.py      # Auto branch creation / commit
│   ├── scripts/                # Standalone CLI tools (run directly, not imported)
│   │   ├── auto_feat_eng.py    # FE automation loop (dataset build → FE)
│   │   ├── build_3yr_dataset.py# 2023–2025 balanced dataset builder
│   │   ├── download_ais.py     # AIS raw data downloader
│   │   └── reset_sheets.py     # Utility: clear all Google Sheets tabs (keep headers)
│   ├── config/                 # Config + state (secrets gitignored, example/state tracked)
│   │   ├── pipeline_config.json         # Slack tokens + Sheets creds path + sheet_id (gitignored)
│   │   ├── pipeline_config.example.json # Template (tracked)
│   │   ├── google_credentials.json      # GCP service account (gitignored)
│   │   ├── notify_config.json           # Discord/Notion tokens (gitignored)
│   │   └── fe_state.json                # FE starting features / initial_extra (tracked)
│   ├── orchestrator.py         # LangGraph orchestrator (reco + per-node harness + gates) ★
│   ├── pipeline_steps.py       # Shared step library (run_cmd, stage_*, _fe_train_eval, parsers) ★
│   └── build_plugin_wsl.sh     # WSL (Ubuntu-24.04) cmake+make package auto-build ★
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

### FE candidate features — fully dynamic (no static pool)

`CANDIDATE_FEATURES` in `feature_engineer.py` is **empty**. All candidates are invented
per run by the orchestrator `recommend` node: weak-scenario diagnosis → `claude -p`
proposes `--invent N` (default 5) new lambda features → validated (exec on dummy seq +
dedup) → written to `ml/dynamic_candidates.py` (gitignored) → `feature_engineer.py`
exec-loads them into the pool.

### Current adoption status

FE history reset after the LangGraph migration — runs restart from the base 12 features
(`fe_state.json` `initial_extra: []`, branch numbering back to `dcdetect_001`).
Prior runs (up to 24 features, best 83.5% FP=1%) are preserved in `run/dcdetect_NNN`
release tags and git history.

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

### Slack messages & Claude analysis / approval

Each stage reports to Slack in this flow (`integrations/slack_bot.py`):

```
📍 [1/2] ■□  *Feature Engineering*  →  next: pipeline end          ← log_stage_start
📊 baseline (12 feat): FP=1% detection 40.6% · objective 77.2      ← fe_progress (live)
⚠️ 5 weak scenarios (baseline <50%): D1-LowSlow(0%), F3(12%)...
🔬 candidate #1/20 `accel` — acceleration Δsog/dt
   └ `accel`: detection 43.6% (+3.0pp) · objective 80.5 (+3.3) → ✅ meets bar (≥+3.0)
🔬 candidate #2/20 `turn_rate` — COG change rate
   └ `turn_rate`: detection 33.6% (-6.7pp) · objective 62.0 (-11.9) → ⬜ below bar
   ... (20 candidates)
🏆 adopted! `accel` — FP=1% 40.6% → 43.6% | objective +3.3
🔁 retrain final model on adopted set (13 feat) — this is the deployable model
🧠 final training 100% — Epoch 1/1 (train=0.019 val=0.005)
  ✅ final training done — best val MSE 0.005150
📊 evaluating final model (FP=1%/5%/10% + per-scenario)...
📈 final detection — FP=1%: 43.6% · FP=5%: 58.2% · FP=10%: 67.0%
🎯 deploy threshold (FP=1% normal 99th pct): 0.00491234
✅/❌ [FE done] + candidate table + feature-importance table (log_stage_result / log_table)
```

**Claude analysis** (`claude_analyze` → `claude -p`): called at each stage end.
- Prompt: `[stage] result analysis / success·elapsed / extra info (JSON) / last 60 lines of run output`
  → returns three things: ① result assessment (numbers) ② cause/evidence ③ next action `continue`/`retry`/`stop` + reason.
- Slack output: `🤖 *Claude analysis*` + answer lines. (If the `claude` CLI is missing, shows "analysis unavailable".)

**Approval gates** (`_wait`) — per stage within a branch:
- **Gate ① after FE eval**: Claude analysis + candidate table → "proceed to deploy?" (✅proceed / 🔄rerun FE / ❌stop)
- **Gate ② after plugin build**: build result → "commit + release?" (✅proceed / ❌stop)
- **Gate ③ on convergence**: if nothing adopted, confirm "end pipeline?"
- `--auto_approve` **ON**: all gates auto-pass (if the summary contains `❌`, returns `stop`). Slack shows only a `🤖 [auto_approve] … → approve` log → unattended branch chaining.
- **OFF**: each gate waits on a Slack button.

---

## LangGraph Orchestrator (`ml/orchestrator.py`)

`orchestrator.py` is a LangGraph `StateGraph` (control flow); the heavy execution functions
live in `ml/pipeline_steps.py` (shared step library). Design diagram: `graph.md` / `pipeline_full.png`.

> **노드 단위 상세 문서**: [`ml/PIPELINE.md`](ml/PIPELINE.md) — 각 노드 설명, 라우팅, State,
> Mermaid + 자동 렌더 구조도(`pipeline_langgraph.png`). LangGraph 관점 입문용.

- **pipeline_steps.py** — `run_cmd`, output parsers, `claude_analyze`, `stage_preprocess`,
  `stage_build_plugin`, `stage_release`, `_fe_train_eval` (greedy 1-step train+eval+parse+log),
  `_fe_build_and_release`, `_fe_commit_release`, fe_state io, constants. Not an entry point.
- **FE decomposition**: `fe_baseline` node (`feature_engineer --diagnose_only` → baseline det +
  weak scenarios) is split from `fe_train` (scan→adopt→retrain→importance→finaleval→export, one
  `feature_engineer` subprocess via `_fe_train_eval`).
- **claude feature recommendation (`n_recommend`)**: weak-scenario diagnosis → `claude -p` proposes
  N new candidate features (name + lambda) → validated (exec on dummy seq + dedup) → written to
  `ml/dynamic_candidates.py` (gitignored) → `feature_engineer` loads + scans them. Convergence
  (no adoption) re-recommends from a different angle up to `--invent_rounds`. `--invent` defaults
  to **5** and is the ONLY candidate source — `CANDIDATE_FEATURES` is empty (no static pool).
- **per-node claude harness** (`claude_harness` factory): each compute node is followed by a harness
  node that runs `claude -p --output-format json` → `{assessment, verdict: continue|retry|stop, ...}`
  → routing. Toggle per node via `HARNESS_ON` set; `--no_harness` disables all.
- **interrupt() gates**: deploy / release / converge are independent `interrupt()` nodes. Because
  gates sit at node boundaries, a crash while awaiting Slack approval resumes **without retraining**.
- **Sheets**: `log_sheet(kind)` DRY factory (`run_start|fe|run_done|converge`).
- **Chaining = graph cycle**: `release → chain → new_branch`. Convergence → `converge → END`.
  `--max_runs` is a state-counter guard; `recursion_limit = max_runs × 25`.
- **Checkpointer**: `MemorySaver` (in-process). Swap to `SqliteSaver` for cross-restart resume.
- **Runner**: `run_pipeline` polls `__interrupt__`, gets the Slack decision via `bot.wait_approval`,
  resumes with `Command(resume=decision)`.
- **Launch**: `python -m ml.orchestrator` (same flags as before + `--invent`, `--invent_rounds`,
  `--no_harness`).
- **LangSmith tracing**: `orchestrator.py` auto-loads repo-root `.env` (gitignored) at import —
  set `LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` there and every
  node run/route is traced to smith.langchain.com with no code changes.

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
- Adoption history is stored in `ml/config/fe_state.json` and **committed every run** → accumulates in git history (no reliance on an uncommitted working-tree file).
- The main loop **restores `develop`** in a `finally` block on any path (normal / stop / crash).

---

## Feature Engineering (`core/feature_engineer.py`)

Goal: find derived features that raise the DCdetect detection rate, then export a deployable model.

### Algorithm

**Greedy Forward Selection**:
1. Train baseline (current feature set) + eval (FP=1/5/10).
2. Add each candidate → train → compute objective score (`overall_mean + 1.0 × weak_mean`).
3. If the best objective gain is ≥ `--min_gain` (default 3.0pp), adopt it.
4. On the adopted set, **retrain (model_best)** → permutation importance + final FP1/5/10 + threshold → export for deployment.
5. `--max_steps` caps adoptions per call (the orchestrator uses `1` → 1 feature per run, branch chaining).
   If unset, repeats until convergence.

> The orchestrator calls with `--max_steps 1` → adopts 1 feature per run (branch), then chains to a new branch (dcdetect_001→002→...). A direct standalone run (`feature_engineer.py`) without `--max_steps` runs to convergence in one go.

### Evaluation criterion (FP = 1%)

- Threshold = the **99th percentile** of holdout normal-sequence scores (top 1% are false positives).
- Detection rate = fraction of attack-scenario sequences exceeding the threshold.
- FP=5% and FP=10% are computed simultaneously (95/90th percentile thresholds).
- Deploy threshold = this FP=1% threshold → the same false-positive rate holds in the field.

### Output JSON keys

| Key | Description |
|---|---|
| `best_extra` | Final adopted extra features |
| `best_det` | FP=1% final detection rate (%) |
| `det_fp5` | FP=5% final detection rate (%) |
| `det_fp10` | FP=10% final detection rate (%) |
| `threshold` | Deploy threshold (99th pct of normal scores) |
| `baseline_det` | Detection rate before FE (%) |
| `scenario_fp1` | Per-scenario FP=1% detection dict |
| `scenario_fp5/fp10` | Per-scenario FP=5%/10% detection dicts |
| `permutation_importance` | Detection-rate drop when a feature is removed (more negative = more important) |

### Main options

| Option | Default | Description |
|---|---|---|
| `--input` | (required) | Preprocessed CSV |
| `--max_mmsi` | 500 | Cap on number of training MMSIs |
| `--epochs` | 5 | Number of epochs |
| `--n_anom` | 200 | Anomalous sequences per scenario |
| `--min_gain` | 3.0 | Minimum objective-score gain for adoption (pp) |
| `--initial_extra` | [] | Greedy starting extra features (auto-loaded from fe_state.json) |
| `--export_dir` | — | Path to save deployable ONNX/scaler/threshold |
| `--holdout_file` | — | Separate file for FP measurement (fully disjoint from training data) |

---

## Google Sheets Tab Structure

**Per-model tabs in a single master spreadsheet.** Each model gets its own set of tabs prefixed by model name: `{model}_실행요약`, `{model}_상세로그`, `{model}_시나리오결과`, `{model}_피처중요도`, plus a `{model}` detail tab. A `모델목록` (hub) tab lists every model with `=HYPERLINK` jump links to its tabs (click model → jump). Tabs are auto-created on first run for that model (`sheets.py` `_use`).

> Why per-tab, not per-spreadsheet: a service account on personal Gmail has **0 Drive quota** and cannot `gc.create()` new spreadsheets, so model separation is done by tab prefix inside the one master sheet (which is shared to the service account). Config in `ml/config/pipeline_config.json` (gitignored).

Column layout per tab (titles kept Korean for continuity):

### 1. `dcdetect` tab — per-run detail log

| Column | Meaning |
|---|---|
| `branch` | Branch name (run delimiter, e.g. `▶ dcdetect_001`) |
| `timestamp` | Log time |
| `stage` | Stage name (RUN START / Feature Engineering / RUN DONE) |
| `status` | done/failed/in-progress |
| `det_change` | Detection-rate change (baseline→final, e.g. `56.6%→81.8%(+25.3pp)`) |
| `n_features` | Final total feature count this run |
| `adopted` | Newly adopted feature(s) this run |
| `threshold` | FP=1% deploy threshold |
| `elapsed_s` | Elapsed time (s) |

### 2. `실행요약` (Run Summary) tab — one line per run

| Column | Meaning |
|---|---|
| `timestamp` | Run start time |
| `branch` | Branch name |
| `model` | Model name (dcdetect) |
| `epochs` | Training epochs |
| `max_mmsi` | Cap on ships used for training |
| `data_file` | Training data path |
| `fe_steps` | Features newly adopted this run |
| `fe_baseline` | FP=1% detection before FE (%) |
| `fe_det_fp1` | FP=1% final detection (%) |
| `fe_det_fp5` | FP=5% final detection (%) |
| `fe_det_fp10` | FP=10% final detection (%) |
| `fe_n_feat` | Final total feature count |
| `fe_features` | Full cumulative adopted-feature list this run |
| `fe_threshold` | Deploy threshold (FP=1% normal-score 99th pct) |
| `notes` | Status (done / converged) |

### 3. `상세로그` (Detail Log) tab — raw log of every stage

| Column | Meaning |
|---|---|
| `timestamp` | Log time |
| `branch` | Branch name |
| `stage` | Stage name |
| `status` | done/failed |
| `det_rate` | FP=1% detection rate |
| `n_features` | Feature count |
| `threshold` | Threshold |
| `elapsed_sec` | Elapsed (s) |
| `notes` | Memo (adopted features, etc.) |

### 4. `시나리오결과` (Scenario Results) tab — per-scenario detection

| Column | Meaning |
|---|---|
| `timestamp` | Log time |
| `branch` | Branch name |
| `model` | Model name |
| `fp_target` | FP target (`FP=1%`) |
| `scenario` | Scenario name (Basic1, D2, FN3, F1, G2 ...) |
| `det_rate` | Detection rate for that scenario (%) |

### 5. `피처중요도` (Feature Importance) tab — permutation importance

| Column | Meaning |
|---|---|
| `timestamp` | Log time |
| `branch` | Branch name |
| `fe_step` | FE step (Step 1) |
| `feature` | Feature name |
| `importance_pp` | Detection-rate drop when removed (pp). **More negative = more important.** e.g. -20.9 = removing it drops detection by 20.9pp |
| `description` | Feature description |

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

## Model File Path Rules

- Trained (per model): `D:\ais_models\{name}\model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`
- Plugin source (bundled into the build): `ais_ids_pi/data/model.onnx`, `scaler.json`, `threshold.txt`
- **Runtime load location** (`g_pData`, `ais_ids_pi.cpp:157`): `GetpPrivateApplicationDataLocation()/plugins/ais_ids_pi/data/` → on Linux `~/.opencpn/plugins/ais_ids_pi/data/`. `local-build-package.sh` copies `ais_ids_pi/data/` here (`DATA_DEST`), so the two paths match.

### Deploying a trained model to the plugin

Training exports `model_{name}.onnx` / `scaler_{name}.json` / `threshold_{name}.txt`, but the plugin loads **fixed names** (fallback when no `ensemble_config.json`): `model.onnx` / `scaler.json` / `threshold.txt` (`ais_ids.cpp` `LoadMLFromConfig`). Rename into the runtime load location:

```bash
DEST="$HOME/.opencpn/plugins/ais_ids_pi/data"
mkdir -p "$DEST"
cp model_{name}.onnx     "$DEST/model.onnx"
cp scaler_{name}.json    "$DEST/scaler.json"
cp threshold_{name}.txt  "$DEST/threshold.txt"
```

The orchestrator's `stage_build_plugin` does this rename-copy into `ais_ids_pi/data/`, and run-release notes embed the same `$HOME/.opencpn/...` deploy snippet. `local-build-package.sh` then installs `ais_ids_pi/data/` to the runtime location on a native-Linux build.

---

## Plugin Auto-Patch & Build

Run automatically by the orchestrator on FE adoption. Manual run:

```bash
# 1. Patch C++ code (dry_run first to inspect)
python ml/core/patch_plugin.py --scaler D:/ais_models/dcdetect/scaler_dcdetect.json --dry_run
python ml/core/patch_plugin.py --scaler D:/ais_models/dcdetect/scaler_dcdetect.json

# 2. Linux build (native Linux only)
./local-build-package.sh   # from ais_ids_pi/
# Output: ais_ids_pi-<version>-ubuntu-x86_64-24.04-noble.tar.gz
```

**AUTO: marker locations** (C++ auto-patch regions):
- `ais_ml.h`: `[AUTO:feat_block]` (ML_FEATURE_COUNT + feature comments), `[AUTO:push_decl]`
- `ais_ml.cpp`: `[AUTO:push_impl]`
- `ais_ids.cpp`: `[AUTO:extra_feats]`, `[AUTO:push_calls]`

---

## Plugin Build & Deploy (native Linux ONLY)

**The OpenCPN plugin is built and deployed on native Linux. Windows is used only for ML model training.**

- Target: Ubuntu 24.04 (noble)
- `ais_ids_pi/opencpn-libs/` is a git submodule. Before first build: `git submodule update --init --recursive`
- ONNX Runtime bundled at `ais_ids_pi/onnxruntime/{include,lib}`
- Build command (from `ais_ids_pi/`): `./local-build-package.sh`
- C++ feature count hardcoded: `ML_FEATURE_COUNT` in `ais_ids_pi/include/ais_ml.h`. Must match the deployed model.

> The orchestrator's `--build_plugin` flag (WSL build) is opt-in and **off by default** — the canonical plugin build is native Linux.

---

## Branch Strategy

- `main`: stable releases
- `develop`: main integration branch — work here
- `dcdetect_NNN`: per-run auto-created branch (created by the orchestrator, committed after FE; `_NNN` chains off `_NNN-1`)

---

## Release & Version Management

### Automated Run Releases (prerelease)

Auto-created by the orchestrator on FE completion:
- Tag: `run/dcdetect_NNN` (prerelease)
- Target: commit SHA (a branch name triggers a 422 error)
- Attachments: 3 model files (`model_dcdetect.onnx`, `scaler_dcdetect.json`, `threshold_dcdetect.txt`)
- The plugin tar.gz can only be built on Linux — attach manually

### Stable Releases (manual)

```bash
git checkout main && git merge develop
git tag v1.0.0 && git push origin main --tags
gh release create v1.0.0 \
  --title "v1.0.0 — dcdetect 24 features" \
  --notes "..."
```

### Version Scheme

| Bump | When |
|---|---|
| **major** | Feature-count/interface change (12→N), SEQ_LEN change |
| **minor** | New model, new eval scenarios, large detection-rate gain |
| **patch** | Threshold retune, bug fix, same-structure retrain |

### Version History

| Version | Date | Notes |
|---|---|---|
| v0.1.0 | — | Initial release (conv1d, tranad, dcdetect, 1-day data) |
| v0.2.0 | 2026-05-22 | dcdetect 12 features, 3yr data |
| run/dcdetect_001~012 | 2026-05-29 | Greedy FE automation runs (prerelease, 13–24 features) |

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
