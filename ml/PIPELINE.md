# ML 파이프라인 — LangGraph 구조 문서

`ml/orchestrator.py` 의 LangGraph `StateGraph` 를 노드 단위로 설명한다.
제어흐름은 이 그래프, 무거운 실행 함수는 `ml/pipeline_steps.py` 에 있다.

---

## 1. 큰 그림

- **1 run = 1 git 브랜치 = 피처 1개 채택 시도** (`dcdetect_001`, `_002`, …).
- 채택에 성공하면 **체인**(그래프 사이클)으로 다음 브랜치를 시작하고, 더 이상
  목적점수 이득이 없으면 **수렴**하여 종료한다.
- 각 compute 노드 뒤에는 **판정(judge)** 노드가 붙어 claude 가 `continue / retry / stop`
  으로 라우팅한다 (LLM-as-judge).
- 비가역 단계(배포·커밋·수렴)에는 **게이트**(`interrupt()` 또는 `--auto_approve`)가 있다.
- claude 호출(추천·판정·분석)은 **브랜치당 세션 1개**를 공유. 모델은 역할별 분리 —
  판정/지식요약 `--claude_model`(기본 sonnet), 피처발명/상세분석 `--claude_model_heavy`(기본 opus).

```
새 브랜치 ─▶ 베이스 진단 ─▶ 피처 발명 ─▶ FE 학습/채택 ─▶ 배포 게이트
   ▲                                                          │
   └──────────────── 체인(사이클) ◀── 릴리즈 ◀── 빌드 ◀───────┘
                          채택 없음 ─▶ 수렴 ─▶ 종료
```

---

## 1.5 파이썬 파일 구성

파이프라인을 이루는 `ml/` 하위 파일별 역할.

### 제어흐름 & 실행 (루트)
| 파일 | 역할 |
|---|---|
| `orchestrator.py` | **LangGraph `StateGraph`** — 노드/엣지/게이트/판정 제어흐름. 진입점 `python -m ml.orchestrator` |
| `pipeline_steps.py` | 무거운 **실행함수 라이브러리** — `run_cmd`, 출력 파서, `stage_*`, `_fe_train_eval`/`_fe_build_and_release`/`_fe_commit_release`, `claude_analyze`, fe_state io |
| `dynamic_candidates.py` | **런타임 생성** — `recommend` 노드가 claude 발명 피처를 여기 기록, `feature_engineer` 가 exec 로드 (gitignored) |
| `build_plugin_wsl.sh` | WSL tar.gz 빌드 (`--build_plugin`, 기본 off) |

### `core/` — ML 엔진
| 파일 | 역할 |
|---|---|
| `constants.py` | `BASE_FEATURES`(12)/`SEQ_LEN`(10) **단일 출처** |
| `preprocess.py` | raw AIS(.csv/.zst/.zip) → 파생피처 CSV |
| `train_benchmark.py` | 비지도 9모델 정의·학습 (dcdetect★/usad/tranad/…) |
| `eval_anomaly.py` | 32개 공격 시나리오 탐지율/오탐 평가 |
| `feature_engineer.py` | **Greedy FE** + ONNX export. orchestrator 가 subprocess 로 호출 |
| `patch_plugin.py` | scaler features → C++ 코드젠 (`[AUTO:*]` 마커) |
| `pipeline.py` | 단순 train+eval 경로 (실험용; `automation/ens24` 가 import) |

### `integrations/` — 외부 연동
| 파일 | 역할 |
|---|---|
| `slack_bot.py` | Slack 로그 전송 + 버튼 승인 대기 (Block Kit) |
| `sheets.py` | Google Sheets 5탭 자동 로깅 |
| `notify.py` | Discord webhook + Notion 리포트 |
| `git_manager.py` | 브랜치 생성(`get_next_run_num`/`create_branch`)·커밋·푸시 |

### `scripts/` — 독립 CLI (import 안 됨, 직접 실행)
| 파일 | 역할 |
|---|---|
| `auto_feat_eng.py` | FE 자동화 루프 (데이터셋 빌드 → FE) |
| `build_3yr_dataset.py` | 2023–2025 균형 데이터셋 빌더 |
| `download_ais.py` | AIS 원본 다운로더 |
| `reset_sheets.py` | 시트 탭 데이터 초기화 (`python -m ml.scripts.reset_sheets`) |

