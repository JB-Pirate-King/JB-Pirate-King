---
name: change-report
description: Detect code and GitHub changes, then write a change report / changelog / release note. Pulls local git diff+log and (if gh is authed) merged PRs, releases, and issues, synthesizes a dated report, and appends to CHANGELOG.md. Use when the user says "change report", "changelog", "what changed", "변경사항 정리", "릴리스 노트".
---

# change-report — detect changes and write them up

Turns "what changed?" into a written artifact. Reads (never mutates) git + GitHub, synthesizes a report, and records it. Separate from `/sync-docs` (which edits the living docs); this one PRODUCES a changelog/report.

## Hard safety rules

- **Read-only on git/GitHub.** Use `git log`/`git diff`/`gh ... list`/`gh ... view` only. No commit, push, stage, or release creation.
- **Do not run while an orchestrator run is in progress** (shared working tree). If unsure, check `ml/logs/` for an active run first.
- **External publish (Slack/Notion/PR comment): confirm first.** Writing a local file is fine; sending outward needs the user's OK.
- Report prose: English (project convention). The spoken summary to the user: Korean.

## Step 1 — Gather sources

Local (always):
- `git log --oneline -30` and, if a last tag exists, `git log <last_tag>..HEAD` — the commit timeline.
- `git diff --stat` for the changed-file surface; `git diff` for detail where needed.
- `git log origin/main..HEAD --oneline` for unpushed commits (if remote present).

GitHub (only if available — check `gh auth status` first):
- `gh pr list --state merged --limit 20` — merged PRs (the cleanest change units).
- `gh release list` + `gh release view <tag>` — released changes.
- `gh issue list --state all --limit 20` — context for what the changes address.
- **Note:** the `upstream` remote may be detached (see HANDOFF.md). If GitHub data is needed and the remote is missing, tell the user the exact command to add it (`git remote add upstream https://github.com/JB-Pirate-King/JB-Pirate-King`) rather than adding it yourself.

If `gh` is not authed or no remote, proceed with local-only sources and say so in the report.

## Step 2 — Synthesize the report

Get the date from `git log -1 --format=%cd --date=short` (do not guess). Build:

```
## <date> — <one-line headline>

### Code changes
- <area>: <what changed and why> (file paths, options, flags)

### Merged PRs           (omit if no gh)
- #<n> <title> — <effect>

### Releases             (omit if none)
- <tag> — <summary>

### Affected docs / follow-ups
- <doc or task that now needs updating> -> suggest running /sync-docs
```

Group by area (orchestrator / pipeline_steps / feature_engineer / plugin / docs). Be concrete: name files, flags, node names. No filler.

## Step 3 — Record + report

- **Append** the section to `CHANGELOG.md` at repo root (create it with a `# Changelog` header if absent). Newest entry on top.
- Optionally also write `docs/changes/<date>.md` if the user wants a standalone file (ask if unclear).
- Speak a short Korean summary to the user: headline + how many commits/PRs covered + which docs to sync next.
- Do NOT commit. Remind the user the changelog edit is staged for them to commit when ready.
