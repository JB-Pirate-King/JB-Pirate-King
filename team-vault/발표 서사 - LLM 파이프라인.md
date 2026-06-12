---
tags: [JB-Pirate-King, 발표, 서사, claude-maintained]
updated: 2026-06-12
type: narrative
---

# 발표 핵심 서사 — "LLM이 운영하는 자가개선 AIS 이상탐지 파이프라인"

작성 2026-06-11. ML 모델 개선 작업은 종료했다. 발표는 "모델"이 아니라 "파이프라인 방법론"을 주인공으로 한다.
노션 동일본: CCIT 하위 `발표 방향 — LLM 기반 파이프라인 서사`

## 한 줄 메시지 (발표 후 청중이 기억할 한 문장)

"우리는 모델 하나를 학습시킨 게 아니라, LLM이 개발하고, 분석하고, 판단하고, 피처를 발명하는 자가개선 MLOps 파이프라인을 설계했다."

## 차별점 (심사 어필 포인트)

대부분의 캡스톤/학부 연구는 모델을 만들고 정확도를 보고하는 데서 끝난다. 본 프로젝트의 핵심 성과는 파이프라인 그 자체다. 사람이 피처를 수작업으로 설계하는 대신 LLM을 4개 역할로 파이프라인에 내장해 탐지율을 자동으로 끌어올리는 시스템을 구축했다. 이는 "AI로 AI를 만든다"는 메타적 접근이며, 실무 MLOps 트렌드(LLM-as-judge, agentic workflow)와 맞닿아 있다.

## LLM 4중 활용 (전부 실제 구현되어 동작)

| # | 역할 | 구현 | 기능 |
|---|---|---|---|
| 1 | 개발자 | Claude Code + `CLAUDE.md` 컨텍스트 | ml/ 파이프라인 전체(전처리, 9모델 벤치마크, 평가, FE, 오케스트레이터)를 LLM이 작성 |
| 2 | 분석가 | `claude_analyze` (pipeline_steps.py) | 매 단계 결과를 5섹션으로 분석 — 탐지율 해석, 채택피처 물리해석, 약세 진단, 다음 전략, 판정 |
| 3 | 심판 (LLM-as-judge) | `claude_harness` (orchestrator.py) | 각 노드 뒤 JSON verdict `{assessment, evidence, verdict: continue\|retry\|stop}`로 흐름 게이팅 |
| 4 | 피처 엔지니어 | `n_recommend` / reco 노드 | 약세 시나리오를 LLM이 읽고 새 파생피처를 발명(lambda 코드 생성) → 더미 검증 → 후보풀 확장 |

핵심 원칙: LLM은 제안하고, 채택은 데이터가 결정한다(목적점수 +3.0pp 게이트 통과 피처만). 무비판적 LLM 신뢰가 아니라 LLM 자동화 + 정량 검증의 하이브리드다.

## 파이프라인 아키텍처 (LangGraph StateGraph)

```
                  ┌─────────── 브랜치 체이닝 (그래프 사이클) ───────────┐
                  ↓                                                      │
new_branch → baseline →[reco: LLM 피처발명]→ fe_train →[게이트:사람승인]→ release → chain
                  │하네스        │하네스           │하네스                    │하네스
                  └─ claude_harness verdict (continue/retry/stop) ─────────┘
                                          수렴 시 → converge → END
```

- StateGraph: 파이프라인을 노드+엣지 그래프로 표현. 브랜치 체이닝은 사이클로 구현.
- interrupt() HITL 게이트: 배포·릴리즈 같은 비가역 단계는 사람이 최종 승인한다 (LLM 분석은 의사결정 보조).
- 자동화 백본: Slack 실시간 보고 + Google Sheets 5탭 기록 + git 브랜치/커밋/GitHub 릴리즈 자동화.
- 체크포인트(MemorySaver): 중단 후 재개 가능한 구조.

## 파이프라인이 만든 결과 (수치)

- 베이스 12피처 FP=1% 40.6% → 자동 Greedy FE → 20피처 91.9% (FP=5% 97.6% / FP=10% 98.7%)
- run마다 LLM 분석 → 1피처 자동 채택 → 재학습 → 배포까지 사람 개입 최소화
- 32개 공격 시나리오 평가 자동화 (정상/이상 시퀀스 생성 → 탐지율/오탐율)

## 발표 슬라이드 매핑 (제안)

| 슬라이드 | 메시지 | 재료 |
|---|---|---|
| 도입 | "모델이 아니라 파이프라인이 주인공" 선언 | 위 한 줄 메시지 |
| 아키텍처 | LangGraph 파이프라인 전체도 (위 다이어그램) | graph.md 재현 |
| LLM 4중 활용 (핵심) | 개발/분석/심판/피처발명 표 + 각 실제 출력 캡처 | claude_analyze, harness, reco 실행 로그 |
| 자동화 백본 | Slack 게이트, Sheets, git 릴리즈 자동화 스크린샷 | Slack 채널, Sheets 탭 |
| 결과 | 40.6%→91.9% 그래프 + 32시나리오 표 | Sheets 시나리오결과 |
| 인간-AI 협업 | interrupt 게이트 = LLM 제안, 사람 승인, 데이터 검증 | 게이트 흐름도 |
| 한계·향후 | D1 분포중첩(정직한 한계), SqliteSaver 재개성 강화 | 약세 진단 |

## 정직성 가드 (발표 중 지킬 선)

1. LLM이 발명한 피처도 정량 게이트(+3.0pp) 통과분만 채택 — "LLM이 다 했다"가 아니라 "LLM 제안 → 데이터 검증".
2. 최종 배포는 사람이 승인한다 (interrupt 게이트) — 완전 무인 시스템이 아님.
3. 수치는 freeze 값만 인용한다 (Iter8 20피처 91.9%). 구 83.5%와 최신 84.1%는 평가조건이 달라 사용하지 않는다.
4. 데모는 녹화 백업을 필수로 준비한다 (라이브 실패 대비).

## 관련

[[발표 마스터플랜]] · [[LLM 동작 방식 — 위키·LangGraph·MCP]] · [[CCIT/운영 현황 정리 (2026-06-11) — Claude|운영 현황 정리]] · 코드: develop `ml/orchestrator.py`(LangGraph), `ml/pipeline_steps.py`(claude_analyze)
