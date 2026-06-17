---
name: token-usage
description: Check Claude Code token usage and find where tokens are being spent (leakage) with ccusage. Use when the user asks about token usage, cost, how many tokens were used, what is burning tokens, why usage is high, how close to the rate limit, or to audit/report Claude usage by day, model, session, or 5-hour billing block.
---

# 토큰 사용량 / 누수 점검 (ccusage)

Claude Code의 로컬 사용 로그(`~/.claude/projects/**/*.jsonl`)를 분석해 토큰 사용량과
누수 지점을 파악한다. 설치 불필요(`npx`), 무료·OSS.

## 실행

```powershell
# 일자별 사용량(모델별 분해)
npx -y ccusage@latest daily

# 현재 5시간 과금 창(레이트리밋 잔량·소진율) — "토큰 얼마 남았나" 확인용
npx -y ccusage@latest blocks --active

# 세션별(어느 세션이 토큰 과다 = 누수 지점)
npx -y ccusage@latest session --breakdown

# 월별
npx -y ccusage@latest monthly
```

## 해석

- 컬럼: Input / Output / **Cache Create** / **Cache Read** / Total / Cost.
- **Cache Read는 매우 저렴**(컨텍스트 재사용). Total이 커도 대부분 Cache Read면 비용은 작다.
- **Output 토큰이 가장 비싸다.** Output·Cache Create가 큰 항목이 실제 비용 동인.
- Cost는 API 환산 추정치다(구독 사용자는 실제 청구액이 아니라 "API였다면" 값).

## 누수(leak) 진단 가이드

- **멀티에이전트 Workflow / 대규모 서브에이전트 fan-out이 최대 소비처다.** deep-research·대형 리뷰
  한 번이 수백만 출력토큰을 쓴다. 꼭 필요할 때만 쓰고, 일반 작업은 단일 세션이 효율적.
- 매 세션·매 `claude -p` 마다 로드되는 거대 CLAUDE.md도 누적 비용 → 슬림 유지(현재 ~360줄).
- 같은 큰 파일을 반복 Read하지 말고 repomix-pack(시그니처 맵)·llms.txt(진입점)로 좁혀 읽기.
- 세션 내 즉석 확인은 Claude Code `/cost`.

## 참고

- 데이터 원천: `~/.claude/projects` (세션 JSONL). 비공개 로컬 데이터.
- 정기 리포트가 필요하면 n8n 커맨드센터에 주간 워크플로로 묶을 수 있다(dev-tooling).
