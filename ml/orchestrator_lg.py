"""
AIS 파이프라인 오케스트레이터 — LangGraph 이식판 (방식 B: interrupt 게이트).

방식 A 와 차이: HITL 게이트 3개(FE평가/빌드/수렴)+실패 게이트를 _fe_run 내부의
blocking _wait 가 아니라 **독립 노드 + LangGraph interrupt()** 로 구현한다.

왜 분해가 필요한가:
  interrupt() 는 그래프를 일시정지하고 외부 resume 을 기다린다. 재개 시 그 노드는
  처음부터 재실행되므로, 게이트가 무거운 학습 노드(fe_train) 안에 있으면 재개마다
  재학습이 발생한다. → fe 를 train_eval / build / release 로 쪼개고, 게이트를 그
  경계에 가벼운(부작용 없는) 노드로 둔다. 학습 결과는 State 에 체크포인트로 보존.

이득 (방식 A 대비):
  - 게이트가 노드 경계 → 승인 대기 중 크래시해도 재개 시 **재학습 없음**
    (fe_train 결과가 체크포인트에 보존). 영속 체크포인터(SqliteSaver) 로 바꾸면
    프로세스 재시작까지 견딤. (지금은 MemorySaver = 동일 프로세스 내.)
  - 게이트가 외부 이벤트 드리븐 → Slack 버튼 핸들러가 Command(resume=) 로 재개 가능
    (현재 runner 는 blocking wait_approval 로 단순화. 비동기화는 runner 만 교체).

실행 함수(_fe_train_eval/_fe_build_and_release/_fe_commit_release/stage_preprocess)와
통합(slack/sheets/git/claude)은 orchestrator.py 의
것을 그대로 재사용한다. orchestrator.py 의 동작은 _fe_run 래퍼로 보존됨.

실행:
    python -m ml.orchestrator_lg --model dcdetect --epochs 5 --max_mmsi 3000 \
        --data_file D:/ais_data/preprocessed/ais_preprocessed_3yr.csv \
        --skip_preprocess --auto_approve
"""
from __future__ import annotations

import sys
from typing import Optional, TypedDict

sys.stdout.reconfigure(encoding="utf-8")

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

import ml.orchestrator as orc
import ml.integrations.slack_bot as _sb
import ml.integrations.sheets as _sh
import ml.integrations.git_manager as git


# ─────────────────────────────────────────────
# 직렬화 불가 런타임 핸들(bot/sheet/args)은 State 밖 모듈 전역으로.
# ─────────────────────────────────────────────
class _Runtime:
    bot = None
    sheet = None
    args = None

RT = _Runtime()


# ─────────────────────────────────────────────
# State (직렬화 가능한 값만 — 체크포인터가 보존)
# ─────────────────────────────────────────────
class PipelineState(TypedDict, total=False):
    run_num: int
    branch: str
    current_extra: list
    iters: int
    first_iter: bool
    adopted_any: bool
    # fe 분해 결과 전달
    r: dict                 # _fe_train_eval 반환 dict (전부 직렬화 가능)
    commit_files: list
    build_summary: list
    decision: str           # 게이트 결과 (approve/retry/stop)
    terminate: bool


def _step_info(name: str, run_preprocess: bool) -> tuple:
    stages = (["전처리"] if run_preprocess else []) + ["피처 엔지니어링 학습"]
    total = len(stages)
    idx = stages.index(name)
    nxt = stages[idx + 1] if idx + 1 < total else "파이프라인 종료"
    return (idx + 1, total, nxt)


def _gate(summary: list, key: str, prompt: str) -> str:
    """게이트 결정. auto_approve 면 즉시(=orc._wait 규칙), 아니면 interrupt() 로 일시정지.

    interrupt(payload) 는 첫 호출 시 그래프를 멈추고 payload 를 invoke 결과의
    __interrupt__ 로 노출 → runner 가 Slack 승인 받아 Command(resume=decision) 로 재개 →
    이 호출이 decision 을 반환한다. 게이트 노드는 이 함수만 호출(부작용 없음) → 재개
    시 재실행돼도 무해.
    """
    if RT.args.auto_approve:
        if any("❌" in s for s in summary):
            RT.bot.log(f"🤖 [auto_approve] {prompt} ❌ → stop", "gate")
            return "stop"
        RT.bot.log(f"🤖 [auto_approve] {prompt} → approve", "gate")
        return "approve"
    return interrupt({"gate": key, "prompt": prompt, "summary": summary})


