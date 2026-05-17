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

- Unsupervised: `D:\ais_models\model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`
- Deploy: `ml/deploy/model.onnx`, `ml/deploy/scaler.json`, `ml/deploy/threshold.txt`
- Plugin: `ais_ids_pi/data/model.onnx`, `scaler.json`, `threshold.txt`

---

## Branch Strategy

- `main`: stable releases
- `develop`: main integration branch — work here
- `claude/*`: Claude Code working branches (merge into develop when done)

---

## Environment

- Python 3.14 (Windows)
- Console encoding: cp949 — `sys.stdout.reconfigure(encoding='utf-8')` applied in pipeline.py
- GPU: Intel Arc B390 (iGPU, shared memory) — no CUDA, torch-directml not installed
- Training runs on CPU
