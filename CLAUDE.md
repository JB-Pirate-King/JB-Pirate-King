# CLAUDE.md — JB-Pirate-King Project Context

## Pre-Push Checklist

Whenever code or structure changes, keep ALL docs in sync. Before pushing code or when asked to push, always check and update the following first:

1. **README.md / ml/README.md** — Verify that any changed features, paths, or options are reflected in the docs.
2. **CLAUDE.md (this file)** — Update so a fresh session can immediately understand the current state.
3. **Notion** — Update the methodology/results pages. Use `python ml/integrations/notify.py` helpers (token in `ml/notify_config.json`, gitignored).
4. **Source code comments** — Verify that comments in modified functions/classes match current behavior.

> Rule of thumb: a code/structure change is not "done" until README + CLAUDE.md + Notion reflect it.

> **Docs language: English.** All Markdown docs and any prompt-as-file (e.g. `ml/ralph_feature_invention.md`) are written in English.

---

## Project Overview

AIS anomaly detection system for ships. Consists of an OpenCPN plugin (C++), an ML pipeline (Python), and a local server (Python/Docker).

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
│   ├── orchestrator.py         # Full pipeline entry point (Slack + Sheets + git)
│   ├── ralph_feature_invention.md # Ralph Loop prompt: autonomous feature invention (English)
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

> The candidate pool is **not fixed** — see [Ralph Loop](#ralph-loop-autonomous-feature-invention) for autonomous invention of new candidates.

### Current adoption status (as of dcdetect_012)

Base 12 + extra 12 = **24 features**
Extra: `sog_vec_kn, lowspeed_crab, cog_change, cog_move_diff, dist_speed_err, dist_speed_ratio, accel, anchor_suspicion, heading_rate, heading_change, speed_ratio, anchored_excess_speed`
Best detection rate: dcdetect_011 → **83.5% (FP=1%, 23 features)**

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

The autonomous chaining path was hardened. Key items a fresh session must know:

- **Branch chaining accumulates in git history** (`git_manager.create_branch`): `dcdetect_NNN` branches **off the previous run branch** (`_002` base = `_001`), not always `develop`. First run (or missing previous) branches off `develop`.
- **fe_state.json is committed every run** (orchestrator): adoption history travels in git history, not via an uncommitted working-tree file.
- **develop is always restored** via `try/finally` in `main()` (normal exit, user stop, or crash).
- **Slack `connect()` (not `start()`)**: avoids a global `signal.signal` monkeypatch leak that broke Ctrl+C; real connection is polled (not a blind `sleep`).
- **Approval timeout** (`SlackPipelineBot.APPROVAL_TIMEOUT`, 3600s) → returns `stop` to avoid hanging forever.
- **Stale-button guard**: each approval message carries a uuid token; clicks on old messages are ignored.
- **`--max_runs`** (default 50) safety cap to prevent infinite chaining before convergence.
- **`--build_plugin`** gate (default off): the WSL `tar.gz` build is opt-in; the canonical build is native Linux. When off, only the C++ patch + model files are committed.
- Subprocesses use `sys.executable` (not bare `python`); `det_rate == 0.0` no longer renders as `?`; `run_cmd` buffers output with a bounded `deque`.

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

## Ralph Loop: Autonomous Feature Invention

The orchestrator only **selects** from the fixed `CANDIDATE_FEATURES` pool; it never writes new feature code. The `ralph-loop` plugin closes that gap: it re-feeds a prompt file each iteration so Claude **invents new candidate features** and grows the pool until convergence.

- **Prompt file**: `ml/ralph_feature_invention.md` (English). Per iteration: pick a weak scenario (<50%) → form a physical hypothesis → add ONE `(desc, lambda)` to `CANDIDATE_FEATURES` → validate via standalone FE → keep (commit) if objective gain ≥ +3.0pp, revert if < 0 → log to `ml/.ralph_fe_log.md`. Completes at 3 adopted features with `<promise>RALPH_FE_DONE</promise>`.
- **Launch** (English prompt):
  ```
  /ralph-loop Execute the mission in ml/ralph_feature_invention.md exactly. Re-read that file at the start of every iteration and follow the procedure. --max-iterations 30 --completion-promise "RALPH_FE_DONE"
  ```
- **Windows hook**: the plugin Stop hook must use Git Bash (it needs `jq`/`perl`). The cached `hooks/hooks.json` is patched to `"C:/Program Files/Git/bin/bash.exe"`. **A hooks.json change requires a Claude Code restart** to take effect.
- **Do NOT run Ralph while the orchestrator test is running** — both share one working tree and touch git; commits collide. Ralph uses **standalone FE only** (no orchestrator, no branch chaining).

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

### Branch chaining behavior (important)

- `dcdetect_NNN` branches **off the previous run branch** (e.g. `_002` base = `_001`).
  The first run (or when the previous branch is missing) branches off `develop`.
- Adoption history is stored in `ml/fe_state.json` and **committed every run** → accumulates in git history (no reliance on an uncommitted working-tree file).
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

> Why per-tab, not per-spreadsheet: a service account on personal Gmail has **0 Drive quota** and cannot `gc.create()` new spreadsheets, so model separation is done by tab prefix inside the one master sheet (which is shared to the service account). Config in `ml/pipeline_config.json` (gitignored).

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

Training exports `model_{name}.onnx` / `scaler_{name}.json` / `threshold_{name}.txt`, but the plugin loads **fixed names** (fallback when no `ensemble_config.json`): `model.onnx` / `scaler.json` / `threshold.txt` (`ais_ids.cpp` `LoadMLFromConfig`). So the files must be **renamed** into the runtime load location:

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