def _update_run_summary(state: PipelineState, new_extra, converged: bool):
    """stage_fe 의 update_run_summary 등가 (B 는 stage_fe 를 우회하므로 직접 호출)."""
    r = state["r"]
    fe_stats = r["fe_stats"]
    cur = state["current_extra"]
    n_adopted = (len(new_extra) - len(cur)) if (new_extra and not converged) else 0
    RT.sheet.update_run_summary(
        fe_steps=n_adopted,
        fe_baseline=fe_stats.get("baseline_det"),
        fe_det=fe_stats.get("det_rate"),
        fe_det_fp5=fe_stats.get("det_fp5"),
        fe_det_fp10=fe_stats.get("det_fp10"),
        fe_n_feat=(len(orc.BASE_FEATURES) + len(new_extra)) if new_extra else None,
        adopted=new_extra or cur,
        threshold=fe_stats.get("threshold"),
        notes="완료" if (new_extra and not converged) else "수렴 완료",
    )


# ─────────────────────────────────────────────
# 노드
# ─────────────────────────────────────────────
def n_new_branch(state: PipelineState) -> dict:
    run_num = git.get_next_run_num(RT.args.model)
    branch = git.create_branch(RT.args.model, run_num)
    RT.sheet.log_run_start(branch, RT.args.model, RT.args.epochs,
                           RT.args.max_mmsi, data_file=RT.args.data_file)
    RT.bot.log_run_start(branch, {
        "모델": RT.args.model, "epochs": RT.args.epochs,
        "max_mmsi": RT.args.max_mmsi, "데이터": RT.args.data_file,
        "base_dir": RT.args.base_dir,
        "베이스 피처": f"{len(orc.BASE_FEATURES)}개",
        "출발 피처": f"{len(state.get('current_extra', []))}개 (기채택)",
    })
    return {"run_num": run_num, "branch": branch, "iters": state.get("iters", 0) + 1}


def n_preprocess(state: PipelineState) -> dict:
    ok = orc.stage_preprocess(RT.bot, RT.sheet, state["branch"], RT.args,
                              _step_info("전처리", True))
    if not ok:
        RT.bot.log("파이프라인 중단", "warning")
        return {"first_iter": False, "terminate": True}
    return {"first_iter": False}


def n_fe_train(state: PipelineState) -> dict:
    """무거운 노드: greedy 1피처 학습+평가+파싱+로깅. 게이트/빌드/릴리즈 없음."""
    run_preprocess = state.get("first_iter", True) and not RT.args.skip_preprocess
    si = _step_info("피처 엔지니어링 학습", run_preprocess)
    orc.WORK_DIR.mkdir(parents=True, exist_ok=True)
    r = orc._fe_train_eval(RT.bot, RT.sheet, state["branch"], RT.args,
                           state["run_num"], state["current_extra"], orc.WORK_DIR, si)
    return {"r": r, "first_iter": False}


def n_gate1(state: PipelineState) -> dict:
    r = state["r"]
    prompt = (f"FE 평가 — `{', '.join(r['newly_adopted'])}` 채택 "
              f"(탐지율 {r['det_str']}%) → 배포 진행?")
    return {"decision": _gate(r["summary"], "gate1", prompt)}


def n_build(state: PipelineState) -> dict:
    commit_files, build_summary = orc._fe_build_and_release(
        RT.bot, RT.sheet, state["branch"], RT.args, state["run_num"], state["r"])
    return {"commit_files": commit_files, "build_summary": build_summary}


def n_gate2(state: PipelineState) -> dict:
    return {"decision": _gate(state["build_summary"], "gate2",
                              "배포 — git 커밋 + GitHub 릴리즈 진행?")}


