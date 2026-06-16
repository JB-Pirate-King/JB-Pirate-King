---
name: briefing
description: Generate a progress briefing or status report from recent commits, open PRs, and project context. Use when the user asks for a briefing, a status report, a summary of what changed, a standup, a weekly report, or "what happened recently". Reuses the existing commit_context digest and pulls PRs/issues via the github MCP or gh CLI, then writes a concise Korean report.
---

# Briefing / report (commit_context 재활용)

최근 커밋 + 열린 PR + 프로젝트 맥락을 모아 간결한 한국어 보고서를 만든다.
새 도구를 만들지 않고 이미 있는 `commit_context` + git/gh + Obsidian SSOT를 엮는다.

## 1. 입력 수집

```powershell
# (a) 최근 커밋 다이제스트 — 기존 생성기 재활용
python ml/automation/commit_context.py --print
#     → ml/automation/context/commit_log.md

# (b) 열린 PR / 최근 머지 (gh CLI 또는 github MCP)
gh pr list --state open --limit 20
gh pr list --state merged --limit 10

# (c) 현재 작업 / 차기 할 일 (Obsidian SSOT, 있으면)
python ml/automation/bootstrap.py
```

PR 본문·리뷰 맥락이 필요하면 github MCP(`get_pull_request`, `get_pull_request_comments`)로 보강한다.

## 2. 보고서 구성 (한국어, 이모지 없이, 보고체)

다음 섹션으로 합성한다:

1. 기간 요약 — 무엇이 진행됐나 (커밋/PR 기준 3~5줄)
2. 변경 하이라이트 — 주요 커밋/PR을 항목별로 (파일·영향 포함)
3. 진행 중 / 차기 — 열린 PR, Obsidian 차기 할 일
4. 리스크·블로커 — 있으면

## 3. 출력처 선택

- 화면 출력(기본) 또는
- Obsidian 볼트 `운영/Daily Summary/` 노트로 저장 또는
- Discord 요약 전송(기존 웹훅 `C:\scripts\discord_webhook.txt`)

## 참고

- 회의록을 함께 먹이려면 해당 .md 경로를 입력에 추가해 컨텍스트로 사용한다.
- 정기 자동 브리핑이 필요하면 n8n 커맨드센터에 주간 워크플로로 묶는다
  (이 스킬을 호출하는 형태). 상세: `dev-tooling/README.md`.
