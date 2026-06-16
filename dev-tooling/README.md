# dev-tooling — Claude 중심 개발/자동화 도구 세팅

이 디렉토리는 JB-Pirate-King 팀의 Claude Code 중심 개발 워크플로를 강화하는 도구 세팅을
버전관리한다. 무료·오픈소스만 사용한다. 2026-06-17 도입.

도구 선정은 deep-research(다중 소스 + 적대적 검증)로 조사 후, 이미 쓰던 것
(Repomix, pyright-lsp, llms.txt, 자체 스킬 7개, commit_context, n8n 커맨드센터,
IDA/obsidian/sheets/github/context7 MCP, LM Studio)을 제외하고 갭을 메우는 것만 채택했다.

## 채택한 도구

### 1. Semgrep MCP — 보안 정적분석(SAST)
- 무엇: 공식 MIT 라이선스 MCP 서버. SAST 5,000+ 룰로 코드 취약점 탐지.
- 왜: pyright는 타입만 본다. injection / 안전하지 않은 eval / 하드코딩 비밀키 등
  보안 취약점은 못 잡는다. Semgrep이 그 갭을 메운다(AIS 이상탐지·리버싱 팀에 직결).
- 설치: `uvx semgrep-mcp` (uv 필요). Windows 네이티브 동작 확인(semgrep 1.166+, WSL 불필요).
- Claude 연동: `.mcp.json` 의 `semgrep` 항목(템플릿은 루트 `.mcp.json.example`).
- 온디맨드 사용: 스킬 `security-scan` (`.claude/skills/security-scan`).
- 자동화: n8n 워크플로 `JB - Semgrep Weekly` (주 1회 레포 스캔 → Discord 요약).

### 2. agnix — AI 설정 파일 린터
- 무엇: CLAUDE.md / SKILL.md / AGENTS.md / hooks / .mcp.json 전용 정적 린터(422룰, MIT/Apache-2.0).
- 왜: 큰 CLAUDE.md + 스킬 다수를 유지하는 팀. frontmatter 오류·MCP 오타·CLAUDE.md 과대 등을
  조용히 깨지기 전에 잡는다.
- 설치: `npm i -g agnix` (순수 Node, Windows 네이티브).
- 사용: 스킬 `config-lint` (`.claude/skills/config-lint`). `npx agnix .` / `--fix`.

### 3. 브리핑/보고서 — 기존 자산 재활용
- 새 도구 없이 `commit_context` + git/gh + Obsidian SSOT를 엮어 진행 보고서를 생성.
- 사용: 스킬 `briefing` (`.claude/skills/briefing`).

## n8n 커맨드센터 연계

백그라운드 자동화는 n8n(`http://localhost:5678`)이 단일 GUI로 관리한다(이 머신).
운영 상세 문서는 `C:\scripts\README-commandcenter.md`(로컬, 머신 고유).
참조용 워크플로 정의 사본은 `dev-tooling/n8n/` 에 둔다.

| n8n 워크플로 | 스케줄(KST) | 동작 |
|---|---|---|
| JB - Notion Vault Sync | 09:00, 18:00 | Notion 자료 → git team-vault 미러 |
| JB - Daily PM Snapshot | 18:00 | Sheets → Obsidian Daily Summary + Discord |
| JB - Ops Nightly | 21:30 | 야간 운영 점검 → Discord |
| JB - Semgrep Weekly | 월 09:00 | 레포 보안 스캔 → Discord 요약 (신규) |

> n8n은 pm2가 데몬으로 상시 구동(콘솔 비의존). 켜고/끄기·즉시실행·이력은 GUI에서.

## 협업 연동 (Slack/Discord) — 준비 완료, 활성화 대기

Claude를 팀원처럼 채팅에서 구동하는 연동은 토큰만 넣으면 되도록 준비했다.
가이드: `dev-tooling/slack-discord-setup.md`. (korotovsky slack-mcp / OpenACP, 둘 다 MIT.)

## 보류한 도구 (이유)

| 도구 | 보류 이유 |
|---|---|
| Claude Squad | tmux 의존 → Windows에서 WSL2 필요 |
| RuFlo (ex-Claude-Flow) | 100+ 에이전트 전제, 2인엔 과대 |
| Auto-Claude/Aperant | Electron 앱, 유지보수 모드(3.0 대기) |
| Vibe Kanban | 서비스 종료 중(코드만 잔존) |
| SonarQube MCP | Docker + 인스턴스 필요, OSS 경로 조건부 → Semgrep이 우위 |

병렬·멀티에이전트는 이미 쓰는 git worktree + Claude Code의 Workflow/서브에이전트로 충분.

## 디렉토리

```
dev-tooling/
├── README.md                  이 문서
├── slack-discord-setup.md     협업 연동 준비 가이드(토큰만 입력하면 활성화)
└── n8n/                       n8n 워크플로 정의 참조 사본(머신 고유 경로 포함)
```

관련 스킬: `.claude/skills/{security-scan,config-lint,briefing}`.
MCP 템플릿: 루트 `.mcp.json.example` (실제 `.mcp.json` 은 비밀키 때문에 gitignore).
