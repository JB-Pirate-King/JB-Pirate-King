# ML Pipeline — AIS Anomaly Detection

An anomaly-detection pipeline over ship AIS data. Centered on the DCdetect model, it uses Greedy feature engineering to produce a deployable ONNX model.

---

## File Structure

```
ml/
├── core/                    # Core ML logic
│   ├── pipeline.py          # Multi-model training / detection-rate comparison ★
│   ├── preprocess.py        # AIS preprocessing (.csv / .csv.zst / .zip)
│   ├── train_benchmark.py   # Unsupervised model training (9 models)
│   ├── eval_anomaly.py      # Detection-rate / false-positive evaluation
│   ├── feature_engineer.py  # DCdetect Greedy FE + ONNX export ★
│   └── patch_plugin.py      # C++ plugin auto-patch
├── integrations/            # External integrations
│   ├── slack_bot.py         # Slack bot (logs, button approval, Claude queries)
│   ├── sheets.py            # Google Sheets logging
│   ├── notify.py            # Discord + Notion reports
│   └── git_manager.py       # Auto branch creation / commit
├── orchestrator.py          # Full pipeline entry point ★
├── orchestrator_lg.py       # LangGraph port (interrupt-gated HITL) ★
├── reset_sheets.py          # Utility: clear all Google Sheets tabs (keep headers)
├── fe_state.json            # FE starting features (initial_extra)
├── auto_feat_eng.py         # FE automation loop
├── build_3yr_dataset.py     # 2023–2025 balanced dataset builder
└── download_ais.py          # AIS raw data downloader
```

---

## Two Execution Paths

| Path | Entry | Use |
|---|---|---|
| Full orchestrator | `orchestrator.py` | Slack + Sheets + git automation. Real operation. |
| Simple train+eval | `core/pipeline.py` | Fast experiments, no Slack/git. |

```bash
# Full orchestrator (MUST use the -m flag)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess

# Unattended mode
python -m ml.orchestrator ... --auto_approve

# Quick test (small data + run cap)
python -m ml.orchestrator ... --epochs 1 --max_mmsi 50 --auto_approve --max_runs 3

# Simple train+eval
python ml/core/pipeline.py --train --eval --models dcdetect tranad conv1d --epochs 10

# Standalone FE
python ml/core/feature_engineer.py \
  --input D:\ais_data\preprocessed\ais_preprocessed_3yr.csv \
  --base_dir D:\ --max_mmsi 3000 --epochs 5 \
  --export_dir D:\ais_models\dcdetect
```

---

## Orchestrator Flow

```
[preprocess (once on first branch; skipped with --skip_preprocess)]
   ↓
dcdetect_001: FE (Greedy adopt 1 feature) → retrain/eval/threshold on adopted set → export
              → C++ patch · plugin build · commit · release → save fe_state
   ↓ auto-chain (off the previous run branch)
dcdetect_002 → 003 → ... → converge & stop when nothing is adopted
```

- **No separate base Train/Eval stage** — baseline train+eval runs inside FE (removes duplication + 11GB re-scan).
- **Adopt 1 feature per run** (`--max_steps 1`) → chain to a new branch.
- On adoption: **retrain** the adopted set → export deployable `.onnx`/`scaler.json`/`threshold.txt` → commit → push to project (upstream).
- Output: metrics→Sheets, model→branch + release, FE intermediates→`ml/.pipeline_tmp/` (gitignored).
- `dcdetect_NNN` branches **off `_NNN-1`**; `fe_state.json` is committed each run so history accumulates. The main loop restores `develop` in a `finally` on any exit path.
- Safety flags: `--max_runs` (default 50, chaining cap), `--build_plugin` (off by default; WSL tar.gz build is opt-in, canonical build is native Linux). See `CLAUDE.md` for the full flag list and the 11-issue stability hardening (commit 723368a).

---

## LangGraph Port (`orchestrator_lg.py`)

A LangGraph reimplementation of the orchestrator's control flow with the same behavior. The
pipeline is a `StateGraph`; the 3 HITL gates (FE-eval / build / converge) + fail gate are
independent nodes using `interrupt()`, so a crash while awaiting Slack approval resumes without
retraining. Branch chaining is a graph cycle (`release → chain → new_branch`). Same CLI flags as
`orchestrator.py` — run with `python -m ml.orchestrator_lg`. Details in `CLAUDE.md`.

---

## Data Paths (`--base_dir` default: `D:\`)

```
<base_dir>/
├── ais_data/
│   ├── raw/2023/, 2024/   # .zip (Marine Cadastre)
│   │   2025/              # .csv.zst
│   └── preprocessed/
│       ├── 2025/daily/    # per-day preprocessed output
│       ├── ais_preprocessed_2025.csv
│       └── ais_preprocessed_3yr.csv   # 3-year balanced (~10.9 GB)
├── ais_models/{name}/
│   ├── model_{name}.onnx
│   ├── scaler_{name}.json
│   └── threshold_{name}.txt
└── ais_output/
    ├── pipeline/           # comparison_TIMESTAMP.txt/.csv
    └── feat_eng/, feat_eng_iter/   # FE JSON/txt results
```

---

## Preprocessing (`core/preprocess.py`)

```bash
# Per-day preprocessing
python ml/core/preprocess.py D:\ais_data\raw\2025 --output_dir D:\ais_data\preprocessed\2025\daily

# Merge
python ml/core/preprocess.py "D:\ais_data\preprocessed\2025\daily\*_preprocessed.csv" ^
    --output D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
```

---

## Feature Engineering (`core/feature_engineer.py`)

### Algorithm — Greedy Forward Selection

