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
| tcn_003 | 2026-06-21 | `cog_change_reversal` | 56.1%→66.0% (+9.9pp) | 81.9% | 86.2% | 0.000007 | 15 | cog_change_reversal(COG-HDG 부호반전 강도) 채택으로 탐지율 56.1%→66.0%(+9.9pp) 달성. |
| tcn_002 | 2026-06-21 | `speed_consistency_min` | 50.2%→57.1% (+6.9pp) | 73.8% | 83.3% | 0.000004 | 14 | speed_consistency_min(윈도우 내 속도일관성 최솟값) 채택으로 FP=1% 탐지율 50.2%→57.1%(+6.9pp) 달성 |
| tcn_001 | 2026-06-20 | `dt_irregularity` | 18.9%→46.1% (+27.2pp) | 73.1% | 83.2% | 0.000002 | 13 | dt_irregularity(수신간격 불규칙성) 채택으로 FP=1% 탐지율 18.9%→46.1%(+27.2pp) 달성. |
<!-- RUN_RESULTS:END -->

### Per-run detail

<!-- RUN_DETAILS:BEGIN -->
<details>
<summary><b>tcn_003</b> — <code>cog_change_reversal</code> · FP=1% 56.1%→66.0% · 2026-06-21</summary>

- `cog_change_reversal` — 연속 COG-HDG 변화의 부호 반전 강도(지그재그) — 인위적 왕복/봇 패턴 포착
- 🤖 cog_change_reversal(COG-HDG 부호반전 강도) 채택으로 탐지율 56.1%→66.0%(+9.9pp) 달성.

| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |
|---|---|---|---|---|
| G6-LandRoute | 0.4% | 7.2% | 16.2% | ⚠️ weak |
| F4-TimeSkew | 13.7% | 50.5% | 74.1% | ⚠️ weak |
| D1-LowSlow | 16.6% | 36.0% | 48.8% | ⚠️ weak |
| FN4-status | 22.2% | 47.6% | 59.6% | ⚠️ weak |
| FN2-속도단계 | 26.3% | 63.9% | 73.9% | ⚠️ weak |
| 속도이상 | 47.9% | 74.0% | 80.8% | ⚠️ weak |
| E4-Contextual | 48.1% | 67.2% | 76.5% | ⚠️ weak |
| 위치점프 | 50.5% | 89.0% | 95.9% |  |
| FN3-COG경계 | 58.2% | 78.2% | 81.7% |  |
| G2-SpeedBurst | 58.5% | 77.2% | 82.0% |  |
| G1-CircularLoop | 59.3% | 91.4% | 96.6% |  |
| COG/HDG불일치 | 59.6% | 77.9% | 82.4% |  |
| F5-MultiCoord | 61.5% | 88.6% | 90.4% |  |
| F6-AISGap | 61.9% | 80.8% | 83.5% |  |
| D4-Mimicry | 65.8% | 80.2% | 84.4% |  |
| 정박이동 | 69.5% | 88.0% | 93.1% |  |
| FN1-dt점프 | 70.9% | 98.5% | 100.0% |  |
| E3-WinEdge | 73.9% | 86.1% | 88.8% |  |
| F7-LSTMBeat | 77.0% | 87.2% | 87.8% |  |
| E2-Desync | 79.6% | 86.6% | 89.6% |  |
| E5-Shadow | 80.4% | 100.0% | 100.0% |  |
| D3-GradDrift | 84.2% | 98.2% | 99.6% |  |
| D2-Temporal | 84.5% | 93.9% | 96.3% |  |
| F1-FeatSmooth | 91.1% | 93.1% | 94.0% |  |
| F2-Intermit | 92.3% | 96.4% | 96.8% |  |
| F3-TrajStitch | 97.5% | 100.0% | 100.0% |  |
| G4-StatusFlicker | 98.7% | 100.0% | 100.0% |  |
| G3-PhantomHDG | 98.8% | 99.8% | 99.9% |  |
| G5-ZigzagAccel | 99.0% | 100.0% | 100.0% |  |
| G7-MMSISpoof | 99.6% | 99.9% | 99.9% |  |
| E1-Smooth | 99.9% | 100.0% | 100.0% |  |

</details>

<details>
<summary><b>tcn_002</b> — <code>speed_consistency_min</code> · FP=1% 50.2%→57.1% · 2026-06-21</summary>

- `speed_consistency_min` — 윈도우 내 속도일관성 최솟값 — 구간 중 가장 모순적인 순간을 포착해 맥락기반 위장 탐지
- 🤖 speed_consistency_min(윈도우 내 속도일관성 최솟값) 채택으로 FP=1% 탐지율 50.2%→57.1%(+6.9pp) 달성

| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |
|---|---|---|---|---|
| FN4-status | 0.0% | 0.4% | 6.8% | ⚠️ weak |
| D1-LowSlow | 1.3% | 19.8% | 33.6% | ⚠️ weak |
| 속도이상 | 10.4% | 33.6% | 55.0% | ⚠️ weak |
| G2-SpeedBurst | 15.0% | 41.4% | 63.9% | ⚠️ weak |
| F7-LSTMBeat | 24.0% | 41.7% | 55.9% | ⚠️ weak |
| F3-TrajStitch | 26.2% | 62.0% | 83.2% | ⚠️ weak |
| D2-Temporal | 33.0% | 71.3% | 85.8% | ⚠️ weak |
| F5-MultiCoord | 35.9% | 60.1% | 78.0% | ⚠️ weak |
| E3-WinEdge | 36.1% | 54.6% | 72.2% | ⚠️ weak |
| 위치점프 | 41.2% | 77.5% | 92.2% | ⚠️ weak |
| F6-AISGap | 44.2% | 78.3% | 93.0% | ⚠️ weak |
| FN3-COG경계 | 45.4% | 72.2% | 80.7% | ⚠️ weak |
| 정박이동 | 45.5% | 60.7% | 72.9% | ⚠️ weak |
| E2-Desync | 46.0% | 73.7% | 84.8% | ⚠️ weak |
| G5-ZigzagAccel | 48.0% | 76.9% | 89.7% | ⚠️ weak |
| E5-Shadow | 48.8% | 76.6% | 93.5% | ⚠️ weak |
| F1-FeatSmooth | 51.2% | 64.8% | 85.4% |  |
| COG/HDG불일치 | 63.9% | 77.6% | 84.7% |  |
| G3-PhantomHDG | 73.2% | 94.1% | 98.3% |  |
| E4-Contextual | 75.1% | 82.3% | 85.0% |  |
| FN2-속도단계 | 76.6% | 89.2% | 100.0% |  |
| F4-TimeSkew | 78.1% | 99.7% | 99.9% |  |
| E1-Smooth | 81.4% | 97.5% | 99.7% |  |
| F2-Intermit | 85.8% | 88.7% | 91.7% |  |
| D4-Mimicry | 90.7% | 95.6% | 97.8% |  |
| G7-MMSISpoof | 96.0% | 98.6% | 99.4% |  |
| D3-GradDrift | 98.6% | 100.0% | 100.0% |  |
| G4-StatusFlicker | 99.4% | 99.8% | 99.9% |  |
| G6-LandRoute | 99.6% | 100.0% | 100.0% |  |
| FN1-dt점프 | 100.0% | 100.0% | 100.0% |  |
| G1-CircularLoop | 100.0% | 100.0% | 100.0% |  |

</details>

<details>
<summary><b>tcn_001</b> — <code>dt_irregularity</code> · FP=1% 18.9%→46.1% · 2026-06-20</summary>

- `dt_irregularity` — 직전 대비 수신 간격 변동 비율 — dt점프/간헐송출/시간왜곡 타이밍 이상
- 🤖 dt_irregularity(수신간격 불규칙성) 채택으로 FP=1% 탐지율 18.9%→46.1%(+27.2pp) 달성.

| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |
|---|---|---|---|---|
| FN4-status | 0.0% | 0.6% | 3.9% | ⚠️ weak |
| D1-LowSlow | 0.0% | 4.9% | 17.9% | ⚠️ weak |
| FN2-속도단계 | 0.3% | 13.4% | 25.4% | ⚠️ weak |
| G5-ZigzagAccel | 2.2% | 51.2% | 71.0% | ⚠️ weak |
| 위치점프 | 9.8% | 67.2% | 87.2% | ⚠️ weak |
| F5-MultiCoord | 12.4% | 63.5% | 85.2% | ⚠️ weak |
| G2-SpeedBurst | 14.3% | 40.7% | 65.0% | ⚠️ weak |
| E5-Shadow | 15.7% | 80.9% | 98.3% | ⚠️ weak |
| 속도이상 | 16.0% | 59.4% | 79.9% | ⚠️ weak |
| 정박이동 | 18.8% | 64.3% | 81.4% | ⚠️ weak |
| E4-Contextual | 32.0% | 63.8% | 73.9% | ⚠️ weak |
| D2-Temporal | 32.2% | 83.1% | 95.3% | ⚠️ weak |
| F7-LSTMBeat | 37.0% | 69.6% | 82.3% | ⚠️ weak |
| FN3-COG경계 | 38.0% | 66.6% | 75.0% | ⚠️ weak |
| F4-TimeSkew | 38.6% | 64.6% | 89.2% | ⚠️ weak |
| E3-WinEdge | 41.3% | 84.1% | 95.9% | ⚠️ weak |
| F6-AISGap | 48.8% | 93.0% | 99.0% | ⚠️ weak |
| D3-GradDrift | 50.8% | 76.4% | 84.8% |  |
| E2-Desync | 53.5% | 90.5% | 97.4% |  |
| COG/HDG불일치 | 53.9% | 73.8% | 80.8% |  |
| F3-TrajStitch | 61.0% | 91.7% | 98.7% |  |
| G6-LandRoute | 63.9% | 77.5% | 97.5% |  |
| FN1-dt점프 | 74.5% | 100.0% | 100.0% |  |
| D4-Mimicry | 78.3% | 93.9% | 98.3% |  |
| G3-PhantomHDG | 85.1% | 99.2% | 99.9% |  |
| F2-Intermit | 86.5% | 92.0% | 94.8% |  |
| G7-MMSISpoof | 87.8% | 99.6% | 100.0% |  |
| F1-FeatSmooth | 89.8% | 99.7% | 99.9% |  |
| G1-CircularLoop | 93.2% | 100.0% | 100.0% |  |
| E1-Smooth | 94.3% | 99.9% | 100.0% |  |
| G4-StatusFlicker | 98.1% | 99.9% | 100.0% |  |

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
