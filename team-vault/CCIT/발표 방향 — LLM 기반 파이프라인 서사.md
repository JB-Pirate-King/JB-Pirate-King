---
notion_url: https://www.notion.so/37cbe0809830816da650da8c6afd87be
last_synced: 2026-06-13 18:02
tags: [notion-sync]
---

# 발표 방향 — LLM 기반 파이프라인 서사

> ML 모델 긎기 종료. **발표는 "모델"이 아니라 "파이프라인 방법론"을 주인공으로.** (옆시디안 동일본: `운영/10 발표 서사 - LLM 파이프라인.md`)

## 한 줄 메시지
> **"우리는 모델 하나를 학습시킨 게 아니라, LLM이 개발하고·분석하고·판단하고·피처를 발명하는 자가개선 MLOps 파이프라인을 설계했다."**

## 왜 이게 차별점인가
대부분의 학부 연구는 **"모델 만들고 정확도 보고"**에서 끝난다. 우리의 진짜 성과는 **파이프라인 그 자체** — 사람이 일일이 피처를 긎는 대신 **LLM을 4개 역할로 파이프라인에 내장**해 탐지율을 자동으로 끌어올렸다. "AI로 AI를 만든다"는 메타적 접근이고, 실무 MLOps 트렌드(LLM-as-judge, agentic workflow)와 정확히 맞닿는다.

## LLM 4중 활용 (전부 실제 구현·동작)
| # | 역할 | 구현 | 무엇을 하나 |
| 1 | **개발자** | Claude Code + `CLAUDE.md` | ml/ 파이프라인 전체(전처리·9모델 벤치마크·평가·FE·오케스트레이터)를 LLM이 작성 |
| 2 | **분석가** | `claude_analyze` | 매 단계 결과를 5섹션 상세 분석 — 탐지율 해석·채택피처 물리해석·약세 진단·다음 전략·판정 |
| 3 | **심판 (LLM-as-judge)** | `claude_harness` | 각 노드 뒤 JSON verdict `{assessment, evidence, verdict: continue/retry/stop}`로 흐름 게이팅 |
| 4 | **피처 엔지니어** | `n_recommend` (reco) | 약세 시나리오를 LLM이 읽고 **새 파생피처를 발명**(lambda 생성)→검증→후보풀 확장 |
> 핵심: LLM은 **제안**하고, **채택은 데이터가 결정**한다(목적점수 +3.0pp 게이트 통과분만). → 무비판적 LLM 신뢰가 아니라 **LLM 자동화 + 정량 검증 하이브리드**.

## 아키텍처 (LangGraph StateGraph)

```javascript
            ┌─── 브랜치 체이닝 (그래프 사이클) ───┐
            ↓                                          │
new_branch → baseline →[reco: LLM 피처발명]→ fe_train →[게이트: 사람승인]→ release → chain
            │하네스        │하네스              │하네스                    │
            └─ claude_harness verdict (continue/retry/stop) ─┘
                              수렴 시 → converge → END
```

- **StateGraph**: 파이프라인을 노드+엣지 그래프로. 브랜치 체이닝 = 사이클.
- **interrupt() HITL 게이트**: 배포·릴리즈 같은 비가역 단계는 **사람이 최종 승인** (LLM 분석이 의사결정 보조).
- **자동화 백밸**: Slack 실시간 보고 + Google Sheets 5탭 기록 + git 브랜치/커밋/GitHub 릴리즈 자동.

## 파이프라인이 만든 결과
- **베이스 12피처 FP=1% 40.6% → 자동 Greedy FE → 20피처 91.9%** (FP=5% 97.6% / FP=10% 98.7%)
- run마다 LLM 분석 → 1피처 자동 채택 → 재학습 → 배포까지 사람 개입 최소화
- 32개 공격 시나리오 평가 자동화

## ⚖정직성 가드 (발표 중 지킬 선)
1. LLM이 발명한 피처도 **정량 게이트(+3.0pp) 통과분만 채택** — "LLM이 다 했다"가 아니라 "LLM 제안 → 데이터 검증".
1. **최종 배포는 사람이 승인** (interrupt 게이트).
1. **수치는 freeze 값만 인용** (Iter8 20피처 91.9%).
1. 데모는 **녹화 백업 필수**.

---

*코드: develop **`ml/orchestrator.py`**(LangGraph)·**`ml/pipeline_steps.py`**(claude_analyze)*
