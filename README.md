# JB-Pirate-King — AIS Anomaly Detection System

A system that detects anomalous behavior in ship AIS signals. It consists of an OpenCPN plugin, a local server, and an ML pipeline.

---

## Run Results

Auto-updated by the orchestrator's `readme` node after each release. The summary table is one
row per run (newest first); each run also gets a collapsible detail block with per-FP and
per-scenario detection rates. The Note is written by claude resuming the branch session.

<!-- RUN_RESULTS:BEGIN -->
| Run | Date | Adopted | FP=1% (base→final) | FP=5% | FP=10% | Threshold | Feats | Note |
|---|---|---|---|---|---|---|---|---|
| lstm_002 | 2026-06-19 | `pos_sog_ratio` | 34.1%→38.7% (+4.6pp) | 77.3% | 87.5% | 0.005723 | 14 | pos_sog_ratio(보고SOG↔실이동속도 괴리) 채택, FP=1% 34.1%→38.7%(+4.6pp), 피처 14개 |
| lstm_001 | 2026-06-19 | `sog_accel` | 26.9%→35.0% (+8.1pp) | 79.0% | 88.2% | 0.006155 | 13 | lstm_001: sog_accel(가속도) 채택으로 FP=1% 탐지율 26.9%→35.0%(+8.1pp) 달성 |
<!-- RUN_RESULTS:END -->

### Per-run detail

<!-- RUN_DETAILS:BEGIN -->
<details>
<summary><b>lstm_002</b> — <code>pos_sog_ratio</code> · FP=1% 34.1%→38.7% · 2026-06-19</summary>

- `pos_sog_ratio` — 위치기반 이동속도크기 / 보고 SOG — 저속 위장 시 보고속도와 실제이동 불일치
- 🤖 pos_sog_ratio(보고SOG↔실이동속도 괴리) 채택, FP=1% 34.1%→38.7%(+4.6pp), 피처 14개

| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |
|---|---|---|---|---|
| FN3-COG경계 | 0.0% | 0.5% | 13.7% | ⚠️ weak |
| FN4-status | 0.0% | 0.1% | 0.9% | ⚠️ weak |
| F3-TrajStitch | 0.0% | 47.0% | 96.0% | ⚠️ weak |
| F7-LSTMBeat | 0.0% | 9.5% | 80.0% | ⚠️ weak |
| D1-LowSlow | 0.1% | 1.0% | 1.8% | ⚠️ weak |
| F1-FeatSmooth | 0.2% | 81.6% | 100.0% | ⚠️ weak |
| G4-StatusFlicker | 0.5% | 88.1% | 100.0% | ⚠️ weak |
| D3-GradDrift | 0.8% | 89.0% | 100.0% | ⚠️ weak |
| G2-SpeedBurst | 0.9% | 98.8% | 100.0% | ⚠️ weak |
| COG/HDG불일치 | 1.0% | 25.7% | 57.2% | ⚠️ weak |
| 정박이동 | 2.6% | 27.5% | 63.3% | ⚠️ weak |
| F5-MultiCoord | 3.1% | 80.3% | 99.9% | ⚠️ weak |
| D4-Mimicry | 3.6% | 80.2% | 100.0% | ⚠️ weak |
| G3-PhantomHDG | 4.1% | 98.3% | 100.0% | ⚠️ weak |
| E1-Smooth | 7.6% | 80.8% | 99.8% | ⚠️ weak |
| 속도이상 | 7.8% | 93.2% | 100.0% | ⚠️ weak |
| G6-LandRoute | 35.2% | 100.0% | 100.0% | ⚠️ weak |
| F6-AISGap | 39.3% | 94.6% | 100.0% | ⚠️ weak |
| E5-Shadow | 42.2% | 100.0% | 100.0% | ⚠️ weak |
| E2-Desync | 45.7% | 99.1% | 100.0% | ⚠️ weak |
| E4-Contextual | 63.5% | 99.8% | 100.0% |  |
| 위치점프 | 82.6% | 100.0% | 100.0% |  |
| G1-CircularLoop | 89.6% | 100.0% | 100.0% |  |
| G7-MMSISpoof | 90.7% | 100.0% | 100.0% |  |
| F2-Intermit | 93.3% | 100.0% | 100.0% |  |
| E3-WinEdge | 93.9% | 100.0% | 100.0% |  |
| D2-Temporal | 95.2% | 100.0% | 100.0% |  |
| G5-ZigzagAccel | 96.1% | 100.0% | 100.0% |  |
| F4-TimeSkew | 99.8% | 100.0% | 100.0% |  |
| FN1-dt점프 | 100.0% | 100.0% | 100.0% |  |
| FN2-속도단계 | 100.0% | 100.0% | 100.0% |  |

