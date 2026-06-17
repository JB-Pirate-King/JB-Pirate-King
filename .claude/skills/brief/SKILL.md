---
name: brief
description: Generate a Korean progress briefing for the professor/advisor and publish it to Notion. Folds in ALL PR context + latest meeting notes (회의록) + experiment results + any files you point at, then writes a 진행·성과·계획 briefing (not a code changelog) and posts it as a dated Notion page. Use when the user says "교수님 브리핑", "브리핑", "주간보고", "교수 보고", "advisor briefing", "progress briefing".
---

# brief — 교수님 브리핑 페이지 자동 생성 (한글, Notion 게시)

"PR 전체 맥락 + 최신 회의록 + 파일 → 전부 먹이고 → 교수 보고서 → 딸깍"을 한 스킬로. `change-report`(개발 changelog, 영어, 코드 디테일)와 **다름**: 이건 **한글 진행 브리핑** — 무엇을 했고, 성과 지표는 어떻고, 회의 결정을 어떻게 반영했고, 다음에 뭘 하는지. 코드 함수명/diff는 최소화하고 결과·의미 중심.

## Hard safety rules

- **읽기 전용 (git/GitHub/Sheets/파일).** `git log`/`git diff`/`gh ... view`/파일 읽기만. commit·push·stage·release 안 함.
- **오케스트레이터 run 중엔 실행 금지** (공유 워킹트리). 불확실하면 `ml/logs/` 최신 로그 mtime + python 프로세스부터 확인.
- **team-vault/ 는 읽기전용 Notion 미러** — 읽기만, 쓰기 금지.
- **출력 언어 = 한글.** 교수님 대상. 표·수치는 그대로, 서술은 한글.
- **Notion 쓰기는 메인 컨텍스트에서** (제한 서브에이전트는 MCP 접근 없음).

## Step 1 — 소스 수집 (PR 전체 맥락 + 회의록 + 실험결과 + 파일)

기간 결정: 직전 브리핑(있으면 그 날짜) ~ HEAD, 또는 사용자가 준 범위. 날짜는 `git log -1 --format=%cd --date=short`.

**A. 코드/PR 전체 맥락** (요약용, 디테일 아님):
- `git log --oneline <since>..HEAD` — 커밋 타임라인.
- `gh pr list --state merged --limit 20` → 각 핵심 PR은 `gh pr view <n> --json title,body,mergedAt,comments` 로 **본문+논의까지** (네 말의 "PR 모든 맥락"). gh 미인증이면 로컬 로그만 쓰고 보고서에 명시.

**B. 최신 회의록**:
- 우선순위 ① 사용자가 준 경로(파일/폴더) → ② 폴백 `team-vault/자료/**/*회의록*.md` 최근순.
- 추출: 결정사항, 액션아이템(담당+할일), 마감.
- 없으면 한 줄(`회의록 없음`) 찍고 진행.

**C. 실험결과/지표**:
- 루트 `README.md`의 Run Results 블록(검출률 FP=1/5/10, threshold, 시나리오) 읽기.
- `ml/deploy/{branch}/` per-run report 또는 `ml/.pipeline_tmp/` 최신 결과 JSON 있으면 수치 인용.
- (선택) Google Sheets — 비용/인증 있으면만. 없으면 건너뜀.

**D. 추가 파일**: 사용자가 지목한 파일 경로 있으면 읽어서 맥락 반영.

## Step 2 — 브리핑 합성 (고정 한글 포맷)

아래 구조·순서 그대로. 내용 없는 섹션만 생략(생략 사실은 언급 안 함).

```
# 교수님 브리핑 — <YYYY-MM-DD>

> <한 줄 핵심: 이번 기간 가장 중요한 진전>
> 기간: <시작>~<끝> · 커밋 <N> · 머지 PR <N>

## 이번 기간 진행
<2~4문장 서술. 무엇을 왜 했는지. 리스트 아님.>

## 주요 성과
- <항목>: <수치/결과 — 예: FP=1% 검출률 56.6%→81.8%(+25.3pp)>

## 회의 반영              (회의록 있을 때만)
출처: <회의록 파일 + 날짜>
- ✅ 반영: <결정> → <실제 한 일>
- ⬜ 미반영: <결정 중 아직 안 된 것>

## 다음 계획
- [ ] <다음 기간 할 일 / 마일스톤>

## 리스크 · 이슈           (있을 때만)
- <막힌 점 / 결정 필요 사항>
```

**규칙:**
- 핵심 한 줄 + 기간 줄 필수. 나머지는 내용 게이팅.
- 교수 대상 → 코드 함수명/플래그 남발 금지. 결과와 의미로. 꼭 필요하면 괄호로 한 번만.
- 수치는 구체적으로(검출률·pp·threshold). 과장·군더더기·hedging 금지.
- 과거형 서술, 한글. 한 항목 한 줄. CHANGELOG/백업은 최신이 위.

## Step 3 — Notion 게시 (Notion MCP, 주 경로)

> notify.py(내부 integration 토큰) 경로는 멤버 권한에서 parent 페이지 미공유로 **404 날 수 있음**. 그래서 이 스킬은 **사용자 OAuth Notion MCP**로 게시한다(본인 권한이면 멤버로도 됨).

1. MCP 미인증이면: 사용자에게 `/mcp` → "claude.ai Notion" 인증 요청. 인증돼야 `notion-create-pages` 등이 뜸.
2. 부모 페이지 찾기: `notion-search`로 **"교수님 브리핑"**(또는 "브리핑") 페이지 검색.
   - 있으면 그 페이지 id를 부모로.
   - 없으면(첫 실행): 사용자에게 "어느 페이지 아래 만들까?" 한 번 확인 → 그 밑에 `notion-create-pages`로 컨테이너 페이지 1개("교수님 브리핑") 생성 후 재사용. (정해지면 이후엔 안 물음.)
3. `notion-create-pages`:
   - parent = 위 페이지 id (`{"type":"page_id","page_id":"..."}`)
   - 하위 페이지 1개, properties.title = `교수님 브리핑 — <date>`, icon 예 `🎓`.
   - content = Step 2 본문에서 **H1 제목 줄 제외**한 마크다운(제목은 properties로).
4. 생성된 page url 확인 → Step 4에서 보고.

## Step 4 — 로컬 백업 + 보고

- 로컬 백업: `reports/brief_<date>.md`에 Step 2 전문 저장(폴더 없으면 생성). Notion 실패해도 안 날아가게.
- 사용자에게 한글로: 핵심 한 줄 + 커버한 커밋/PR 수, Notion 페이지 생성됐는지(url) 또는 스킵 사유, 백업 파일 경로.
- 커밋/푸시는 사용자 몫 — 자동 안 함.
