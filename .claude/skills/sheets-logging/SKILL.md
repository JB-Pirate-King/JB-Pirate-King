---
name: sheets-logging
description: Reference for the project's Google Sheets logging: the 5 per-model tabs and their exact column schemas. Use when working on sheets.py, Google Sheets logging, tab structure, or interpreting or adding logged pipeline metrics.
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
