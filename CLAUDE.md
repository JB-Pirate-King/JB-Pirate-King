# CLAUDE.md — JB-Pirate-King Project Context

## Pre-Push Checklist

Before pushing code or when asked to push, always check the following first:

1. **README.md / ml/README.md** — Verify that any changed features, paths, or options are reflected in the docs. Update outdated content before pushing.
2. **Source code comments** — Verify that comments in modified functions/classes match current behavior. Remove or fix any stale comments.

---

## Project Overview

AIS anomaly detection system for ships. Consists of an OpenCPN plugin (C++), ML pipeline (Python), and local server (Python/Docker).

---

## Directory Structure

```
JB-Pirate-King/
├── ml/                    # ML pipeline (training & evaluation)
│   ├── pipeline.py        # Multi-model training/detection rate comparison ★
│   ├── preprocess.py      # AIS CSV preprocessing (.csv.zst supported)
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

```
D:\ais_data\
├── raw\
│   └── 2025\                          # Raw .csv.zst files (172 files)
└── preprocessed\
    └── 2025\
        ├── daily\                     # Per-day preprocessed files (to be created)
        │   ├── ais-2025-07-13_preprocessed.csv
        │   └── ...
        └── ais_preprocessed_2025.csv  # Yearly merged file (870 MB, pipeline default)
```

### Preprocessing Steps

```bash
# Step 1: Per-day preprocessing
python ml/preprocess.py D:\ais_data\raw\2025 --output_dir D:\ais_data\preprocessed\2025\daily

# Step 2: Yearly merge
python ml/preprocess.py "D:\ais_data\preprocessed\2025\daily\*_preprocessed.csv" ^
    --output D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
```

> Currently only 2025-09-14 (1 day) has been preprocessed. Remaining 171 files to be run later.

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

## Directory Structure (D:\ base)

```
D:\
├── ais_data\
│   ├── raw\2025\                     # raw .csv.zst files
│   └── preprocessed\2025\
│       ├── daily\                    # per-day preprocessed files
│       └── ais_preprocessed_2025.csv # yearly merged (pipeline default)
├── ais_models\                       # trained model files (--base_dir default)
│   ├── model_{name}.onnx
│   ├── scaler_{name}.json
│   └── threshold_{name}.txt
└── ais_output\
    └── pipeline\                     # pipeline comparison results
        ├── comparison_TIMESTAMP.txt
        ├── comparison_TIMESTAMP.csv  # combined all-model CSV
        └── {model}_TIMESTAMP.csv     # per-model individual CSV
```

## Model File Path Rules

- Unsupervised: `D:\ais_models\{name}\model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`
- Deploy: `ml/deploy/model.onnx`, `ml/deploy/scaler.json`, `ml/deploy/threshold.txt`
- Plugin: `ais_ids_pi/data/model.onnx`, `scaler.json`, `threshold.txt`

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

Each release should attach:
- `model_{name}.onnx` — trained ONNX model (one per model)
- `scaler_{name}.json` — Min-Max scaler
- `threshold_{name}.txt` — anomaly threshold
- `comparison_TIMESTAMP.txt` — human-readable performance table
- `comparison_TIMESTAMP.csv` — combined model comparison CSV
- `{model}_TIMESTAMP.csv` — per-model individual CSV (one per model)

### How to Create a Release

```bash
# 1. Merge claude/* branch into develop, then develop into main
git checkout main
git merge develop

# 2. Tag
git tag v1.0.0
git push origin main --tags

# 3. Create release and attach files
gh release create v1.0.0 \
  D:\ais_models\model_conv1d.onnx \
  D:\ais_models\scaler_conv1d.json \
  D:\ais_models\threshold_conv1d.txt \
  D:\ais_output\pipeline\comparison_TIMESTAMP.txt \
  D:\ais_output\pipeline\comparison_TIMESTAMP.csv \
  D:\ais_output\pipeline\conv1d_TIMESTAMP.csv \
  --title "v1.0.0 — conv1d / tranad / dcdetect" \
  --notes "$(cat <<'EOF'
## Models
- conv1d, tranad, dcdetect (3 epochs each)

## Training Data
- D:\ais_data\preprocessed\2025\ais_preprocessed_2025.csv
- Coverage: 2025-09-14 (1 day, expand later)

## Performance (FP ≈ 1%)
- conv1d:   학습 68.3% / 홀드아웃 70.8%
- tranad:   학습 35.4% / 홀드아웃 61.0%
- dcdetect: 학습 47.4% / 홀드아웃 65.6%

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
- <model>: 학습 X% / 홀드아웃 Y%

## Changes from previous version
- <what changed>

## Plugin Deploy
Copy model.onnx / scaler.json / threshold.txt to ais_ids_pi/data/
```

### Version History

| Version | Date | Models | Notes |
|---|---|---|---|
| v0.1.0 | — | conv1d, tranad, dcdetect | Initial release (1-day training data) |

---

## Environment

- Python 3.14 (Windows)
- Console encoding: cp949 — `sys.stdout.reconfigure(encoding='utf-8')` applied in pipeline.py
- GPU: Intel Arc B390 (iGPU, shared memory) — no CUDA, torch-directml not installed
- Training runs on CPU