### `config/` — 설정·상태 (시크릿 gitignored)
`pipeline_config.json`(Slack/Sheets, 시크릿) · `pipeline_config.example.json`(템플릿) ·
`google_credentials.json`(GCP, 시크릿) · `notify_config.json`(Discord/Notion, 시크릿) ·
`fe_state.json`(채택 피처 누적, 추적)

### `automation/` — 보조
| 파일 | 역할 |
|---|---|
| `bootstrap.py` | 세션 시작 현황 부트스트랩 |
| `ens24.py` | ens24 앙상블 자동화 |

---

## 2. 그래프 구조도

### 자동 렌더 (LangGraph 실제 구조)

`build_graph().get_graph().draw_mermaid_png()` 로 컴파일된 그래프에서 직접 추출.

![LangGraph 파이프라인 구조](pipeline_langgraph.png)

| 색 | 그룹 | 노드 | 역할 |
|---|---|---|---|
| 🟢 초록 | compute | new_branch · preprocess · fe_baseline · reco_again · fe_train · build · release · chain · converge | 실제 일하는 노드 — 브랜치/전처리/진단/FE/빌드/릴리즈/체인 |
| 🩷 분홍 | reco | recommend | claude 피처 발명 (opus) — 약세 겨냥 새 lambda |
| 🟣 보라 | judge | j_branch ~ j_chain (7) | claude 판정 (sonnet) — continue/retry/stop 라우팅 |
| 🟡 노랑 | gate | gate_deploy · gate_release · gate_converge | 사람 승인 (`interrupt()`/auto_approve) — 비가역 관문 |
| 🔵 파랑 | log | log_run_start · log_fe · log_run_done · log_converge · readme | 기록 — Sheets + 루트 README 결과표 |
| 🔴 빨강 | stop | user_stop | 중단 종착 — stop verdict/게이트 거부 수렴점 |

> 읽는 법: **초록이 일하고 → 보라가 심사하고 → 노랑이 사람 허락 받고 → 파랑이 적는다.**
> 분홍이 아이디어를 내고, 틀어지면 빨강으로. 매핑은 `ml/scripts/render_graph.py` 의 GROUPS/STYLES.

> 재생성 (노드 성격별 색상 — 초록 compute · 분홍 recommend · 보라 judge · 노랑 gate · 파랑 log · 빨강 stop):
> ```bash
> python -m ml.scripts.render_graph    # repo 루트에서 — ml/pipeline_langgraph.png 갱신
> ```
> 노드 분류/색상은 `ml/scripts/render_graph.py` 의 GROUPS/STYLES — 새 노드 추가 시 미분류 경고가 뜬다.

### 주석 달린 Mermaid (라우팅 레이블 포함)

> **선 종류**: `──▶` 실선 = 직접 엣지(`add_edge`) · `┄┄▶` 점선 = 조건부 엣지(`add_conditional_edges`, 라우팅 함수가 분기). LangGraph 원본과 동일 규칙.

