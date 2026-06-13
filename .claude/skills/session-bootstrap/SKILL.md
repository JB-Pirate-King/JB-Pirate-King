---
name: session-bootstrap
description: Load current JB-Pirate-King project status at the start of a work session, before doing any task in this repo. Use when a new session starts, or when the user asks where we are, what's the status, what's next, or what changed recently. Runs ml/automation/bootstrap.py (Obsidian SSOT current and next tasks, running/finished/stopped runs, deploy-pending items) and refreshes the git commit-context digest.
---

# Session bootstrap

새 세션 시작 시 프로젝트 현황을 즉시 파악한다. 사용자 요청 작업 전에 이 단계를 먼저 수행한다.

## 1. 현황 요약 실행

```powershell
$env:PYTHONUTF8="1"
python ml/automation/bootstrap.py
```

출력 내용:
- Obsidian SSOT: 현재 작업 + 차기 할 일
- run 상태: 실행중 / 완료 / 정지(재개 필요)
- 최근 커밋 맥락 (commit_context 자동 갱신)
- 배포 대기 항목 (distribute_manifest 감지 시)

## 2. 배포 대기 항목이 있으면 즉시 처리

```powershell
python ml/automation/bootstrap.py --distribute
```

출력된 지시에 따라 Sheets / Drive / Notion MCP 작업을 수행하고,
완료 후 매니페스트의 `pending` 필드를 비운다.

## 3. 커밋 맥락만 따로 갱신 (선택)

```powershell
python ml/automation/commit_context.py --print
```

`ml/automation/context/commit_log.md`에 최근 커밋 다이제스트를 생성한다
(bootstrap가 자동 호출하므로 보통 별도 실행 불필요).

## 자주 참조

- run 상태 파일: `D:\ais_output\ens24_v2\state.json`
- 정식 문서·코드 진입점 색인: 레포 루트 `llms.txt`
- 프로젝트 규칙: `CLAUDE.md`

> 참고: 일부 경로(D 드라이브, Obsidian 볼트)는 머신에 따라 없을 수 있으며,
> bootstrap.py는 없는 항목을 건너뛰고 계속 진행한다.
