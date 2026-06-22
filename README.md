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
| dcdetect_004 | 2026-06-23 | `status_expected_speed_dev` | 81.9%→83.2% (+1.3pp) | 92.0% | 93.9% | 0.000657 | 16 | status 기대속도 위반량 피처 채택으로 FP=1% 탐지율 81.9%→83.2%(+1.3pp), 16피처. |
<!-- RUN_RESULTS:END -->

### Per-run detail

<!-- RUN_DETAILS:BEGIN -->
<details>
<summary><b>dcdetect_004</b> — <code>status_expected_speed_dev</code> · FP=1% 81.9%→83.2% · 2026-06-23</summary>

- `status_expected_speed_dev` — status가 함의하는 기대속도(정박/계류 status 1,5,6→0, 그 외 항행→약5kn)와 실제 sog의 연속 편차 — 모든 행에서 값이 살아있는 dense 신호라 status 단독 위조(FN4) 시 잔차가 끊김없이 커짐
- 🤖 status 기대속도 위반량 피처 채택으로 FP=1% 탐지율 81.9%→83.2%(+1.3pp), 16피처.

| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |
|---|---|---|---|---|
| D1-LowSlow | 0.0% | 0.1% | 3.0% | ⚠️ weak |
| 정박이동 | 7.8% | 64.9% | 84.2% | ⚠️ weak |
| FN4-status | 12.2% | 37.0% | 46.8% | ⚠️ weak |
| D3-GradDrift | 14.3% | 82.5% | 97.0% | ⚠️ weak |
| COG/HDG불일치 | 61.2% | 84.1% | 86.8% |  |
| 속도이상 | 72.6% | 96.8% | 99.1% |  |
| F4-TimeSkew | 75.0% | 100.0% | 100.0% |  |
| 위치점프 | 75.4% | 98.4% | 99.9% |  |
| E4-Contextual | 78.9% | 88.9% | 95.3% |  |
| FN3-COG경계 | 85.3% | 99.2% | 100.0% |  |
| G3-PhantomHDG | 96.3% | 99.8% | 100.0% |  |
| G1-CircularLoop | 99.4% | 100.0% | 100.0% |  |
| D2-Temporal | 100.0% | 100.0% | 100.0% |  |
| D4-Mimicry | 100.0% | 100.0% | 100.0% |  |
| FN1-dt점프 | 100.0% | 100.0% | 100.0% |  |
| FN2-속도단계 | 100.0% | 100.0% | 100.0% |  |
| E1-Smooth | 100.0% | 100.0% | 100.0% |  |
| E2-Desync | 100.0% | 100.0% | 100.0% |  |
| E3-WinEdge | 100.0% | 100.0% | 100.0% |  |
| E5-Shadow | 100.0% | 100.0% | 100.0% |  |
| F1-FeatSmooth | 100.0% | 100.0% | 100.0% |  |
| F2-Intermit | 100.0% | 100.0% | 100.0% |  |
| F3-TrajStitch | 100.0% | 100.0% | 100.0% |  |
| F5-MultiCoord | 100.0% | 100.0% | 100.0% |  |
| F6-AISGap | 100.0% | 100.0% | 100.0% |  |
| F7-LSTMBeat | 100.0% | 100.0% | 100.0% |  |
| G2-SpeedBurst | 100.0% | 100.0% | 100.0% |  |
| G4-StatusFlicker | 100.0% | 100.0% | 100.0% |  |
| G5-ZigzagAccel | 100.0% | 100.0% | 100.0% |  |
| G6-LandRoute | 100.0% | 100.0% | 100.0% |  |
| G7-MMSISpoof | 100.0% | 100.0% | 100.0% |  |

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