```mermaid
flowchart TD
    START([START]) --> new_branch

    subgraph BRANCH_INIT[브랜치 시작]
        new_branch[new_branch<br/>브랜치 생성·세션발급] --> log_run_start[log_run_start<br/>Sheets]
        log_run_start --> j_branch{{j_branch 판정}}
    end

    j_branch -.->|preprocess| preprocess[preprocess<br/>raw→csv·첫브랜치만]
    j_branch -.->|fe_baseline| fe_baseline
    j_branch -.->|stop| user_stop
    j_branch -.->|max_runs 초과| END1([END])
    preprocess -.->|continue| fe_baseline
    preprocess -.->|terminate| END1

    subgraph DIAGNOSE_RECO[진단 · 추천]
        fe_baseline[fe_baseline<br/>베이스 탐지율·약세진단] --> j_base{{j_base 판정}}
        j_base -.->|continue| recommend[recommend<br/>claude 피처 N개 발명]
        recommend --> j_reco{{j_reco 판정}}
        reco_again[reco_again<br/>라운드+1] --> recommend
    end
    j_base -.->|retry| fe_baseline
    j_base -.->|stop| user_stop
    j_reco -.->|continue| fe_train
    j_reco -.->|retry| recommend
    j_reco -.->|stop| user_stop

    subgraph FE[피처 엔지니어링]
        fe_train[fe_train<br/>스캔→채택→재학습→중요도→최종평가→export] --> log_fe[log_fe<br/>Sheets]
        log_fe --> j_fe{{j_fe 판정}}
    end

    j_fe -.->|채택O| gate_deploy
    j_fe -.->|채택X·라운드남음| reco_again
    j_fe -.->|채택X·라운드끝| gate_converge
    j_fe -.->|실패/retry| fe_baseline
    j_fe -.->|stop| user_stop

    subgraph DEPLOY[배포 · 릴리즈]
        gate_deploy[/gate_deploy<br/>배포 승인/] -.->|approve| build[build<br/>C++ 패치·모델 복사]
        build --> j_build{{j_build 판정}}
        j_build -.->|continue| gate_release[/gate_release<br/>커밋·릴리즈 승인/]
        gate_release -.->|approve| release[release<br/>git commit·gh release]
        release --> j_release{{j_release 판정}}
        j_release -.->|continue| log_run_done[log_run_done<br/>Sheets]
    end
    gate_deploy -.->|retry| fe_baseline
    gate_deploy -.->|stop| user_stop
    j_build -.->|retry| build
    j_build -.->|stop| user_stop
    gate_release -.->|stop| user_stop
    j_release -.->|retry| release
    j_release -.->|stop| user_stop

    log_run_done --> readme[readme<br/>루트 README 결과표 갱신]
    readme --> chain[chain<br/>fe_state 저장·커밋]
    chain --> j_chain{{j_chain 판정}}
    j_chain -.->|continue·상한미달 사이클| new_branch
    j_chain -.->|상한 도달| END4([END])
    j_chain -.->|retry| chain
    j_chain -.->|stop| user_stop

    gate_converge[/gate_converge<br/>수렴 종료 승인/] -.->|approve| converge[converge]
    gate_converge -.->|stop| user_stop
    converge --> log_converge[log_converge<br/>Sheets] --> END2([END])
    user_stop[user_stop<br/>중단] --> END3([END])
```

> `{{ }}` = 판정(judge) · `[/ /]` = 게이트(승인) · `[ ]` = compute/로그 노드.

### LangGraph 원본 (배선 그대로, 노드명만)

`build_graph().get_graph().draw_mermaid()` 출력 — 우리가 `add_node`/`add_edge` 로 배선한
그래프 그 자체. `-->` = 직접 엣지, `-.->` = 조건부(라우팅 함수) 엣지.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([__start__]):::first
	new_branch(new_branch)
	preprocess(preprocess)
	fe_baseline(fe_baseline)
	recommend(recommend)
	reco_again(reco_again)
	fe_train(fe_train)
	build(build)
	release(release)
	readme(readme)
	chain(chain)
	converge(converge)
	user_stop(user_stop)
	gate_deploy(gate_deploy)
	gate_release(gate_release)
	gate_converge(gate_converge)
	log_run_start(log_run_start)
	log_fe(log_fe)
	log_run_done(log_run_done)
	log_converge(log_converge)
	j_branch(j_branch)
	j_base(j_base)
	j_reco(j_reco)
	j_fe(j_fe)
	j_build(j_build)
	j_release(j_release)
	j_chain(j_chain)
	__end__([__end__]):::last
	__start__ --> new_branch;
	build --> j_build;
	chain --> j_chain;
	converge --> log_converge;
	fe_baseline --> j_base;
	fe_train --> log_fe;
	gate_converge -.-> converge;
	gate_converge -.-> user_stop;
	gate_deploy -.-> build;
	gate_deploy -.-> fe_baseline;
	gate_deploy -.-> user_stop;
	gate_release -.-> release;
	gate_release -.-> user_stop;
	j_base -.-> fe_baseline;
	j_base -.-> recommend;
	j_base -.-> user_stop;
	j_branch -.  END  .-> __end__;
	j_branch -.-> fe_baseline;
	j_branch -.-> preprocess;
	j_branch -.-> user_stop;
	j_build -.-> build;
	j_build -.-> gate_release;
	j_build -.-> user_stop;
	j_chain -.  END  .-> __end__;
	j_chain -.-> chain;
	j_chain -.-> new_branch;
	j_chain -.-> user_stop;
	j_fe -.-> fe_baseline;
	j_fe -.-> gate_converge;
	j_fe -.-> gate_deploy;
	j_fe -.-> reco_again;
	j_fe -.-> user_stop;
	j_reco -.-> fe_train;
	j_reco -.-> recommend;
	j_reco -.-> user_stop;
	j_release -.-> log_run_done;
	j_release -.-> release;
	j_release -.-> user_stop;
	log_fe --> j_fe;
	log_run_done --> readme;
	log_run_start --> j_branch;
	new_branch --> log_run_start;
	preprocess -.  END  .-> __end__;
	preprocess -.-> fe_baseline;
	readme --> chain;
	reco_again --> recommend;
	recommend --> j_reco;
	release --> j_release;
	log_converge --> __end__;
	user_stop --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

