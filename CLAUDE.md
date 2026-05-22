# CLAUDE.md — JB-Pirate-King Project Context

## Pre-Push Checklist

Whenever code or structure changes, keep ALL docs in sync. Before pushing code or when asked to push, always check and update the following first:

1. **README.md / ml/README.md** — Verify that any changed features, paths, or options are reflected in the docs. Update outdated content before pushing.
2. **CLAUDE.md (this file)** — Update so a fresh session can immediately understand the current state: new scripts/options, changed workflows, version history, feature count, paths.
3. **Notion** — Update the methodology/results pages so external readers stay current. Use `python ml/notify.py` helpers (token in `ml/notify_config.json`, gitignored). Parent + results page IDs are in that config.
4. **Source code comments** — Verify that comments in modified functions/classes match current behavior. Remove or fix any stale comments.

> Rule of thumb: a code/structure change is not "done" until README + CLAUDE.md + Notion reflect it.

---

## Project Overview

AIS anomaly detection system for ships. Consists of an OpenCPN plugin (C++), ML pipeline (Python), and local server (Python/Docker).

---

## Directory Structure

```
JB-Pirate-King/
├── ml/                    # ML pipeline (training & evaluation)
│   ├── pipeline.py        # Multi-model training/detection rate comparison ★
│   ├── preprocess.py      # AIS preprocessing (.csv / .csv.zst / .zip; old & new column formats)
│   ├── train_benchmark.py # Unsupervised model training (9 models)
│   ├── eval_anomaly.py    # Detection rate / false positive evaluation
│   ├── compare_models.py  # Model comparison tool
│   ├── run_pipeline.py    # Pipeline runner script
│   ├── download_ais.py    # AIS data downloader
│   ├── eval_rule_gen.py   # Rule-based evaluation generator
│   └── deploy/            # Deployment model/scaler/threshold files
├── ais_ids_pi/            # OpenCPN plugin (C++)
│   └── src/ais_ids.cpp    # Plugin main source
├── s-c/                   # Local server + GUI
└── aivdm_gen/             # AIVDM test signal generator
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

### Input Format Support (preprocess.py)
- Accepts `.csv`, `.csv.zst` (2025+), and `.zip` (Marine Cadastre ≤2024, one CSV inside; corrupt zips are skipped with a warning).
- Normalizes old vs new column headers (case/underscore-insensitive): `MMSI/BaseDateTime/LAT/LON/VesselType/Status` ↔ `mmsi/base_date_time/latitude/longitude/vessel_type/status`.
- Timestamps parsed with `datetime.fromisoformat` (handles both `YYYY-MM-DD HH:MM:SS` and ISO `...T...`).

### Preprocessing Steps

```bash
# Step 1: Per-day preprocessing (a dir mixes .csv/.csv.zst/.zip automatically)
python ml/preprocess.py D:\ais_data\raw\2025 --output_dir D:\ais_data\preprocessed\2025\daily

# Step 2: Yearly merge
python ml/preprocess.py "D:\ais_data\preprocessed\2025\daily\*_preprocessed.csv" ^
    --output D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
```

For the 3-year balanced dataset use `build_3yr_dataset.py` (see Feature Engineering section), which calls preprocess.py per selected day across 2023–2025.

---

## ML Models

### Unsupervised (train_benchmark.py)

| Model | Description |
|---|---|
| `usad` | Dual-decoder adversarial autoencoder |
| `tranad` | Transformer self-conditioning reconstruction |
| `conv1d` | 1D Conv Autoencoder |
| `lstm` | LSTM Seq2Seq Autoencoder |
| `tcn` | Dilated Causal Conv Autoencoder |
| `anomtrans` | Anomaly Transformer (association discrepancy) |
| `dcdetect` | Dual channel/patch attention contrastive learning |
| `iforest` | Isolation Forest |
| `ocsvm` | One-Class SVM |

### Supervised (train_supervised.py — removed from develop)
patchtst, itrans, tsmixer, moderntcn, mamba

---

## Input Features (12)

`sog, cog, heading, status, dt, dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency, lat_speed, lon_speed`

SEQ_LEN = 10

---

## Key Pipeline Commands

```bash
# Per-day preprocess then merge (see steps above)

# Multi-model training + evaluation
python ml/pipeline.py --train --eval --models conv1d tranad dcdetect --epochs 10 --n_anom 200 --fp_targets 1

# Skip already-trained models
python ml/pipeline.py --train --eval --models conv1d tranad dcdetect --skip_trained

# Evaluation only
python ml/pipeline.py --eval --models conv1d dcdetect

