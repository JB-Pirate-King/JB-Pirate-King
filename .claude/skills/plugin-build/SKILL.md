---
name: plugin-build
description: Reference for deploying a trained model into the OpenCPN C++ plugin and building it: model file path rules, runtime load location, C++ AUTO: patch markers, patch_plugin.py, and the native-Linux build and deploy. Use when working on the plugin build, ais_ids_pi, deploying a model, or AUTO: codegen markers.
---

## Model File Path Rules

- Trained (per model): `D:\ais_models\{name}\model_{name}.onnx`, `scaler_{name}.json`, `threshold_{name}.txt`
- Plugin source (bundled into the build): `ais_ids_pi/data/model.onnx`, `scaler.json`, `threshold.txt`
- **Runtime load location** (`g_pData`, `ais_ids_pi.cpp:157`): `GetpPrivateApplicationDataLocation()/plugins/ais_ids_pi/data/` → on Linux `~/.opencpn/plugins/ais_ids_pi/data/`. `local-build-package.sh` copies `ais_ids_pi/data/` here (`DATA_DEST`), so the two paths match.

### Deploying a trained model to the plugin

Training exports `model_{name}.onnx` / `scaler_{name}.json` / `threshold_{name}.txt`, but the plugin loads **fixed names** (fallback when no `ensemble_config.json`): `model.onnx` / `scaler.json` / `threshold.txt` (`ais_ids.cpp` `LoadMLFromConfig`). Rename into the runtime load location:

```bash
DEST="$HOME/.opencpn/plugins/ais_ids_pi/data"
mkdir -p "$DEST"
cp model_{name}.onnx     "$DEST/model.onnx"
cp scaler_{name}.json    "$DEST/scaler.json"
cp threshold_{name}.txt  "$DEST/threshold.txt"
```

The orchestrator's `stage_build_plugin` does this rename-copy into `ais_ids_pi/data/`, and run-release notes embed the same `$HOME/.opencpn/...` deploy snippet. `local-build-package.sh` then installs `ais_ids_pi/data/` to the runtime location on a native-Linux build.

---

## Plugin Auto-Patch & Build

Run automatically by the orchestrator on FE adoption. Manual run:

```bash
# 1. Patch C++ code (dry_run first to inspect)
python ml/core/patch_plugin.py --scaler D:/ais_models/dcdetect/scaler_dcdetect.json --dry_run
python ml/core/patch_plugin.py --scaler D:/ais_models/dcdetect/scaler_dcdetect.json

# 2. Linux build (native Linux only)
./local-build-package.sh   # from ais_ids_pi/
# Output: ais_ids_pi-<version>-ubuntu-x86_64-24.04-noble.tar.gz
```

**AUTO: marker locations** (C++ auto-patch regions):
- `ais_ml.h`: `[AUTO:feat_block]` (ML_FEATURE_COUNT + feature comments), `[AUTO:push_decl]`
- `ais_ml.cpp`: `[AUTO:push_impl]`
- `ais_ids.cpp`: `[AUTO:extra_feats]`, `[AUTO:push_calls]`

---

## Plugin Build & Deploy (native Linux ONLY)

**The OpenCPN plugin is built and deployed on native Linux. Windows is used only for ML model training.**

- Target: Ubuntu 24.04 (noble)
- `ais_ids_pi/opencpn-libs/` is a git submodule. Before first build: `git submodule update --init --recursive`
- ONNX Runtime bundled at `ais_ids_pi/onnxruntime/{include,lib}`
- Build command (from `ais_ids_pi/`): `./local-build-package.sh`
- C++ feature count hardcoded: `ML_FEATURE_COUNT` in `ais_ids_pi/include/ais_ml.h`. Must match the deployed model.

> The orchestrator's `--build_plugin` flag (WSL build) is opt-in and **off by default** — the canonical plugin build is native Linux.
