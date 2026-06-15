---
name: sync-docs
description: Update project documentation to match code changes. Fans out parallel agents to sync README.md, ml/README.md, and CLAUDE.md against the current diff. Use when the user says "sync docs", "update the docs", "docs 동기화", or after a code/structure change before pushing.
---

# sync-docs — keep docs in sync with code (parallel fan-out)

Enforces the project rule: *a code/structure change isn't "done" until README + ml/README + CLAUDE.md reflect it* (see CLAUDE.md "Pre-Push Checklist"). This skill detects what changed and updates each doc target **in parallel**, one subagent per target. (Notion is intentionally out of scope for this skill.)

## Hard safety rules (read first)

- **NEVER run while an orchestrator run is in progress.** Shared working tree -> the run's auto-commit would sweep staged changes. If `ml/logs/` shows an active run or a `dcdetect_NNN` branch is checked out mid-run, STOP and tell the user.
- **Do NOT git commit / stage / rm.** This skill only edits doc files. Committing is the user's call.
- **Docs language: English** (project convention). Only the final summary spoken to the user stays Korean.

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

## Step 3 — Fan out (parallel)

Spawn one subagent **per affected target, all in a single message** (this is what makes it parallel). Targets edit different files -> no conflict -> no worktree isolation needed.

Each agent gets:
- the relevant slice of the diff (what changed),
- its single doc target,
- instruction: *update ONLY this doc to reflect the changes; match the existing structure, heading style, comment density, and English wording; do not invent features not in the diff; do not touch other files.*

Use `caveman:cavecrew-builder` for the markdown-file targets (bounded 1-file edits).

## Step 4 — Report

After agents return, give the user a Korean summary:
- which docs were updated and the key lines changed,
- anything skipped (untouched) and why,
- remind: `/change-report` for a changelog/release note if they want one,
- remind: commit is their call (do not auto-commit).