# Use a different base directory (default: D:\)
python ml/pipeline.py --train --eval --models conv1d --base_dir E:\
```

pipeline.py defaults:
- `--base_dir`: `D:\` — all model files and output go under this root
- `--raw_data`: `D:\ais_data\raw\2025`
- `--data_file`: `D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv`

---

## Feature Engineering (`feature_engineer.py` + `auto_feat_eng.py`)

Goal: find derived features that raise DCdetect detection rate, then export a deployable model trained on that exact feature set.

- **`feature_engineer.py`** — one FE pass: Greedy Forward Selection (add a candidate feature only if detection rate gains ≥ `--min_gain`, default 3.0pp) + Permutation Importance on the best set.
  - `--export_dir DIR` — after selection, trains `model_best` on the best feature set and writes deployable **`model_dcdetect.onnx` / `scaler_dcdetect.json` (records `features`/`min`/`max`) / `threshold_dcdetect.txt`** to DIR. The scaler `features` array is the authoritative feature order — the C++ plugin must match it.
  - Other opts: `--max_mmsi`, `--epochs`, `--n_anom`, `--weak_floor`/`--weak_weight` (up-weight weak scenarios in the objective), `--max_feat` (cap; 16 keeps nhead=8), `--initial_extra` (start set), `--out_json`.
  - **Sequence cache**: first load of the big CSV is pickled to `<input>.s<max_mmsi>_seed<SEED>_…seqs.pkl`; later runs/iterations reuse it instead of re-parsing. Deterministic (fixed SEED).
- **`auto_feat_eng.py`** — automation loop: (optionally wait for downloads →) build 3yr dataset → run `feature_engineer.py` up to `--max_iter` times, chaining each iteration's best set as the next `--initial_extra`, stopping when no new feature is adopted. Sends Discord + Notion per-iteration reports. Defaults: `MAX_MMSI=3000, EPOCHS=5, N_ANOM=150, MAX_ITER=5, EXPORT_DIR=D:\ais_models\dcdetect`.
  - Run: `python ml/auto_feat_eng.py --no_wait --skip_build` (dataset already built). Use `PYTHONUNBUFFERED=1` for live logs.

### 3-Year Balanced Dataset (`build_3yr_dataset.py`)
- Output: `D:\ais_data\preprocessed\ais_preprocessed_3yr.csv` (~10.9 GB).
- Purpose: avoid confirmation bias from a single day/season. Picks N days/month across 2023–2024–2025.
- **MMSI sampling (in `load_raw_seqs`)**: each MMSI is bucketed by its dominant `YYYY-MM`; `max_mmsi` is split evenly across active months (random within bucket, fixed SEED), so no year/season dominates. Then sequences (SEQ_LEN=10, split on `dt ≥ 600s`) capped at 500/MMSI.

### Current feature status
- Base = 12 features. FE validated 16 = 12 base + `accel, heading_rate, vec_sog_diff, heading_change` (~88.8%); adding more (e.g. `lowspeed_crab`) did not help.
- **Deployed plugin model is still 12-feature.** To ship the 16-feature model: take `--export_dir` output → update C++ `ML_FEATURE_COUNT`/`PushFeature`/`ais_ids.cpp` compute to match the scaler `features` order → build → release.

---

## Evaluation Scenarios (32 total)

| Group | Description |
|---|---|
| Basic (4) | COG/HDG mismatch, anchored movement, speed anomaly, position jump |
| FN (4) | Designed to evade rule-based detectors |
| D (4) | ML model evasion attempt 1st gen |
| E (5) | ML model evasion attempt 2nd gen |
| F (7, holdout) | Advanced attacks — excluded from training |
| G (7, holdout) | Novel holdout scenarios |

---

## Model File Path Rules

(For the full D:\ tree see "Data Paths" above.)
- Trained (per model): `D:\ais_models\{name}\model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`
- Deploy: `ml/deploy/model.onnx`, `ml/deploy/scaler.json`, `ml/deploy/threshold.txt`
- Plugin: `ais_ids_pi/data/model.onnx`, `scaler.json`, `threshold.txt`

---

## Plugin Build & Deploy (native Linux ONLY)

**The OpenCPN plugin is built and deployed on native Linux. Windows is used only for ML model training (the `ml/` pipeline). Do not add Windows/WSL-specific workarounds to the build scripts.**

- Target: Ubuntu 24.04 (noble) — matches `OCPN_TARGET=noble` in `local-build-package.sh`.
- `ais_ids_pi/opencpn-libs/` is a git submodule (`https://github.com/OpenCPN/opencpn-libs`). Before the first build run `git submodule update --init --recursive`, otherwise cmake fails with "opencpn-libs/... is not an existing directory".
- ONNX Runtime is bundled at `ais_ids_pi/onnxruntime/{include,lib}`. On Linux the `.so`/`.so.1` files are real symlinks (only break when checked out on NTFS/Windows).
- Build deps: `g++ cmake`, wxWidgets 3.2 dev (`libwxgtk3.2-dev`), `nlohmann-json3-dev`, and full OpenCPN build deps (`sudo mk-build-deps --install ci/control`).
- Build command (from `ais_ids_pi/`): `./local-build-package.sh` → produces `ais_ids_pi-<version>-ubuntu-x86_64-24.04-noble.tar.gz`.
- Plugin loads `model.onnx`/`scaler.json`/`threshold.txt` by default (single-model). For ensemble, drop an `ensemble_config.json` in `data/`. `model_seq5.*` is the optional early-detection seq5 model.
- C++ feature count is hardcoded: `ML_FEATURE_COUNT` in `ais_ids_pi/include/ais_ml.h`. It MUST match the deployed model/scaler feature count (currently 12). Changing features requires updating `ML_FEATURE_COUNT`, `PushFeature()` (decl + impl in `ais_ml.cpp`), and the compute/call sites in `ais_ids.cpp`.

