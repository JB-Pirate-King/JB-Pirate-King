---
name: config-lint
description: Lint AI-assistant config files (CLAUDE.md, SKILL.md, AGENTS.md, hooks, .mcp.json) with agnix before pushing or after editing them. Use when the user edits CLAUDE.md or a skill, asks to validate / check the Claude config, or wants to catch broken skill frontmatter, malformed MCP entries, oversized CLAUDE.md, or hook mistakes.
---

# Config lint (agnix)

agnix는 AI 코딩 어시스턴트 설정 파일 전용 린터다. 이 레포는 큰 `CLAUDE.md` + 스킬 다수 +
`.mcp.json` 을 유지하므로, 이들이 깨지면(스킬 frontmatter 오류, MCP 항목 오타, CLAUDE.md
과대 등) 조용히 동작이 어긋난다. agnix가 정적으로 잡아준다(422룰).

## 실행

```powershell
# 레포 전체 설정 파일 린트
npx agnix .

# 특정 파일만
npx agnix CLAUDE.md
npx agnix .claude/skills/

# 자동 수정 가능한 항목 적용
npx agnix . --fix
```

전역 설치돼 있으면 `agnix .` 로 바로 실행(이 머신은 `npm i -g agnix` 완료).

## 언제 돌리나

- `CLAUDE.md` 또는 `.claude/skills/*/SKILL.md` 를 수정한 직후
- 새 스킬/MCP 항목 추가 후
- 푸시 전 점검 (release-management 스킬과 함께)

## 결과 해석

- 카테고리별 경고/오류 + 라인 위치 + 권장 수정. `--fix` 로 자동수정 가능한 건 일괄 처리.
- frontmatter `description` 누락/약함은 스킬 자동트리거 실패로 이어지므로 우선 수정한다.

## 참고

- 순수 Node 도구라 Windows 네이티브 동작.
- 다중 어시스턴트(Claude/Cursor 등) 공용이지만 Claude Code 설정을 정면 지원.