def n_release(state: PipelineState) -> dict:
    r = state["r"]
    orc._fe_commit_release(RT.bot, RT.sheet, state["branch"], RT.args,
                           state["run_num"], r, state["commit_files"])
    _update_run_summary(state, r["full_extra"], converged=False)
    RT.sheet.log_run_done(state["branch"], RT.args.model, success=True)
    return {}


def n_chain(state: PipelineState) -> dict:
    """채택 확정 → fe_state 저장 + 커밋 → 다음 브랜치(사이클)."""
    full_extra = state["r"]["full_extra"]
    branch = state["branch"]
    orc._save_fe_initial_extra(full_extra)
    git.commit_results(
        [orc.FE_STATE_FILE],
        f"chore(fe): {branch} fe_state 갱신 ({len(full_extra)}피처 누적)",
        branch=branch)
    next_run = git.get_next_run_num(RT.args.model)
    RT.bot.log(f"🔁 채택 완료 ({', '.join(full_extra)}) → "
               f"{RT.args.model}_{next_run:03d} 브랜치로 자동 재시작 (base={branch})",
               "피처개선")
    return {"current_extra": full_extra, "adopted_any": True}


def n_gate_converge(state: PipelineState) -> dict:
    return {"decision": _gate(state["r"]["summary"], "gate_converge",
                              "피처 엔지니어링 — 수렴 완료 → 파이프라인 종료?")}


def n_gate_fail(state: PipelineState) -> dict:
    return {"decision": _gate(state["r"]["summary"], "gate_fail",
                              "피처 엔지니어링 실패 — 재시도?")}


def n_converge(state: PipelineState) -> dict:
    """수렴(채택 0) → update_run_summary + 종료."""
    _update_run_summary(state, [], converged=True)
    RT.sheet.log_run_done(state["branch"], RT.args.model, success=True)
    RT.bot.log_stage_result(
        "파이프라인 완료 — 수렴",
        [f"브랜치: {state['branch']}", "모든 후보 탐색 완료 — 추가 채택 없음"],
        success=True)
    return {"terminate": True}


def n_user_stop(state: PipelineState) -> dict:
    RT.sheet.log_run_done(state["branch"], RT.args.model, success=False)
    RT.bot.log("파이프라인 중단 (사용자 stop)", "warning")
    return {"terminate": True}


# ─────────────────────────────────────────────
# 라우팅
# ─────────────────────────────────────────────
def route_after_branch(state: PipelineState) -> str:
    if state.get("iters", 0) > RT.args.max_runs:
        RT.bot.log(f"⚠️ 안전 상한 도달: {RT.args.max_runs} run 후 종료", "warning")
        return "END"
    run_preprocess = state.get("first_iter", True) and not RT.args.skip_preprocess
    return "preprocess" if run_preprocess else "fe_train"


def route_after_preprocess(state: PipelineState) -> str:
    return "END" if state.get("terminate") else "fe_train"


def route_after_train(state: PipelineState) -> str:
    r = state["r"]
    if r["ret"] != 0:
        return "gate_fail"
    if r["newly_adopted"]:
        return "gate1"
    return "gate_converge"


def route_gate1(state: PipelineState) -> str:
    return {"approve": "build", "retry": "fe_train"}.get(state["decision"], "user_stop")


def route_gate2(state: PipelineState) -> str:
    return "release" if state["decision"] == "approve" else "user_stop"


def route_gate_converge(state: PipelineState) -> str:
    # approve = '종료 승인' → 수렴 처리(재발명 시도 가능). stop = 사용자 즉시 중단.
    return "converge" if state["decision"] == "approve" else "user_stop"


def route_gate_fail(state: PipelineState) -> str:
    return "fe_train" if state["decision"] == "retry" else "user_stop"