---

## Branch Strategy

- `main`: stable releases
- `develop`: main integration branch — work here
- `claude/*`: Claude Code working branches (merge into develop when done)

---

## Release & Version Management

### Version Scheme

`v{major}.{minor}.{patch}` — Semantic Versioning

| Bump | When | Example |
|---|---|---|
| **major** | Input features change (12→N), model interface breaks, SEQ_LEN change | v1.0.0 → v2.0.0 |
| **minor** | New model added, new eval scenarios added, significant accuracy improvement | v1.0.0 → v1.1.0 |
| **patch** | Threshold re-tuned, bug fix, same model retrained on more data | v1.0.0 → v1.0.1 |

A model retrained on new data (same architecture) = **patch**. New architecture = **minor**.

### Release Assets

Every release MUST attach the following.

**1. Plugin package (REQUIRED) — output of `local-build-package.sh`**
- `ais_ids_pi-{version}-ubuntu-x86_64-{ubuntu_ver}-noble.tar.gz` — built plugin (model/scaler/threshold bundled under `data/` + ONNX Runtime `.so`). Users just extract it and it works.
- Built on native Linux (NOT Windows). Run `git submodule update --init --recursive` before building. See the "Plugin Build & Deploy" section.

**2. Model files (REQUIRED) — for individual download**
- `model_{name}.onnx` — trained ONNX model (one per model)
- `scaler_{name}.json` — Min-Max scaler (the `features` array shows feature order/count)
- `threshold_{name}.txt` — anomaly threshold

**3. Performance reports (optional)**
- `comparison_TIMESTAMP.txt` — human-readable performance table
- `comparison_TIMESTAMP.csv` — combined model comparison CSV
- `{model}_TIMESTAMP.csv` — per-model individual CSV (one per model)

> For the detection-rate numbers, use the actual evaluation values of THAT model — do not misquote numbers from a different feature set.

### How to Create a Release

```bash
# 1. Merge claude/* branch into develop, then develop into main
git checkout main
git merge develop

# 2. Tag
git tag v1.0.0
git push origin main --tags

# 3. Create release and attach files
#    (1) plugin tar.gz  (2) model files  (3) performance reports (optional)
gh release create v1.0.0 \
  ais_ids_pi/ais_ids_pi-1.0.358.1-ubuntu-x86_64-24.04-noble.tar.gz \
  D:\ais_models\dcdetect\model_dcdetect.onnx \
  D:\ais_models\dcdetect\scaler_dcdetect.json \
  D:\ais_models\dcdetect\threshold_dcdetect.txt \
  D:\ais_output\pipeline\comparison_TIMESTAMP.txt \
  D:\ais_output\pipeline\comparison_TIMESTAMP.csv \
  --title "v1.0.0 — conv1d / tranad / dcdetect" \
  --notes "$(cat <<'EOF'
## Models
- conv1d, tranad, dcdetect (3 epochs each)

## Training Data
- D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
- Coverage: 2025-09-14 (1 day, expand later)

## Performance (FP ≈ 1%)
- conv1d:   train 68.3% / holdout 70.8%
- tranad:   train 35.4% / holdout 61.0%
- dcdetect: train 47.4% / holdout 65.6%

## Plugin Deploy
Copy model.onnx / scaler.json / threshold.txt to ais_ids_pi/data/
EOF
)"

# 4. Download on another machine
gh release download v1.0.0 --dir D:\ais_models
```

### Release Notes Template

```
## Models
- <model list and epoch counts>

## Training Data
- <data file path and date coverage>

## Performance (FP ≈ 1%)
- <model>: train X% / holdout Y%

## Changes from previous version
- <what changed>

## Plugin Deploy
- Recommended: extract `ais_ids_pi-*.tar.gz` into the OpenCPN plugin path (model bundled, works immediately)
- Manual: copy model_{name}.onnx/scaler/threshold to ais_ids_pi/data/ as model.onnx/scaler.json/threshold.txt
```

### Version History

| Version | Date | Models | Notes |
|---|---|---|---|
| v0.1.0 | — | conv1d, tranad, dcdetect | Initial release (1-day training data) |
| v0.2.0 | 2026-05-22 | dcdetect | Plugin default model.onnx → dcdetect (12 feat, 3yr data). Adds model_dcdetect.* + Linux tar.gz to release assets |

---

## Environment

Two distinct environments — keep them separate:

**ML training (Windows)**
- Python 3.14 (Windows)
- Console encoding: cp949 — `sys.stdout.reconfigure(encoding='utf-8')` applied in pipeline.py
- GPU: Intel Arc B390 (iGPU, shared memory) — no CUDA, torch-directml not installed
- Training runs on CPU

**Plugin build/deploy (native Linux)**
- Ubuntu 24.04 (noble). See "Plugin Build & Deploy" section above.
- Windows/WSL is NOT the build target — don't add Windows-specific hacks to build scripts.
