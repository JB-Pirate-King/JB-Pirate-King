---
name: feature-engineering
description: Reference for the DCdetect feature engineering procedure: the Greedy forward-selection algorithm, the FP=1%/5%/10% evaluation criterion, output JSON keys, and feature_engineer.py options. Use when working on feature engineering, feature_engineer.py, the FE algorithm, adoption thresholds, or interpreting FE output.
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
