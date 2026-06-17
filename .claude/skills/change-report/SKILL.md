---
name: change-report
description: Detect code and GitHub changes and write a change report into Notion. Pulls local git diff+log and (if gh is authed) merged PRs/releases/issues, optionally folds in meeting notes (회의록) to check decisions vs. actual code, synthesizes a dated report, and publishes it as a Notion page (primary output), with a local CHANGELOG.md copy. Use when the user says "change report", "changelog", "what changed", "변경사항 정리", "릴리스 노트", "회의록 보고서", "브리핑 보고서".
---

# change-report — detect changes and write them up **into Notion**

Turns "what changed?" into a written artifact. Reads (never mutates) git + GitHub, synthesizes a dated report, and **publishes it to Notion** (the primary target — always). Separate from `/sync-docs` (which edits the living markdown docs); this one PRODUCES a changelog/report and posts it to Notion.

## Hard safety rules

- **Read-only on git/GitHub.** Use `git log`/`git diff`/`gh ... list`/`gh ... view` only. No commit, push, stage, or release creation.
- **Do not run while an orchestrator run is in progress** (shared working tree). If unsure, check `ml/logs/` for an active run first.
- **Notion publish is the intended action here** — the user wants every report on Notion. Do it without re-asking each run. BUT the first time in a session (or when the parent page is unknown), confirm WHICH Notion page/space to post under, then reuse it.
- **Do the Notion write from the main context, not a subagent** (cavecrew/limited subagents have no MCP access).
- Report prose: English (project convention). The spoken summary to the user: Korean.

## Step 0 — Meeting notes (회의록), optional

The roadmap wants reports to fold in "PR context **+ latest meeting notes** + files". Gather meeting notes when available so the report can check decisions against actual code.

Source priority:
1. **Path argument** — if the user passed a file/folder path (or names one), read those `.md` notes.
2. **Fallback scan** — else glob `team-vault/자료/**/*회의록*.md` and take the most recent (by `last_synced` frontmatter or filename date). `team-vault/` is a read-only Notion mirror — read only, never write.

From the notes, extract: decisions made, action items (owner + task), and any dated follow-ups. Hold these for Steps 2's `## 회의 맥락` and `## 정합성` sections.

If no notes are found, print one line (`회의록 없음 — git/gh 변경만으로 보고서 생성`) and proceed; everything below is unchanged (backward compatible). Do not block on missing notes.

## Step 1 — Gather sources

Local (always):
- `git log --oneline -30` and, if a last tag exists, `git log <last_tag>..HEAD` — the commit timeline.
- `git diff --stat` for the changed-file surface; `git diff` for detail where needed.
- `git log origin/main..HEAD --oneline` for unpushed commits (if remote present).

GitHub (only if available — check `gh auth status` first):
- `gh pr list --state merged --limit 20` — merged PRs (the cleanest change units).
- `gh release list` + `gh release view <tag>` — released changes.
- `gh issue list --state all --limit 20` — context for what the changes address.
- **Note:** the `upstream` remote may be detached (see CLAUDE.md). If GitHub data is needed and the remote is missing, tell the user the exact command to add it (`git remote add upstream https://github.com/JB-Pirate-King/JB-Pirate-King`) rather than adding it yourself.

If `gh` is not authed or no remote, proceed with local-only sources and say so in the report.

## Step 2 — Synthesize the report (fixed format)

Get the date from `git log -1 --format=%cd --date=short` (do not guess). Produce the report in **exactly this structure and order**. Omit a section only when it is empty (and say nothing about the omission).

```
# Change Report — <YYYY-MM-DD>

> <one-line headline: the single most important change this batch>
> Scope: <commit range or "uncommitted working tree"> · <N commits> · <N merged PRs>

## Summary
<2–4 sentence plain-English overview: what changed and why it matters. No lists here.>

## Meeting context      (omit unless Step 0 found notes)
<source file + date>
- <decision or action item from the notes, one per bullet>

## Alignment            (omit unless Step 0 found notes)
Meeting decisions vs. actual code changes this batch.
- ✅ done: <decision> → <commit/PR that implements it>
- ⬜ not done: <decision with no matching change>
- ➕ unplanned: <code change not traceable to any decision>

## Code changes
Grouped by area, one bullet per distinct change.
- **orchestrator**: <what changed> (`file:func`, flags/nodes touched)
- **pipeline_steps**: ...
- **feature_engineer**: ...
- **plugin (ais_ids_pi)**: ...
- **docs / config**: ...

## Merged PRs           (omit if no gh data)
- #<n> <title> — <one-line effect>

## Releases             (omit if none)
- <tag> — <summary> (prerelease? attached artifacts?)

## Metrics              (omit if no detection-rate / threshold change)
- <scenario or FP level>: <before> → <after> (<±pp>)

## Affected docs / follow-ups
- [ ] <doc that is now stale> → run `/sync-docs`
- [ ] <open task or risk introduced>
```

**Format rules:**
- Headline + Scope line are mandatory; everything else is section-gated by content.
- `## Meeting context` + `## Alignment` appear only when Step 0 found notes; drop both otherwise (the report stays exactly as before).
- Group Code changes by area: `orchestrator / pipeline_steps / feature_engineer / plugin / docs / config`. Drop empty areas.
- Be concrete: name files (`ml/orchestrator.py`), functions/nodes (`_logged_node`, `fe_train`), flags (`--invent_rounds`). No filler, no praise, no hedging.
- Past tense, English. One change per bullet. Newest report on top in CHANGELOG.md.
- Follow-ups use `- [ ]` checkboxes so they render as actionable in Notion.

## Step 3 — Publish to Notion (primary, always)

Publish via **`notify.py` and the Notion API token** (no MCP). `ml/integrations/notify.py` has `send_notion_report(title, summary, report_text)`, which posts a child page under the parent page configured in `ml/config/notify_config.json` (gitignored: `notion_token`, `notion_parent_page_id`, `notion_version`).

1. Write the Step 2 report to a temp file (e.g. `ml/.pipeline_tmp/change_report_<date>.md`), then call `send_notion_report` with the report text read back from that file. Pass:
   - `title` = `Change Report — <date>`
   - `summary` = the one-line headline (rendered as the callout block)
   - `report_text` = the full Step 2 body
   - Run from repo root so config + imports resolve. Example:
     ```powershell
     $env:PYTHONUTF8="1"
     python -c "from ml.integrations.notify import send_notion_report; t=open(r'ml/.pipeline_tmp/change_report_<date>.md',encoding='utf-8').read(); print(send_notion_report('Change Report — <date>','<headline>',t))"
     ```
   - `True` printed = page created. `False` = token/parent missing (it prints why).
2. **Token not set** (`send_notion_report` returns `False` with `notion_token 미설정`) — tell the user to fill `notion_token` + `notion_parent_page_id` in `ml/config/notify_config.json`. Still write the local backup below so nothing is lost.

Always also write a **local backup**: append the Step 2 section to `CHANGELOG.md` at repo root (create with a `# Changelog` header if absent), newest on top.

## Step 4 — Report

Speak a short Korean summary to the user:
- the headline + how many commits/PRs the report covered,
- whether the Notion page was created (`True` from `send_notion_report`) or why it was skipped (token/parent missing),
- which docs to sync next (`/sync-docs`),
- note: local `CHANGELOG.md` updated; commit is their call (do not auto-commit).
