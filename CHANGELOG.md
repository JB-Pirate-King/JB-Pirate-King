# Changelog

# 변경 보고서 — 2026-06-17

> 오케스트레이터 judge 비용/수렴 개편(브랜치당 LLM 7→3) + Claude Code 스킬 3종 신규/확장.
> 범위: origin/develop..HEAD + 미커밋 스킬작업 · 커밋 3개 · 머지된 PR 0개

## 요약
LangGraph 오케스트레이터를 시험-run 낭비 절감 방향으로 손봄: 브랜치당 Claude judge 호출이 7→3으로 줄고, 목표점수 상승이 멈추면 조기 수렴하며, judge 재시도 무한반복을 제한하고, 크래시 복구 가능한 체크포인트를 옵션으로 제공. 별도 커밋으로 노드 경계를 `tail -f`로 실시간 보는 node-IO 로그 싱크 추가. 도구 쪽은 스킬 3종 추가: `repo-status`(커밋됨), 이번 세션의 `read-docs`(신규), 회의록을 엮는 `change-report` 확장.

## 코드 변경
- **orchestrator**: judge 강등 — `JUDGE_ON`을 `{baseline, reco, fe}`로 제한; `new_branch/release/chain`은 pass-through; `build` judge는 `n_check_build`(commit_files>0 → continue)로 교체 — judge 호출 7→3 (`ml/orchestrator.py`, `ab72c39`).
- **orchestrator**: 추세 기반 조기 수렴 — 최근 두 라운드의 `best_obj_gain`이 모두 ≤0이면 `route_after_fe`가 수렴, 신규 `state.obj_hist` 사용 (`ab72c39`).
- **orchestrator**: judge 재시도 예산 — `claude_judge`가 `state.retry_count` 추적, `MAX_JUDGE_RETRY=2` 초과 시 verdict를 `continue`로 강등해 flap 루프 차단 (`ab72c39`).
- **orchestrator**: `--persist` 플래그 — `_make_checkpointer`가 `SqliteSaver` 생성(인터럽트 게이트 대기 중 크래시 복구), 부재 시 경고와 함께 `MemorySaver`로 폴백 (`ab72c39`).
- **orchestrator**: 비동기 Sheets — `log_sheet`가 `_SHEET_LOCK`으로 직렬화된 데몬 스레드에서 기록, 핫패스의 수초 블로킹 제거 (`ab72c39`).
- **orchestrator**: 종료 경로 통합 — 미채택 stop이 하드 `user_stop` 대신 converge 게이트(정상 로깅/Sheets)로 라우팅 (`ab72c39`).
- **orchestrator**: 실시간 node-IO 싱크 — 신규 `ml/logs/nodeio_{ts}.log`, `_node_line`/`_open_node_log`로 줄 단위 flush된 `[NODE→]/[NODE←]` 스트림; tee 로그·대용량 `nodes_{ts}.jsonl`과 분리 (`4856cea`).
- **pipeline_steps**: `_fe_train_eval`가 `best_obj_gain` 반환 → 조기 수렴 `obj_hist`에 공급 (`ml/pipeline_steps.py`, `ab72c39`).
- **docs / skills**: `repo-status` 스킬 추가 — 읽기전용 로컬↔원격 divergence 보고 + 리뷰(fetch만, push/pull/merge 안 함) (`.claude/skills/repo-status/SKILL.md`, `6791a8c`).
- **docs / skills**: `read-docs` 스킬 추가 — repo 실문서 읽고 벤더 README 제외, Explore 병렬 fan-out으로 영역별 요약 (`.claude/skills/read-docs/SKILL.md`, 미커밋).
- **docs / skills**: `change-report` 확장 — Step 0 회의록 수집 + `## 회의 맥락`/`## 정합성` 섹션(경로 인자 + `team-vault/자료/` 폴백) (`.claude/skills/change-report/SKILL.md`, 미커밋).

## 영향받는 문서 / 후속작업
- [ ] CLAUDE.md orchestrator 섹션 stale — 신규 `--persist` 플래그, `JUDGE_ON` 강등(7→3), `obj_hist` 조기수렴, `MAX_JUDGE_RETRY`, 비동기 Sheets → `/sync-docs` 실행.
- [ ] `origin/develop`에 안 푼 커밋 3개; `read-docs`+`change-report` 스킬 변경 미커밋 — 커밋/푸시는 사용자 몫.
- [ ] `--persist`는 `langgraph-checkpoint-sqlite` 필요; 부재 시 조용히 MemorySaver 폴백(크래시 복구 불가).
