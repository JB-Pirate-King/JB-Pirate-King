---
name: sync-docs
description: Update project documentation to match code changes. Fans out parallel agents to sync README.md, ml/README.md, CLAUDE.md, and ml/PIPELINE.md against the current diff, and refreshes the LangGraph graph artifacts (PIPELINE.md mermaid block + ml/pipeline_langgraph.png) when the orchestrator graph changed. Use when the user says "sync docs", "update the docs", "docs 동기화", "그래프 갱신", or after a code/structure change before pushing.
---

# sync-docs — keep docs in sync with code (parallel fan-out)

Enforces the project rule: *a code/structure change isn't "done" until README + ml/README + CLAUDE.md reflect it* (see CLAUDE.md "Pre-Push Checklist"). This skill detects what changed and updates each doc target **in parallel**, one subagent per target. (Notion is intentionally out of scope for this skill.)

## Hard safety rules (read first)

- **NEVER run while an orchestrator run is in progress.** Shared working tree -> the run's auto-commit would sweep staged changes. If `ml/logs/` shows an active run or a `dcdetect_NNN` branch is checked out mid-run, STOP and tell the user.
- **Do NOT git commit / stage / rm.** This skill only edits doc files. Committing is the user's call.
- **Docs language: English** (project convention) for README / ml/README / CLAUDE.md. `ml/PIPELINE.md` is a **Korean** doc — keep it Korean. Only the final summary spoken to the user stays Korean.

## Step 1 — Detect what changed

Gather the change surface (read-only):

- `git status --porcelain` and `git diff` (working tree) for uncommitted edits.
- `git diff develop...HEAD` or `git log --oneline -15` for recent committed changes on the branch.
- Identify changed **features, file paths, CLI options, node/flow changes, config keys**. These are what docs must reflect.

If nothing meaningful changed, say so and stop — do not churn docs.

## Step 2 — Map changes -> doc targets

Decide which of these are affected (skip untouched ones):

| Target | Owns |
|---|---|
| `README.md` | top-level project overview, run results, high-level features |
| `ml/README.md` | ML pipeline usage, commands, options, paths |
| `CLAUDE.md` | architecture/state for a fresh session: nodes, flow, flags, gotchas |
| `ml/PIPELINE.md` | LangGraph node-level detail: node catalog, routing, State, judge/gate behavior (Korean doc) |

## Step 3 — Fan out (parallel)

Spawn one subagent **per affected target, all in a single message** (this is what makes it parallel). Targets edit different files -> no conflict -> no worktree isolation needed.

Each agent gets:
- the relevant slice of the diff (what changed),
- its single doc target,
- instruction: *update ONLY this doc to reflect the changes; match the existing structure, heading style, comment density, and English wording; do not invent features not in the diff; do not touch other files.*

Use `caveman:cavecrew-builder` for the markdown-file targets (bounded 1-file edits).

**Language note:** README.md / ml/README.md / CLAUDE.md are **English**. `ml/PIPELINE.md` is the
exception — it is a **Korean** doc; its agent must match the existing Korean wording/table style.

## Step 3b — Refresh the graph artifacts (only if the orchestrator graph changed)

Run this **in the main context** (it is a deterministic script + a single mermaid-block swap, not
an LLM edit — no fan-out). **Skip entirely** if the diff did not touch the graph topology
(`build_graph`, node/edge wiring, or `n_*` node set in `ml/orchestrator.py`) — do not churn the PNG.

1. Extract the authoritative mermaid (always in sync with code):
   ```
   PYTHONUTF8=1 python -c "from ml.orchestrator import build_graph; print(build_graph().get_graph().draw_mermaid())"
   ```
2. Replace the ```` ```mermaid ```` code block in `ml/PIPELINE.md` (section "2. 구조도", the block
   right after the `draw_mermaid()` regen note) with the freshly extracted source.
3. Re-render the colored PNG (reuses the existing renderer — do not duplicate it):
   ```
   PYTHONUTF8=1 python -m ml.scripts.render_graph
   ```
   → refreshes `ml/pipeline_langgraph.png`. This also prints `[경고] 미분류 노드 …` if a new node
   was added but not classified in `render_graph.py` GROUPS — **surface that warning to the user**
   (it means `render_graph.py` GROUPS needs a one-line update). `draw_mermaid_png` calls mermaid.ink,
   so on a network failure treat the PNG step as best-effort (skip, keep the mermaid-text refresh).

## Step 4 — Report

After agents return, give the user a Korean summary:
- which docs were updated and the key lines changed,
- whether the graph artifacts were refreshed (mermaid block + PNG) or skipped (graph unchanged),
- anything skipped (untouched) and why,
- remind: `/change-report` for a changelog/release note if they want one,
- remind: commit is their call (do not auto-commit).