</details>

<details>
<summary><b>lstm_001</b> — <code>sog_accel</code> · FP=1% 26.9%→35.0% · 2026-06-19</summary>

- `sog_accel` — 속도변화량/시간간격 = 가속도 — 순간 속도급증/단계적 위장 포착
- 🤖 lstm_001: sog_accel(가속도) 채택으로 FP=1% 탐지율 26.9%→35.0%(+8.1pp) 달성

| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |
|---|---|---|---|---|
| FN3-COG경계 | 0.0% | 0.4% | 27.0% | ⚠️ weak |
| FN4-status | 0.0% | 0.1% | 0.5% | ⚠️ weak |
| F1-FeatSmooth | 0.0% | 69.9% | 100.0% | ⚠️ weak |
| F3-TrajStitch | 0.0% | 54.4% | 94.4% | ⚠️ weak |
| F7-LSTMBeat | 0.0% | 11.8% | 76.4% | ⚠️ weak |
| D1-LowSlow | 0.1% | 1.5% | 2.0% | ⚠️ weak |
| G4-StatusFlicker | 0.3% | 91.7% | 99.8% | ⚠️ weak |
| D3-GradDrift | 0.4% | 95.4% | 100.0% | ⚠️ weak |
| F5-MultiCoord | 0.6% | 81.7% | 99.7% | ⚠️ weak |
| G2-SpeedBurst | 0.8% | 100.0% | 100.0% | ⚠️ weak |
| D4-Mimicry | 1.1% | 85.4% | 100.0% | ⚠️ weak |
| COG/HDG불일치 | 1.4% | 43.2% | 66.4% | ⚠️ weak |
| 정박이동 | 3.0% | 34.9% | 67.3% | ⚠️ weak |
| E1-Smooth | 3.0% | 83.3% | 99.6% | ⚠️ weak |
| E5-Shadow | 3.3% | 100.0% | 100.0% | ⚠️ weak |
| G3-PhantomHDG | 3.8% | 99.4% | 100.0% | ⚠️ weak |
| 속도이상 | 8.6% | 97.3% | 100.0% | ⚠️ weak |
| E4-Contextual | 17.3% | 100.0% | 100.0% | ⚠️ weak |
| G6-LandRoute | 31.5% | 100.0% | 100.0% | ⚠️ weak |
| E2-Desync | 38.1% | 99.3% | 100.0% | ⚠️ weak |
| F6-AISGap | 40.3% | 98.1% | 100.0% | ⚠️ weak |
| D2-Temporal | 84.7% | 100.0% | 100.0% |  |
| 위치점프 | 85.8% | 100.0% | 100.0% |  |
| F2-Intermit | 88.0% | 99.9% | 100.0% |  |
| G7-MMSISpoof | 89.0% | 100.0% | 100.0% |  |
| G1-CircularLoop | 92.5% | 100.0% | 100.0% |  |
| E3-WinEdge | 94.5% | 100.0% | 100.0% |  |
| G5-ZigzagAccel | 97.6% | 100.0% | 100.0% |  |
| F4-TimeSkew | 99.7% | 100.0% | 100.0% |  |
| FN1-dt점프 | 100.0% | 100.0% | 100.0% |  |
| FN2-속도단계 | 100.0% | 100.0% | 100.0% |  |

