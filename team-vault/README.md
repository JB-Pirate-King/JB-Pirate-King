# team-vault — Notion 자료 미러 (Obsidian 볼트 / LLM 지식베이스)

> ⚠️ **`자료*`/`CCIT*` 경로는 자동 생성되는 읽기 전용 미러입니다.**
> 원본(SSOT)은 **Notion `홈 > 자료`·`CCIT`** 입니다. 해당 파일을 직접 고치지 마세요 — 다음 동기화(매일 09:00/18:00) 때 덮어써집니다.
> 루트의 `README.md`·`SETUP-팀원.md`·`발표 마스터플랜.md`·`발표 서사 - LLM 파이프라인.md`는 수동 관리 파일(claude-maintained)로 동기화 대상이 아닙니다.

## 🧭 읽기 순서 (LLM·신규 합류자)

1. **이 README** — 볼트 전체 맵 파악
2. [발표 마스터플랜.md](발표%20마스터플랜.md) — 🧊 **공식 수치 freeze의 단일 출처** (FE Iter8 · 20피처 · FP=1% 91.9%). **구수치(83.5% 등) 사용 금지.**
3. `자료/📚 프로젝트 핵심 개념 지식베이스 (Knowledge Base)/` 1→5 — 도메인 → 위협 → 취약점 → ML → 아키텍처 + 통합 용어집(35)
4. `자료/LLM 동작 방식 — 위키·LangGraph·MCP.md` — LLM 4중 활용 구조 (위키·LangGraph·MCP)
5. 심화·이력은 `CCIT/` (주차별 메모, 취약점 리포트 원문, 선행 논문)

## 🗂️ 콘텐츠 맵

```
team-vault/
├─ README.md                          ← 이 파일 (수동 관리)
├─ SETUP-팀원.md                       ← 팀원 1회 셋업 가이드 (수동 관리)
├─ 발표 마스터플랜.md                    ← 🧊 공식 수치 freeze + D-day 일정 (수동 관리)
├─ 발표 서사 - LLM 파이프라인.md          ← 발표 핵심 서사 (수동 관리)
│
├─ 자료.md / 자료/                      ← Notion '자료' 루트 = LLM 위키 진입점
│  ├─ 📚 프로젝트 핵심 개념 지식베이스/     ← ★ 합성 KB 5섹션 + 용어집. 가장 먼저.
│  ├─ LLM 동작 방식 — 위키·LangGraph·MCP.md
│  ├─ 📌 핵심 자료/                     ← ⚠️ 포인터 스텁 (아래 매핑표 참고)
│  ├─ OpenCPN PlugIn/ · ML 파이프라인 자동화/ · 양식/   ← Link·Note·Paper 분류함
│  └─ (원본 변환본) 중간발표·WISA 포스터·AIS IDS 논문·SCADA IDS 비교 등
│
└─ CCIT.md / CCIT/                     ← Notion 'CCIT' 루트 = 심화/이력 아카이브
   ├─ 🧠 프로젝트 핵심 개념 정리.md · 📈 ML 성능 현황 요약.md · 통합 생태계 맵.md
   ├─ 운영 현황 정리 (2026-06-11) — Claude.md · 발표 방향·마스터플랜 사본
   ├─ 주차별 메모/ (1~13주차)           ← 회의·진행 이력
   ├─ 취약점 리포트/ (6종)              ← Code/Command Injection·Path Traversal·
   │                                     chartsymbols 변조·WMM 힙 오버플로 원문
   ├─ 이전자료/                         ← 선행 논문·포스터·NMEA 0183 번역(원서 10만자)
   ├─ 자료/                            ← OpenCPN 개발자 메뉴얼·plugin API·git 사용법 등
   └─ 🧪 ML 실험 로그/                  ← 실험 기록 (현재 conv1d ep3 1건 — 최신 수치는
                                          '📈 ML 성능 현황 요약' 참고)
```

## 📌 `자료/📌 핵심 자료/` 스텁 → 실제 내용 위치

`📌 핵심 자료`의 파일들은 Notion 인라인 DB의 **포인터(요약 1줄 + Notion 링크)**라서 본문이 없습니다. 실제 내용은 아래에서 읽으세요:

| 스텁 | 실제 내용 (이 볼트 안) |
|---|---|
| 🧠 프로젝트 핵심 개념 정리 | `CCIT/🧠 프로젝트 핵심 개념 정리.md` |
| 📈 ML 성능 현황 요약 | `CCIT/📈 ML 성능 현황 요약.md` (freeze 수치 1줄 요약은 스텁에도 있음) |
| 운영 현황 정리 (2026-06-11) | `CCIT/운영 현황 정리 (2026-06-11) — Claude.md` |
| 최종발표 마스터플랜 (D-day 역산) | 루트 `발표 마스터플랜.md` (전체판) · `CCIT/최종발표 마스터플랜 (D-day 역산).md` (요약) |
| 발표 방향 — LLM 기반 파이프라인 서사 | 루트 `발표 서사 - LLM 파이프라인.md` (전체판) · `CCIT/발표 방향 — LLM 기반 파이프라인 서사.md` |
| 머신러닝 기반 선박 AIS IDS 설계 및 구현 (논문) | `자료/머신러닝 기반 선박 AIS IDS 설계 및 구현.md` · `CCIT/머신러닝 기반 선박 AIS IDS 설계 및 구현.md` |

## ⚠️ 알려진 중복·노이즈 (Notion 구조에서 유래 — git에서 고치지 말 것)

- `자료/` 루트의 **`(1)` 붙은 파일들** (`OpenCPN plugin API (1)`, `ais_ids_pi 개발 환경 구축 (1)`, `openCPN 개발자 메뉴얼 (1)`, `프로토콜 OpenCPN 에 WHS 글자 띄우기 (1)`)은 Notion 페이지 중복 사본. **정본은 `CCIT/자료/` 및 `자료/OpenCPN PlugIn/Note/`** 쪽입니다. → Notion에서 정리 전까지는 무시.
- 본문 내 `📎 첨부(미변환)` 의 긴 S3 URL은 **1시간이면 만료**되는 임시 링크 — 따라가지 마세요. 첨부 원본은 Notion에서 받아야 합니다. (2026-06-12 이후 동기화분부터는 파일명만 기록됨)

## 동작 (동기화)

- 발행 PC가 매일 **09:00 / 18:00** Notion `자료`·`CCIT` 트리를 Markdown으로 변환해 이 폴더에 commit → `develop` push (`C:\scripts\notion_team_vault_sync.py`).
- PDF/DOCX/PPTX/XLSX 첨부는 자동 `.md` 변환되어 별도 파일로 저장, 이미지는 각 폴더 `_assets/`에 저장.

## 팀원 사용법

코드 repo를 평소처럼 `develop`으로 clone/pull 하면 이 폴더도 같이 업데이트됩니다.
Obsidian에서 **이 `team-vault/` 폴더를 볼트로 열기**만 하면 끝. → [SETUP-팀원.md](SETUP-팀원.md)