> 재생성: `python -c "from ml.orchestrator import build_graph; print(build_graph().get_graph().draw_mermaid())"`

---

## 3. 노드 카탈로그

> 라인 번호는 작성 시점(`orchestrator.py` / `pipeline_steps.py`) 기준 — 코드 수정 시 어긋날 수 있다.
> 빠르게 찾으려면 함수명(`n_*` / `stage_*` / `_fe_*`)으로 grep.

### 코어 compute 노드

| 노드 | 함수 (코드 위치) | 하는 일 | 주요 state 출력 |
|---|---|---|---|
| `new_branch` | `n_new_branch` — orchestrator.py:236 | 다음 run 번호 계산(`get_next_run_num`) → 브랜치 생성(직전 브랜치 위에, 없으면 develop) → **claude 세션 uuid 발급** → 시작 로그 | `run_num`, `branch`, `iters` |
| `preprocess` | `n_preprocess` — orchestrator.py:252<br/>→ `stage_preprocess` pipeline_steps.py:301 | raw AIS → 파생피처 CSV (`core/preprocess.py`). **첫 브랜치만**, `--skip_preprocess` 면 생략 | `first_iter`, (`terminate`) |
| `fe_baseline` | `n_fe_baseline` — orchestrator.py:261 | `feature_engineer --diagnose_only` → 현 피처셋 베이스 탐지율 + **약세 시나리오** 도출 | `baseline{det,weak,out}` |
| `recommend` | `n_recommend` — orchestrator.py:301<br/>(`_reco_prompt`:286, `_validate_recos`:318, `_write_dynamic_candidates`:339) | 약세 진단을 claude 에 전달 → **새 lambda 피처 N개 발명**(`--invent`) → 더미시퀀스 exec 검증·dedup → `ml/dynamic_candidates.py` 기록 | `candidates`, `tried_feats` |
| `reco_again` | `n_reco_again` — orchestrator.py:449 | 수렴(채택0) 시 추천 라운드 +1 → 다른 각도로 재추천 진입 | `reco_round` |
| `fe_train` | `n_fe_train` — orchestrator.py:350<br/>→ `_fe_train_eval` pipeline_steps.py:557 | **핵심**: 후보 스캔 → 목적점수 ≥`min_gain` 최선 1개 채택 → 재학습(model_best) → 순열중요도 → 최종 FP1/5/10 → 임계값 → 배포 export. `feature_engineer` 1 subprocess | `r{newly_adopted,full_extra,det_rate,summary,fe_stats,…}` |
| `build` | `n_build` — orchestrator.py:360<br/>→ `_fe_build_and_release` pipeline_steps.py:884 (→ `stage_build_plugin`:344) | C++ 플러그인 패치(`patch_plugin`) + 모델 파일 복사 → `ais_ids_pi/data/` | `commit_files`, `build_summary` |
| `release` | `n_release` — orchestrator.py:366<br/>→ `_fe_commit_release` pipeline_steps.py:901 (→ `stage_release`:464) | 채택 커밋 + GitHub 릴리즈(`gh release`, prerelease `run/dcdetect_NNN`) | — |
| `readme` | `n_readme` | **claude 노드** — 루트 `README.md` Run Results 표에 이번 run 행 추가 (수치는 FE JSON, Note 는 브랜치 세션 claude 한 줄) → run 브랜치에 커밋 | — |
| `chain` | `n_chain` | `fe_state.json` 저장 + **채택 lambda 를 `adopted_features.py` 에 영속화** → 함께 커밋 → 다음 브랜치로 사이클 | `current_extra`, `adopted_any` |
| `converge` | `n_converge` — orchestrator.py:383 | 수렴 완료 로그 | `terminate` |
| `user_stop` | `n_user_stop` — orchestrator.py:389 | 중단 — Sheets 실패기록 + 종료 | `terminate` |

