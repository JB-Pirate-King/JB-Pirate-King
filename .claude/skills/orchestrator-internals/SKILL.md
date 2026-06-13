---
name: orchestrator-internals
description: Reference for the pipeline orchestrator internals: the per-stage Slack message and approval flow with Claude analysis, and the LangGraph node/gate/checkpointer structure. Use when working on orchestrator.py, pipeline_steps.py, the LangGraph graph, Slack approval gates, or per-stage reporting.
---

### Slack messages & Claude analysis / approval

Each stage reports to Slack in this flow (`integrations/slack_bot.py`):

```
📍 [1/2] ■□  *Feature Engineering*  →  next: pipeline end          ← log_stage_start
📊 baseline (12 feat): FP=1% detection 40.6% · objective 77.2      ← fe_progress (live)
⚠️ 5 weak scenarios (baseline <50%): D1-LowSlow(0%), F3(12%)...
🔬 candidate #1/20 `accel` — acceleration Δsog/dt
   └ `accel`: detection 43.6% (+3.0pp) · objective 80.5 (+3.3) → ✅ meets bar (≥+3.0)
🔬 candidate #2/20 `turn_rate` — COG change rate
   └ `turn_rate`: detection 33.6% (-6.7pp) · objective 62.0 (-11.9) → ⬜ below bar
   ... (20 candidates)
🏆 adopted! `accel` — FP=1% 40.6% → 43.6% | objective +3.3
🔁 retrain final model on adopted set (13 feat) — this is the deployable model
🧠 final training 100% — Epoch 1/1 (train=0.019 val=0.005)
  ✅ final training done — best val MSE 0.005150
📊 evaluating final model (FP=1%/5%/10% + per-scenario)...
📈 final detection — FP=1%: 43.6% · FP=5%: 58.2% · FP=10%: 67.0%
🎯 deploy threshold (FP=1% normal 99th pct): 0.00491234
✅/❌ [FE done] + candidate table + feature-importance table (log_stage_result / log_table)
```

**Claude analysis** (`claude_analyze` → `claude -p`): called at each stage end.
- Prompt: `[stage] result analysis / success·elapsed / extra info (JSON) / last 60 lines of run output`
  → returns three things: ① result assessment (numbers) ② cause/evidence ③ next action `continue`/`retry`/`stop` + reason.
- Slack output: `🤖 *Claude analysis*` + answer lines. (If the `claude` CLI is missing, shows "analysis unavailable".)

**Approval gates** (`_wait`) — per stage within a branch:
- **Gate ① after FE eval**: Claude analysis + candidate table → "proceed to deploy?" (✅proceed / 🔄rerun FE / ❌stop)
- **Gate ② after plugin build**: build result → "commit + release?" (✅proceed / ❌stop)
- **Gate ③ on convergence**: if nothing adopted, confirm "end pipeline?"
- `--auto_approve` **ON**: all gates auto-pass (if the summary contains `❌`, returns `stop`). Slack shows only a `🤖 [auto_approve] … → approve` log → unattended branch chaining.
- **OFF**: each gate waits on a Slack button.

---

## LangGraph Orchestrator (`ml/orchestrator.py`)

`orchestrator.py` is a LangGraph `StateGraph` (control flow); the heavy execution functions
live in `ml/pipeline_steps.py` (shared step library). Design diagram: `graph.md` / `pipeline_full.png`.

- **pipeline_steps.py** — `run_cmd`, output parsers, `claude_analyze`, `stage_preprocess`,
  `stage_build_plugin`, `stage_release`, `_fe_train_eval` (greedy 1-step train+eval+parse+log),
  `_fe_build_and_release`, `_fe_commit_release`, fe_state io, constants. Not an entry point.
- **FE decomposition**: `fe_baseline` node (`feature_engineer --diagnose_only` → baseline det +
  weak scenarios) is split from `fe_train` (scan→adopt→retrain→importance→finaleval→export, one
  `feature_engineer` subprocess via `_fe_train_eval`).
- **claude feature recommendation (`n_recommend`)**: weak-scenario diagnosis → `claude -p` proposes
  N new candidate features (name + lambda) → validated (exec on dummy seq + dedup) → written to
  `ml/dynamic_candidates.py` (gitignored) → `feature_engineer` loads + scans them. Convergence
  (no adoption) re-recommends from a different angle up to `--invent_rounds`. Enable with `--invent N`.
- **per-node claude harness** (`claude_harness` factory): each compute node is followed by a harness
  node that runs `claude -p --output-format json` → `{assessment, verdict: continue|retry|stop, ...}`
  → routing. Toggle per node via `HARNESS_ON` set; `--no_harness` disables all.
- **interrupt() gates**: deploy / release / converge are independent `interrupt()` nodes. Because
  gates sit at node boundaries, a crash while awaiting Slack approval resumes **without retraining**.
- **Sheets**: `log_sheet(kind)` DRY factory (`run_start|fe|run_done|converge`).
- **Chaining = graph cycle**: `release → chain → new_branch`. Convergence → `converge → END`.
  `--max_runs` is a state-counter guard; `recursion_limit = max_runs × 25`.
- **Checkpointer**: `MemorySaver` (in-process). Swap to `SqliteSaver` for cross-restart resume.
- **Runner**: `run_pipeline` polls `__interrupt__`, gets the Slack decision via `bot.wait_approval`,
  resumes with `Command(resume=decision)`.
- **Launch**: `python -m ml.orchestrator` (same flags as before + `--invent`, `--invent_rounds`,
  `--no_harness`).
