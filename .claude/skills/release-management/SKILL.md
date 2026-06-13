---
name: release-management
description: Reference for releases and versioning: automated per-run prerelease tags, manual stable releases, the version bump scheme, and version history. Use when creating a release, tagging a version, or deciding a version bump for this project.
---

## Release & Version Management

### Automated Run Releases (prerelease)

Auto-created by the orchestrator on FE completion:
- Tag: `run/dcdetect_NNN` (prerelease)
- Target: commit SHA (a branch name triggers a 422 error)
- Attachments: 3 model files (`model_dcdetect.onnx`, `scaler_dcdetect.json`, `threshold_dcdetect.txt`)
- The plugin tar.gz can only be built on Linux — attach manually

### Stable Releases (manual)

```bash
git checkout main && git merge develop
git tag v1.0.0 && git push origin main --tags
gh release create v1.0.0 \
  --title "v1.0.0 — dcdetect 24 features" \
  --notes "..."
```

### Version Scheme

| Bump | When |
|---|---|
| **major** | Feature-count/interface change (12→N), SEQ_LEN change |
| **minor** | New model, new eval scenarios, large detection-rate gain |
| **patch** | Threshold retune, bug fix, same-structure retrain |

### Version History

| Version | Date | Notes |
|---|---|---|
| v0.1.0 | — | Initial release (conv1d, tranad, dcdetect, 1-day data) |
| v0.2.0 | 2026-05-22 | dcdetect 12 features, 3yr data |
| run/dcdetect_001~012 | 2026-05-29 | Greedy FE automation runs (prerelease, 13–24 features) |