### 게이트 노드 (비가역 단계 승인)

| 노드 | 함수 (코드 위치) | 질문 | 분기 |
|---|---|---|---|
| `gate_deploy` | `n_gate_deploy` orchestrator.py:401 | "채택 → 배포 진행?" | approve→build / retry→fe_baseline / stop→user_stop |
| `gate_release` | `n_gate_release` orchestrator.py:408 | "커밋 + GitHub 릴리즈 진행?" | approve→release / stop→user_stop |
| `gate_converge` | `n_gate_converge` orchestrator.py:413 | "수렴 → 종료?" | approve→converge / stop→user_stop |

게이트 공통 헬퍼 `_gate` — orchestrator.py:215.
`--auto_approve` 면 자동 통과(요약에 `❌` 있으면 stop). 아니면 Slack 버튼(`interrupt()`) 대기.
게이트는 노드 경계라 승인 대기 중 크래시해도 **재학습 없이 재개** 가능.

### 로그 노드 (Google Sheets)

`log_run_start` / `log_fe` / `log_run_done` / `log_converge` — `log_sheet(kind)` 팩토리(orchestrator.py:185) 1개로 생성.

### 판정 노드 (claude 판정)

`j_branch · j_base · j_reco · j_fe · j_build · j_release · j_chain` — 각 compute 노드 뒤에 붙음.
`claude_judge(stage, ctx_fn)` 팩토리(orchestrator.py:150)로 생성, `build_graph`(orchestrator.py:469) 안에서 `add_node`.
claude 호출은 `_branch_claude`(orchestrator.py:123) → `_claude_json`(orchestrator.py:92).
`JUDGE_ON` 에 든 stage 만 동작(`--no_judge` 로 전체 off).

동작: 해당 노드 결과를 claude 에 주고 `{assessment, verdict, reason, suggestion}` JSON 받음 →
`_route_judge`: **stop→user_stop / retry→직전 노드 재실행 / 그외→다음 노드**.