</details>
<!-- RUN_DETAILS:END -->

---

## Components

| Directory | Description |
|---|---|
| `ml/` | AIS anomaly-detection ML pipeline (training · evaluation) |
| `ais_ids_pi/` | OpenCPN plugin (C++, ONNX inference) |
| `s-c/` | Local server + GUI (Python, Docker) |
| `aivdm_gen/` | AIVDM attack-scenario simulator (GUI) |

---

## ML Pipeline (`ml/`)

Supports 9 unsupervised (autoencoder-family) models (supervised models were removed on develop).

```bash
# Full orchestrator (Slack approval gates + Google Sheets + git automation)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess

# Unattended (all gates auto-approved)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess --auto_approve

# Simple train+eval (experiments)
python ml/core/pipeline.py --train --eval --models dcdetect tranad conv1d

# Evaluation
python ml/core/eval_anomaly.py --model dcdetect

# Standalone feature engineering (Greedy selection → export deployable model)
python ml/core/feature_engineer.py --input D:/ais_data/preprocessed/ais_preprocessed_3yr.csv \
  --base_dir D:/ --max_mmsi 3000 --epochs 5 --export_dir D:/ais_models/dcdetect
```

The orchestrator auto-chains run branches (`dcdetect_001 → 002 → ...`), adopting one feature per
run until convergence. See [`CLAUDE.md`](CLAUDE.md) for the full pipeline architecture and
[`ml/PIPELINE.md`](ml/PIPELINE.md) for the LangGraph node-level reference. More ML detail in
[`ml/README.md`](ml/README.md).

---

## OpenCPN Plugin (`ais_ids_pi/`)

Placing a trained ONNX model in the plugin's `data/` folder enables real-time inference.

```
ais_ids_pi/data/
    model.onnx
    scaler.json
    threshold.txt
```

**Build/deploy is native-Linux only** (Windows is for ML training). Initialize the submodule before the first build:

```bash
git submodule update --init --recursive    # fetch opencpn-libs
cd ais_ids_pi && ./local-build-package.sh   # → produces tar.gz
```

The C++ input-feature count is hardcoded in `ML_FEATURE_COUNT` (`ais_ids_pi/include/ais_ml.h`) and must match the deployed model's feature count. See the "Plugin Build & Deploy" section of [`CLAUDE.md`](CLAUDE.md) for the full build guide.

---

## Local Server (`s-c/`)

A server that receives AIS NMEA signals from OpenCPN over TCP and detects anomalies. Run via GUI or CLI.

```powershell
cd s-c
python ais_ids_gui.py
```

See [`s-c/Readme.md`](s-c/Readme.md) for details.

---

## Scenario Simulator (`aivdm_gen/`)

An ML-aware attack simulator that injects AIVDM NMEA signals directly into OpenCPN or the IDS server.

```bash
python aivdm_gen/aivdm_gen.py
```

| Group | Scenarios | Description |
|---|---|---|
| A | A1~A4 | Rule-based detection checks (speed · anchored · COG/HDG · position jump) |
| B | B5~B7 | Multi-vessel coordinated patterns (text formation, pincer, wave formation) |
| D | D1~D4 | ML-evasion 1st gen (Low&Slow, time disguise, Gradual Drift, Mimicry) |
| E | E4~E5 | ML-evasion 2nd gen (Contextual Blend, Shadow Vessel) |
| F | F3, F6 | Structural attacks (trajectory stitching, history reset via AIS gap) |

Transport (TCP server/client, UDP) is selectable in the GUI.
See [`aivdm_gen/README.md`](aivdm_gen/README.md) for details.

---

## CI

GitHub Actions runs automatic checks on Push/PR (`.github/workflows/ci.yml`).

- Python syntax check + DCdetector smoke test
- C++ core-file compile (`g++ -fsyntax-only`)
- C++ static analysis (`cppcheck`)
