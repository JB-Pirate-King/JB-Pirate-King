---
name: read-docs
description: Read the project's real markdown docs and emit an area-grouped summary, skipping vendored library READMEs. Fans out parallel read-only agents so the main context stays small. Use when the user says "md 다 읽어", "문서 읽기", "문서 요약", "프로젝트 파악", "read docs", "read all the markdown".
---

# read-docs — read project docs, summarize by area, skip vendor noise

Turns "read all the md files" into a repeatable, token-cheap action. Reads (never mutates) the repo's **own** markdown, ignores third-party vendored READMEs, and returns one area-grouped summary. Replaces doing it by hand every session.

## Hard safety rules

- **Read-only.** Read/Glob/Explore only. No edit, write, commit, or config change. This skill never mutates a file.
- **team-vault/ is a read-only Notion mirror** — read it, never write it (next sync overwrites anything you'd add).
- Spoken summary to the user: Korean. (Project docs themselves are English/Korean as-is — quote, don't translate.)

## What counts as a "real" doc (include) vs vendor noise (exclude)

**Exclude** (third-party, vendored — never read these):
- `ais_ids_pi/opencpn-libs/**` (jsoncpp, libusb, muparser, plugin_dc, api-18/19/20/21, flatpak, WindowsHeaders …)
- `ais_ids_pi/onnxruntime/**` (Privacy.md, README.md)

Everything else under the repo is a real project/team doc.

## Step 1 — Enumerate

Glob `**/*.md` from repo root, then drop anything matching the exclude globs above. Report the count: `<N> real docs, <M> vendored skipped`. (Baseline at authoring time: ~21 real, ~17 vendored — if the numbers drift a lot, say so; new docs are fine.)

## Step 2 — Read via parallel Explore agents (token-cheap)

Do NOT read all files inline — that floods the main context. Instead fan out **Explore subagents in parallel** (one message, multiple Agent calls), one per group below. Tell each agent: read-only, return a compact factual summary (key facts + file refs), no recommendations.

| Group | Files |
|---|---|
| root | `README.md`, `CLAUDE.md` |
| ml | `ml/README.md`, `ml/PIPELINE.md` |
| component | `s-c/Readme.md`, `aivdm_gen/README.md` |
| team-vault research/ref | `team-vault/README.md`, `team-vault/SETUP-팀원.md`, `team-vault/자료.md`, `team-vault/자료/**/*.md` |
| skills | `.claude/skills/*/SKILL.md` |

If Step 1 found docs outside these groups, add them to the closest group (or a new "기타" group) — don't silently drop them.

> Small-corpus shortcut: if only a handful of real docs exist (≲6), reading them inline is fine; skip the fan-out.

## Step 3 — Emit area-grouped summary

Output one Korean summary with a heading per group (root / ml / component / team-vault / skills). Per doc: 1–2 lines of substance (what it covers, key facts). End with: `벤더 <M>개 스킵 (opencpn-libs, onnxruntime)`. Keep the cross-cutting throughline if obvious (e.g. AIS 이상탐지 / DCdetector / OpenCPN 플러그인).