프롬프트(`_judge_prompt`)는 공통 템플릿에 **stage별 판정 포인트**(`STAGE_FOCUS`)를 주입한다 —
baseline은 "FE 출발점으로 타당한가", build는 "패치 마커·모델 파일·피처수 일치하나" 처럼
단계 고유 기준으로 평가(일률 판정 방지). claude 응답이 ```json 펜스로 와도 `_strip_code_fence`
로 벗겨 파싱한다.

> `j_fe` 만 예외 — `route_after_fe` 가 판정 verdict + **채택여부**를 결합해 분기
> (채택O→gate_deploy / 채택X→reco_again 또는 gate_converge / 실패→fe_baseline).

---

## 4. 라우팅 함수

| 함수 (코드 위치) | 위치 | 분기 로직 |
|---|---|---|
| `route_after_branch_j` orchestrator.py:542 (→`route_after_branch`:418) | j_branch 뒤 | stop이면 user_stop, 아니면 max_runs 체크 → preprocess/fe_baseline/END |
| `route_after_preprocess` orchestrator.py:425 | preprocess 뒤 | terminate면 END, 아니면 fe_baseline |
| `_route_judge(next, retry_to)` | 대부분 판정 | stop→user_stop / **retry→직전 노드 재실행** / else→next |
| `route_after_fe` orchestrator.py:429 | j_fe 뒤 | verdict + 채택여부 결합 (위 설명) |
| `route_gate_deploy` / `route_gate_release` / `route_gate_converge` | 각 게이트 뒤 | approve→진행 / 그외→user_stop(deploy는 retry→fe_baseline) |
| `route_after_chain` | j_chain 뒤 | stop/retry 우선 → `iters >= max_runs` 면 **빈 브랜치 안 만들고 END** → 아니면 new_branch (사이클) |
| `build_graph` | — | 전체 노드·엣지 배선 (그래프 정의) |

---

## 5. State (`PipelineState`)

`TypedDict, total=False` — 노드 간 전달되는 공유 상태.

| 필드 | 의미 |
|---|---|
| `run_num`, `branch`, `iters` | 현재 run 번호·브랜치명·누적 반복수 |
| `current_extra` | 현재까지 채택된 추가 피처 (이번 브랜치 시작 피처셋) |
| `first_iter` | 첫 브랜치 여부 (preprocess 1회 판단) |
| `baseline` | `{det, weak, out}` — 베이스 탐지율·약세시나리오·진단출력 |
| `candidates`, `tried_feats`, `reco_round` | 발명된 후보·시도이력·추천 라운드 |
| `r` | `_fe_train_eval` 결과 (채택·탐지율·요약·통계·full_extra) |
| `commit_files`, `build_summary` | 빌드 산출·요약 |
| `decision`, `judge` | 판정/게이트 결정·노드별 판정 결과 |
| `adopted_any`, `terminate` | 채택 발생 여부·종료 플래그 |

---

## 6. 사이클 & 종료

- **사이클**: `release → log_run_done → chain → j_chain → new_branch` (다음 브랜치 시작).
  `recursion_limit = max(80, max_runs × 25)`, `--max_runs`(기본 50)로 무한루프 방지.
- **수렴 종료 (횟수 기준)**: 어떤 후보도 목적점수 +`min_gain`(기본 3.0) 못 넘으면 `reco_again`
  으로 다른 각도 재추천 — **브랜치당 `--invent_rounds`(기본 3) 라운드 소진 후에야**
  `gate_converge → converge → END`. 단발 미채택 = 즉시 종료가 아님.
- **베이스라인 캐시**: `fe_baseline`(diagnose) 결과 JSON 을 `fe_train` 이 `--baseline_cache` 로
  재사용 — 같은 피처셋이면 베이스라인 재학습 생략 (브랜치당 1회 학습). 재추천 라운드 비용은
  후보 N개 학습만.
- **세션 격리**: 브랜치마다 새 claude 세션(uuid). 브랜치 내 노드는 맥락 공유, 브랜치 간엔 격리.
- **thread_id**: run 마다 `orchestrator-{시각}` 발급 (stdout/로그에 출력) — LangSmith Threads 뷰에서
  run 별로 분리되고, SqliteSaver 도입 시 크래시 재개 키로 쓴다. (고정값이면 전 run 이 한 스레드로 합쳐짐)
- **develop 복구**: `main()` 의 `finally` 에서 항상 `git checkout develop`.

---

## 7. claude 세션 & 모델

**브랜치당 세션 1개** — `n_new_branch` 가 uuid 발급(`--session-id` 생성) 후 이후 호출은
전부 `--resume` 로 같은 대화에 누적. 브랜치 간엔 격리(새 uuid). 세션 파일:
`~/.claude/projects/C--Users-imcas-JB-Pirate-King/<uuid>.jsonl` (완료된 브랜치는
`claude --resume <uuid>` 로 직접 열람 가능).

세션 누적 순서: ① 지식 주입 → ② 판정들 → ③ 피처 추천 → ④ claude_analyze(FE 상세분석)
— 전부 한 대화. 턴마다 모델만 바꿔 resume (맥락 유지 검증됨).

노드별 모델 배치 (기본값). 원칙: **판정·요약·한줄노트 = sonnet** (잦고 가벼움) /
**발명·심층분석 = opus** (추론 가치). 전부 같은 브랜치 세션을 모델만 바꿔 resume.

| claude 호출 노드 | 하는 일 | 모델 | 플래그 |
|---|---|---|---|
| `j_branch` | 브랜치 생성 점검 verdict | Sonnet 4.6 | `--claude_model` |
| `j_base` | 베이스라인 진단 verdict | Sonnet 4.6 | 〃 |
| `j_reco` | 추천 후보 타당성 verdict | Sonnet 4.6 | 〃 |
| `j_fe` | FE 채택 결과 verdict (라우팅 결합) | Sonnet 4.6 | 〃 |
| `j_build` | 빌드 산출 점검 verdict | Sonnet 4.6 | 〃 |
| `j_release` | 릴리즈 점검 verdict | Sonnet 4.6 | 〃 |
| `j_chain` | 체인 상태 점검 verdict | Sonnet 4.6 | 〃 |
| 지식주입+요약 (`_prime_session`) | team-vault 시드 + 한국어 요약 | Sonnet 4.6 | 〃 |
| `readme` | 루트 README Run Results Note 한 줄 | Sonnet 4.6 | 〃 |
| **`recommend`** | **새 피처 lambda 발명** | **Opus 4.8** | `--claude_model_heavy` |
| **`claude_analyze`** | **FE 상세분석** (전처리/FE 실패/FE 성공 3지점) | **Opus 4.8** | 〃 |

### 도메인 지식 주입 (`--knowledge`, 기본 on)

`KNOWLEDGE_FILES`(team-vault ML/보안 4문서: ML IDS 설계, WISA NMEA flooding, 해상 IDS,
중간발표)를 합쳐(~26K자) 브랜치 세션 **첫 턴**으로 주입 → claude 가 한국어 요약(주요 공격·
탐지방식·피처 아이디어)을 반환해 Slack 에 표시. 이후 판정/추천/분석이 이 지식을 알고 수행.
끄기: `--no-knowledge`.

### 채택 피처 lambda 영속화

`dynamic_candidates.py` 는 매 추천마다 덮어써지므로, 채택된 피처의 lambda 는
`ml/config/adopted_features.py`(git 추적)에 병합 저장(`_persist_adopted`, n_chain) →
feature_engineer 가 시작 시 로드. 없으면 다음 run 의 `--initial_extra` 계산에서 KeyError.

---

## 8. 로깅

| 싱크 | 내용 |
|---|---|
| **`ml/logs/`** (gitignored) | stdout/stderr tee + 모든 Slack 메시지 text(`[HH:MM:SS][브랜치]` 접두사). 시작 시 `run_{시각}.log`, **브랜치 진입마다 `{branch}_{시각}.log` 로 전환** — 파일당 한 브랜치. 머리에 브랜치 구분선 + 풀 claude 세션 uuid |
| Slack `#ais-pipeline` | 서술 로그 — 시작그리드·지식요약·판정 verdict·후보표·게이트 |
| Google Sheets | 구조화 지표 5탭 |
| LangSmith (`.env` 트레이싱) | 노드 span·라우팅·state·latency (관찰 전용) |
| `ml/deploy/{branch}/` | 릴리즈 산출물 아카이브 — 모델 3파일은 run 브랜치에 커밋, tar.gz 는 복사만(ignore) |

