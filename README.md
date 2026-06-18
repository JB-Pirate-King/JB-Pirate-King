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
| dcdetect_001 | 2026-06-19 | `anchor_motion` | 66.1%→78.0% (+11.8pp) | 91.4% | 93.9% | 0.002540 | 13 | anchor_motion(정박 중 이동 모순 포착) 채택으로 FP=1% 탐지율 66.1%→78.0% (+11.9pp). |
<!-- RUN_RESULTS:END -->

### Per-run detail

<!-- RUN_DETAILS:BEGIN -->
<details>
<summary><b>dcdetect_001</b> — <code>anchor_motion</code> · FP=1% 66.1%→78.0% · 2026-06-19</summary>

- `anchor_motion` — 정박/계류 상태(status 1,5)인데 이동거리 큼 — 정박이동·status 위장 직접 포착
- 🤖 anchor_motion(정박 중 이동 모순 포착) 채택으로 FP=1% 탐지율 66.1%→78.0% (+11.9pp).

| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |
|---|---|---|---|---|
| FN4-status | 0.0% | 29.3% | 52.2% | ⚠️ weak |
| D1-LowSlow | 0.0% | 0.0% | 0.0% | ⚠️ weak |
| 정박이동 | 9.0% | 63.0% | 90.7% | ⚠️ weak |
| FN3-COG경계 | 36.8% | 78.7% | 90.9% | ⚠️ weak |
| D4-Mimicry | 41.5% | 96.0% | 99.4% | ⚠️ weak |
| COG/HDG불일치 | 50.7% | 74.5% | 80.2% |  |
| 속도이상 | 62.1% | 96.5% | 99.3% |  |
| E4-Contextual | 65.2% | 97.8% | 100.0% |  |
| D3-GradDrift | 69.5% | 99.9% | 100.0% |  |
| FN2-속도단계 | 73.0% | 100.0% | 100.0% |  |
| F7-LSTMBeat | 74.9% | 100.0% | 100.0% |  |
| 위치점프 | 78.5% | 100.0% | 100.0% |  |
| G2-SpeedBurst | 84.9% | 100.0% | 100.0% |  |
| G3-PhantomHDG | 86.0% | 98.6% | 99.5% |  |
| F6-AISGap | 92.3% | 100.0% | 100.0% |  |
| F2-Intermit | 97.0% | 100.0% | 100.0% |  |
| D2-Temporal | 97.7% | 100.0% | 100.0% |  |
| F5-MultiCoord | 98.6% | 100.0% | 100.0% |  |
| G7-MMSISpoof | 99.3% | 100.0% | 100.0% |  |
| G1-CircularLoop | 99.8% | 100.0% | 100.0% |  |
| F4-TimeSkew | 99.9% | 100.0% | 100.0% |  |
| G4-StatusFlicker | 100.0% | 100.0% | 100.0% |  |
| FN1-dt점프 | 100.0% | 100.0% | 100.0% |  |
| E1-Smooth | 100.0% | 100.0% | 100.0% |  |
| E2-Desync | 100.0% | 100.0% | 100.0% |  |
| E3-WinEdge | 100.0% | 100.0% | 100.0% |  |
| E5-Shadow | 100.0% | 100.0% | 100.0% |  |
| F1-FeatSmooth | 100.0% | 100.0% | 100.0% |  |
| F3-TrajStitch | 100.0% | 100.0% | 100.0% |  |
| G5-ZigzagAccel | 100.0% | 100.0% | 100.0% |  |
| G6-LandRoute | 100.0% | 100.0% | 100.0% |  |

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
