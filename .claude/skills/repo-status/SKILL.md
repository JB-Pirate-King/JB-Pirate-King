---
name: repo-status
description: Report and review how the local repo diverges from its remotes (origin + upstream org). Fetches (read-only, never merges), computes ahead/behind per branch, lists unpushed/unpulled commits and working-tree state, then reviews the divergent diff for secrets, WIP, conflict risk, and code issues. Use when the user says "repo status", "remote diff", "local vs github", "원격 비교", "로컬 원격 차이", "동기화 상태", "변경 상황 보고".
---

# repo-status — report + review local ↔ remote divergence

Answers "what's different between my local repo and GitHub, and is it safe to push/pull?" in one pass: a **status report** (what diverges) plus a **review** (what's risky in that divergence). Read-only — it inspects and recommends, it never mutates history.

## Hard safety rules (read first)

- **Read-only git only.** `git fetch` (no merge), `git log`, `git diff`, `git status`, `git rev-list`, `gh ... view/list`. **NEVER** push, pull, merge, rebase, reset, commit, stage, or create PRs. Recommend actions; let the user run them.
- **`git fetch` only updates remote-tracking refs** — it does not touch the working tree or local branches. That is the one network call allowed.
- **NEVER run while an orchestrator run is in progress** (shared working tree). If `ml/logs/` shows an active run or a `dcdetect_NNN` branch is checked out mid-run, STOP and tell the user.
- Report prose to the user: Korean. Any code/diff snippets quoted verbatim.

## Step 1 — Fetch + map remotes

- `git remote -v` — enumerate remotes. This repo's convention: `origin` = personal fork (heahgo), `upstream` = org (`JB-Pirate-King/JB-Pirate-King`). `upstream` may be **detached/missing** — if so, note it and compare against `origin` only (do not add the remote yourself; tell the user the command if they want org comparison).
- `git fetch --all --prune` — refresh remote-tracking refs (read-only, no merge).
- `git rev-parse --abbrev-ref HEAD` — current branch.

## Step 2 — Compute divergence

For the current branch (and any branch the user named) against each relevant remote ref:

- **ahead/behind**: `git rev-list --left-right --count HEAD...<remote>/<branch>` → `<ahead> <behind>`.
- **unpushed** (local-only): `git log --oneline <remote>/<branch>..HEAD`.
- **unpulled** (remote-only): `git log --oneline HEAD..<remote>/<branch>`.
- **working tree**: `git status --porcelain` → split into staged / modified / untracked.
- **file surface of the divergence**: `git diff --stat <remote>/<branch>...HEAD` (and `git diff --stat` for uncommitted).

If both origin and upstream exist, do this for both (e.g. `origin/main` and `upstream/main`) — the gaps often differ.

## Step 3 — Review the divergence

Scan the divergent diff (unpushed commits + working-tree changes) and flag, one line each:

- **Secret leak** — added/changed lines or newly-tracked files matching `notify_config.json`, `google_credentials.json`, `pipeline_config.json`, `.env`, or `token`/`secret`/`key`/`password` patterns. Cross-check `.gitignore` actually excludes them (`git check-ignore <path>`). This is the highest-priority finding.
- **Conflict risk** — behind > 0 **and** working tree dirty, or unpushed + unpulled both non-empty (divergent histories → a plain push will be rejected, a pull may conflict).
- **WIP / debris** — `TODO`/`FIXME`/`XXX`/`print(`-debug/commented-code added in the divergent diff; large or binary files; files that look accidentally staged.
- **Code issues** — for a non-trivial code diff, optionally delegate to **`caveman:cavecrew-reviewer`** (pass the unpushed diff or changed files) for severity-tagged one-line findings. Skip for docs-only or tiny diffs.

## Step 4 — Report

Korean summary, in this order:

1. **상태 한 줄**: `<branch>` is ahead N / behind M vs `origin` (and upstream if present), working tree clean/dirty.
2. **미푸시 커밋** (local-only) — oneline list.
3. **미수신 커밋** (remote-only) — oneline list.
4. **파일 변경** — `--stat` surface (commits + uncommitted), grouped if large.
5. **리뷰 경고** — Step 3 findings, secret-leak first. If none, say "경고 없음".
6. **권장 액션** — concrete git commands (push / pull --rebase / open PR), but **do not run them** — the user decides.