---

## 9. 실행

```bash
# 무인 실행 (게이트 자동승인)
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
  --data_file "D:/ais_data/preprocessed/ais_preprocessed_3yr.csv" \
  --base_dir "D:/" --skip_preprocess --auto_approve

# 주요 플래그
#   --invent N                추천 피처 개수 (기본 5)
#   --invent_rounds N         브랜치당 추천 라운드 상한 (기본 3 — 미채택이어도 재추천 후 수렴)
#   --no_judge              모든 판정 끔
#   --claude_model M          경량 모델 — 판정·지식요약 (기본 sonnet)
#   --claude_model_heavy M    심층 모델 — 피처발명·상세분석 (기본 opus)
#   --knowledge/--no-knowledge  team-vault 지식 주입 (기본 on)
#   --max_runs N              브랜치 체인 안전 상한 (기본 50)
#   --build_plugin            WSL tar.gz 빌드 (기본 off, 정식 빌드는 native Linux)
```

> ⚠️ Slack 버튼 승인(`interrupt()` 게이트)은 SocketMode **인바운드**가 필요하다.
> 봇 채널 초대 + Event Subscriptions(`message.channels`)/Interactivity 설정이 안 되어 있으면
> 버튼 클릭이 안 닿으므로 `--auto_approve` 로 운영한다.