1. Train baseline (train DCdetect on the current set + eval FP=1%/5%/10%).
2. Add each candidate and compute the objective score (`overall_mean + 1.0 × weak_mean`).
3. If the best gain is ≥ `--min_gain` (default 3.0pp), adopt it.
4. Retrain on the adopted set (model_best) → permutation importance + final FP1/5/10 + threshold → export for deployment.
5. `--max_steps` caps adoptions per call (orchestrator = `1`, branch chaining / unset → run to convergence).

### FP evaluation criteria

| Criterion | Threshold | Meaning |
|---|---|---|
| FP=1% | **99th** pct of normal scores | 1% of ships false-positive |
| FP=5% | **95th** pct of normal scores | 5% false positives allowed |
| FP=10% | **90th** pct of normal scores | 10% false positives allowed |

Deploy threshold = the FP=1% threshold → guarantees a ~1% false-positive rate in the field.

### Main options

| Option | Default | Description |
|---|---|---|
| `--input` | (required) | Preprocessed CSV |
| `--max_mmsi` | 500 | Cap on training ships |
| `--epochs` | 5 | Epochs |
| `--n_anom` | 200 | Anomalous sequences per scenario |
| `--min_gain` | 3.0 | Minimum Greedy adoption gain (pp) |
| `--initial_extra` | [] | Starting extra features (auto-loaded from fe_state.json) |
| `--export_dir` | — | Path to save the deployable model |
| `--holdout_file` | — | FP-measurement file (fully disjoint from training data) |

### Reading feature importance

`importance_pp`: detection-rate drop when the feature is removed (random-shuffled), in pp.
- **More negative = more important**: `-20.9pp` = without it, detection drops 20.9pp.
- Positive = removing it keeps (or slightly raises) detection → low importance.

---

## Input Features

### Base features (12, fixed)

| Feature | Description |
|---|---|
| `sog` | Speed over ground (knots, AIS raw) |
| `cog` | Course over ground (0–360°, AIS raw) |
| `heading` | Heading (0–360°, AIS raw) |
| `status` | Navigation status code (0=underway, 1=anchored, 5=moored ...) |
| `dt` | Elapsed time since previous message (s) |
| `dist_km` | Distance between consecutive lat/lon (km, Haversine) |
| `cog_hdg_diff` | COG-Heading angular difference (0–180°) |
| `sog_change` | Speed change (current–previous, knots) |
| `cog_hdg_change` | Change of cog_hdg_diff |
| `speed_consistency` | Consistency between SOG and distance/time speed |
| `lat_speed` | Latitude-direction speed component (°/s) |
| `lon_speed` | Longitude-direction speed component (°/s) |

SEQ_LEN = 10

### FE candidate pool — fully dynamic

`CANDIDATE_FEATURES` in `feature_engineer.py` is **empty** — there are no static candidates.
All candidates are invented per run by the orchestrator's `recommend` node (`claude -p`,
`--invent N`, default 5): weak-scenario diagnosis → N new lambda features → validated →
written to `ml/dynamic_candidates.py` (gitignored) → loaded by `feature_engineer.py` via exec.

FE runs restarted from scratch (base 12 features, `fe_state.json` reset) after the
LangGraph migration; prior adoption history lives in the `run/dcdetect_NNN` release tags.

---

## Models (`core/train_benchmark.py`)

| Model | Description |
|---|---|
| `dcdetect` | Channel/patch dual-attention contrastive learning ← **MAIN** |
| `usad` | Dual-decoder adversarial training |
| `tranad` | Transformer self-conditioning reconstruction |
| `conv1d` | 1D conv autoencoder |
| `lstm` | LSTM Seq2Seq autoencoder |
| `tcn` | Dilated causal conv autoencoder |
| `anomtrans` | Association discrepancy |
| `iforest` | Isolation Forest |
| `ocsvm` | One-Class SVM |

---

## Evaluation Scenarios (32 total)

| Group | Description |
|---|---|
| Basic (4) | COG/HDG mismatch, anchored movement, speed anomaly, position jump |
| FN (4) | Rule-based-detector evasion |
| D (4) | ML model evasion, 1st gen |
| E (5) | ML model evasion, 2nd gen |
| F (7) | Advanced attacks |
| G (7) | Novel scenarios |

---

## Google Sheets Tab Structure

**Per-model tabs in one master spreadsheet** (prefixed by model name):

| Tab (per model `m`) | Content |
|---|---|
| `m` | Per-run stage detail log (det_change, n_features, adopted, threshold, elapsed_s) |
| `m_실행요약` (Run Summary) | One line per run (fe_baseline, fe_det_fp1/fp5/fp10, fe_threshold, fe_features) |
| `m_상세로그` (Detail Log) | Raw log of every stage |
| `m_시나리오결과` (Scenario Results) | Per-scenario detection rate (FP=1%, 32 scenarios) |
| `m_피처중요도` (Feature Importance) | Per-feature permutation importance (importance_pp, more negative = more important) |
| `모델목록` (Hub) | Index of all models with `=HYPERLINK` jump links to each model's tabs |

Tabs are auto-created on a model's first run. A service account on personal Gmail can't create new spreadsheets (0 Drive quota), so models are separated by tab prefix within the single shared master sheet, not separate files. Full column descriptions in `CLAUDE.md`.

---

## Plugin Deployment

```
ais_ids_pi/data/
    model.onnx        ← copy of model_dcdetect.onnx
    scaler.json       ← copy of scaler_dcdetect.json
    threshold.txt     ← copy of threshold_dcdetect.txt
```

Build/deploy procedure: see the "Plugin Build & Deploy" section of `CLAUDE.md`.