# ─────────────────────────────────────────────
# 그래프 빌드
# ─────────────────────────────────────────────
def build_graph(checkpointer=None):
    g = StateGraph(PipelineState)
    for name, fn in [
        ("new_branch", n_new_branch),
        ("preprocess", n_preprocess), ("fe_train", n_fe_train),
        ("gate1", n_gate1), ("build", n_build), ("gate2", n_gate2),
        ("release", n_release), ("chain", n_chain),
        ("gate_converge", n_gate_converge), ("converge", n_converge),
        ("gate_fail", n_gate_fail), ("user_stop", n_user_stop),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "new_branch")
    g.add_conditional_edges("new_branch", route_after_branch,
                            {"preprocess": "preprocess", "fe_train": "fe_train", "END": END})
    g.add_conditional_edges("preprocess", route_after_preprocess,
                            {"fe_train": "fe_train", "END": END})
    g.add_conditional_edges("fe_train", route_after_train,
                            {"gate1": "gate1", "gate_converge": "gate_converge",
                             "gate_fail": "gate_fail"})
    g.add_conditional_edges("gate1", route_gate1,
                            {"build": "build", "fe_train": "fe_train", "user_stop": "user_stop"})
    g.add_edge("build", "gate2")
    g.add_conditional_edges("gate2", route_gate2,
                            {"release": "release", "user_stop": "user_stop"})
    g.add_edge("release", "chain")
    g.add_edge("chain", "new_branch")                 # ← 체이닝 사이클
    g.add_conditional_edges("gate_converge", route_gate_converge,
                            {"converge": "converge", "user_stop": "user_stop"})
    g.add_edge("converge", END)
    g.add_conditional_edges("gate_fail", route_gate_fail,
                            {"fe_train": "fe_train", "user_stop": "user_stop"})
    g.add_edge("user_stop", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


# ─────────────────────────────────────────────
# Runner: interrupt 감지 → Slack 승인 → Command(resume) 재개
# ─────────────────────────────────────────────
def run_pipeline(graph, init: PipelineState, config: dict):
    out = graph.invoke(init, config=config)
    while "__interrupt__" in out:
        payload = out["__interrupt__"][0].value          # {gate, prompt, summary}
        decision = RT.bot.wait_approval(payload["prompt"], payload["summary"])
        out = graph.invoke(Command(resume=decision), config=config)
    return out


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",           default="dcdetect")
    parser.add_argument("--epochs",          type=int, default=5)
    parser.add_argument("--max_mmsi",        type=int, default=500)
    parser.add_argument("--base_dir",        default="D:/")
    parser.add_argument("--raw_dir",         default="D:/ais_data/raw/2025")
    parser.add_argument("--data_file",       default="D:/ais_data/preprocessed/2025/ais_preprocessed_2025.csv")
    parser.add_argument("--skip_preprocess", action="store_true")
    parser.add_argument("--holdout_file",    default=None)
    parser.add_argument("--min_gain",        type=float, default=3.0)
    parser.add_argument("--max_candidates",  type=int, default=None)
    parser.add_argument("--scan_ratio",      type=float, default=1.0)
    parser.add_argument("--candidates",      nargs="*", default=None)
    parser.add_argument("--n_anom",          type=int, default=None)
    parser.add_argument("--overall_tol",     type=float, default=1.0)
    parser.add_argument("--auto_approve",    action="store_true")
    parser.add_argument("--max_runs",        type=int, default=50)
    parser.add_argument("--build_plugin",    action="store_true")
    args = parser.parse_args()

    orc._AUTO_APPROVE = args.auto_approve

    cfg = orc.load_config()
    RT.bot = _sb.SlackPipelineBot(
        cfg["slack"]["bot_token"], cfg["slack"]["app_token"], cfg["slack"]["channel"])
    RT.sheet = _sh.PipelineSheets(
        cfg["google_sheets"]["credentials_file"], cfg["google_sheets"]["sheet_id"])
    RT.args = args

    init: PipelineState = {
        "iters": 0, "first_iter": True,
        "current_extra": orc._load_fe_initial_extra(),
        "adopted_any": False, "terminate": False,
    }

    graph = build_graph()
    config = {"configurable": {"thread_id": "orchestrator_lg"},
              "recursion_limit": max(50, args.max_runs * 15)}

    try:
        run_pipeline(graph, init, config)
    finally:
        try:
            git.checkout("develop")
        except Exception as e:
            print(f"[develop 복구 실패] {e}")


if __name__ == "__main__":
    main()
