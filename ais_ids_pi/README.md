# ais_ids_pi — OpenCPN AIS anomaly-detection plugin (C++)

OpenCPN plugin that runs the trained ML model (ONNX) on live AIS tracks and flags anomalous
ships. This is the deployment target of the ML pipeline in `../ml/`.

## Build & deploy (native Linux only)

Windows is used only for ML training; the plugin is built and deployed on native Linux
(Ubuntu 24.04 / noble). WSL is not the target.

```bash
git submodule update --init --recursive   # opencpn-libs is a submodule (first build)
./local-build-package.sh                   # from this directory
# Output: ais_ids_pi-<version>-ubuntu-x86_64-24.04-noble.tar.gz
```

ONNX Runtime is bundled under `onnxruntime/{include,lib}`.

## Layout

| Path | Purpose |
|---|---|
| `src/ais_ids.cpp` | Plugin main source |
| `include/ais_ml.h` | ML interface; holds `ML_FEATURE_COUNT` and `[AUTO:feat_block]`/`[AUTO:push_decl]` codegen markers |
| `src/ais_ml.cpp` | ML inference impl; `[AUTO:push_impl]` marker |
| `data/` | Bundled model: `model.onnx`, `scaler.json`, `threshold.txt` |
| `local-build-package.sh` | cmake + make package build |
| `opencpn-libs/` | git submodule |
| `onnxruntime/` | bundled ONNX Runtime |

## Model deployment

The pipeline exports `model_{name}.onnx` / `scaler_{name}.json` / `threshold_{name}.txt`; the
plugin loads fixed names `model.onnx` / `scaler.json` / `threshold.txt`. Runtime load location
is `GetpPrivateApplicationDataLocation()/plugins/ais_ids_pi/data/` (on Linux
`~/.opencpn/plugins/ais_ids_pi/data/`). `local-build-package.sh` installs `data/` there.

`ML_FEATURE_COUNT` (in `include/ais_ml.h`) must match the deployed model's feature count.
The orchestrator auto-patches the `[AUTO:*]` regions via `ml/core/patch_plugin.py` on FE adoption.

## See also

- Project rules and the full plugin/patch/build details: root `CLAUDE.md`.
- ML pipeline that produces the model: `../ml/README.md`.
