"""
AIS 파이프라인 오케스트레이터 — LangGraph 완전판.

graph.md 구조도 반영:
  - claude 피처 추천(reco) 노드: 약세 진단 → claude 가 새 후보 피처 발명·검증 → 후보풀 확장.
    수렴(채택0) 시 다른 각도로 재추천 루프(라운드 상한).
  - 노드별 하네스: 각 compute 노드 뒤 claude -p 하네스 → 분석·판정(continue/retry/stop).
  - 사람 게이트: 비가역(배포·커밋) 단계 interrupt() 승인.
  - Sheets: log_sheet(kind) 팩토리로 DRY 로깅.

무거운 실행 함수/통합/파서는 `ml/pipeline_steps.py` 재사용. 제어흐름만 여기 그래프로.

실행:
    python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
        --data_file D:/ais_data/preprocessed/ais_preprocessed_3yr.csv \
        --skip_preprocess --auto_approve
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Optional, TypedDict

sys.stdout.reconfigure(encoding="utf-8")

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

import ml.pipeline_steps as steps
import ml.integrations.slack_bot as _sb
import ml.integrations.sheets as _sh
import ml.integrations.git_manager as git

# 하네스를 켤 노드 (전체). 비용 줄이려면 일부만 남긴다.
HARNESS_ON = {"new_branch", "baseline", "reco", "fe", "build", "release", "chain"}

# 추천 피처를 feature_engineer 가 읽는 동적 후보 파일 (feature_engineer 가 exec 로드)
DYNAMIC_CAND_PATH = "ml/dynamic_candidates.py"


class _Runtime:
    bot = None
    sheet = None
    args = None

RT = _Runtime()


# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────
class PipelineState(TypedDict, total=False):
    run_num: int
    branch: str
    current_extra: list
    iters: int
    first_iter: bool
    # 진단 / 추천
    baseline: dict            # {det, weak, out}
    candidates: list          # 추천된 후보 이름
    tried_feats: list         # 시도한 피처 (중복 회피)
    reco_round: int
    # fe 결과
    r: dict                   # _fe_train_eval 반환 dict
    commit_files: list
    build_summary: list
    # 라우팅
    decision: str             # 하네스/게이트 결정
    harness: dict             # 노드별 하네스 결과
    adopted_any: bool
    terminate: bool


# ─────────────────────────────────────────────
# 하네스 (claude -p 분석·판정) 노드 팩토리
# ─────────────────────────────────────────────
def _claude_json(prompt: str, timeout: int = 120) -> Optional[dict]:
    """claude -p --output-format json 호출 → dict. 실패 시 None."""
    try:
        out = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        if out.returncode == 0 and out.stdout.strip():
            # claude --output-format json 은 메타 래핑이 있을 수 있어 result 추출 시도
            raw = out.stdout.strip()
            data = json.loads(raw)
            if isinstance(data, dict) and "result" in data:
                inner = data["result"]
                return json.loads(inner) if isinstance(inner, str) else inner
            return data
    except Exception as e:
        print(f"[하네스] claude 호출 실패: {e}")
    return None


def _harness_prompt(stage: str, text: str, extra: dict) -> str:
    return (
        f"AIS 이상탐지 ML 파이프라인 [{stage}] 노드 결과를 분석하고 다음 행동을 판정하라.\n\n"
        f"핵심지표(JSON): {json.dumps(extra, ensure_ascii=False)}\n\n"
        f"=== 출력(마지막 부분) ===\n{text[-2500:]}\n\n"
        "아래 JSON 만 출력(설명 산문 금지):\n"
        '{"assessment":"수치 요약 1~2문장","evidence":"근거","verdict":"continue|retry|stop",'
        '"reason":"판정 근거","suggestion":"있으면 다음 개선 아이디어"}\n'
        "verdict 규칙: 정상 진행=continue / 일시오류·재실행 권장=retry / 치명·중단 권장=stop."
    )


def claude_harness(stage: str, ctx_fn):
    """노드 뒤에 붙는 claude -p 하네스 노드 생성.
    ctx_fn(state) -> (분석 텍스트, extra dict). HARNESS_ON 에 없으면 무판정(continue)."""
    def node(state: PipelineState) -> dict:
        if stage not in HARNESS_ON:
            return {"decision": "continue"}
        text, extra = ctx_fn(state)
        v = _claude_json(_harness_prompt(stage, text, extra)) or {
            "assessment": "분석 불가(claude 없음)", "verdict": "continue", "reason": "fallback"}
        verdict = v.get("verdict", "continue")
        RT.bot.log(
            f"🤖 *[{stage}] 하네스* — {v.get('assessment','')}\n"
            f"  → *{verdict}*: {v.get('reason','')}"
            + (f"\n  💡 {v['suggestion']}" if v.get("suggestion") else ""),
            "하네스",
        )
        return {"harness": {**state.get("harness", {}), stage: v}, "decision": verdict}
    return node


def _route_harness(continue_to: str):
    """하네스 verdict → continue_to / fe_train(retry) / user_stop(stop)."""
    def route(state: PipelineState) -> str:
        d = state.get("decision", "continue")
        if d == "stop":
            return "user_stop"
        if d == "retry":
            return "fe_train"
        return continue_to
    return route


# ─────────────────────────────────────────────
# Sheets 로깅 DRY 팩토리
# ─────────────────────────────────────────────
def log_sheet(kind: str):
    """kind: run_start|fe|run_done|converge. 동일 로깅 노드 복제 대신 하나로."""
    def node(state: PipelineState) -> dict:
        s, r, a = RT.sheet, state.get("r", {}), RT.args
        try:
            if kind == "run_start":
                s.log_run_start(state["branch"], a.model, a.epochs, a.max_mmsi,
                                data_file=a.data_file)
            elif kind == "fe":
                fe = r.get("fe_stats", {})
                s.log_fe(state["branch"], state["run_num"], "완료",
                         model=a.model, fe_step=len(r.get("newly_adopted", [])),
                         baseline_det=fe.get("baseline_det"), best_det=fe.get("det_rate"),
                         n_features=r.get("n_feat"), adopted=r.get("newly_adopted"),
                         all_features=r.get("full_extra"),
                         threshold=fe.get("threshold"))
            elif kind == "run_done":
                s.log_run_done(state["branch"], a.model, success=True)
            elif kind == "converge":
                s.update_run_summary(notes="수렴 완료",
                                     adopted=state.get("current_extra"))
        except Exception as e:
            print(f"[Sheets:{kind}] 로깅 실패(무시): {e}")
        return {}
    return node


# ─────────────────────────────────────────────
# 게이트 (interrupt 또는 auto_approve)
# ─────────────────────────────────────────────
def _gate(summary: list, key: str, prompt: str) -> str:
    if RT.args.auto_approve:
        if any("❌" in str(s) for s in summary):
            RT.bot.log(f"🤖 [auto_approve] {prompt} ❌ → stop", "gate")
            return "stop"
        RT.bot.log(f"🤖 [auto_approve] {prompt} → approve", "gate")
        return "approve"
    return interrupt({"gate": key, "prompt": prompt, "summary": summary})


# ─────────────────────────────────────────────
# 코어 노드
# ─────────────────────────────────────────────
def _step_info(name: str, run_preprocess: bool) -> tuple:
    stages = (["전처리"] if run_preprocess else []) + ["피처 엔지니어링 학습"]
    total = len(stages)
    idx = stages.index(name)
    nxt = stages[idx + 1] if idx + 1 < total else "파이프라인 종료"
    return (idx + 1, total, nxt)


def n_new_branch(state: PipelineState) -> dict:
    run_num = git.get_next_run_num(RT.args.model)
    branch = git.create_branch(RT.args.model, run_num)
    RT.bot.log_run_start(branch, {
        "모델": RT.args.model, "epochs": RT.args.epochs, "max_mmsi": RT.args.max_mmsi,
        "데이터": RT.args.data_file, "base_dir": RT.args.base_dir,
        "베이스 피처": f"{len(steps.BASE_FEATURES)}개",
        "출발 피처": f"{len(state.get('current_extra', []))}개 (기채택)",
    })
    return {"run_num": run_num, "branch": branch, "iters": state.get("iters", 0) + 1}


def n_preprocess(state: PipelineState) -> dict:
    ok = steps.stage_preprocess(RT.bot, RT.sheet, state["branch"], RT.args,
                                _step_info("전처리", True))
    if not ok:
        RT.bot.log("파이프라인 중단", "warning")
        return {"first_iter": False, "terminate": True}
    return {"first_iter": False}


def n_fe_baseline(state: PipelineState) -> dict:
    """feature_engineer --diagnose_only → 베이스 탐지율 + 약세 시나리오."""
    RT.bot.log("🧪 *베이스라인 진단* (현재 피처셋, 약세 시나리오 도출)", "피처개선")
    out_json = str(steps.WORK_DIR / "baseline_diag.json")
    steps.WORK_DIR.mkdir(parents=True, exist_ok=True)
    ret, out = steps.run_cmd(
        [sys.executable, "ml/core/feature_engineer.py",
         "--model", RT.args.model, "--input", RT.args.data_file,
         "--base_dir", RT.args.base_dir, "--epochs", str(RT.args.epochs),
         "--max_mmsi", str(RT.args.max_mmsi),
         "--n_anom", str(RT.args.n_anom if RT.args.n_anom else RT.args.max_mmsi),
         "--initial_extra"] + state.get("current_extra", []) + [
         "--candidates", "--diagnose_only", "--out_json", out_json]
        + (["--holdout_file", RT.args.holdout_file] if RT.args.holdout_file else []),
    )
    import re
    det = None
    m = re.search(r"전체 평균 탐지율\s+([\d.]+)%", out)
    if m:
        det = float(m.group(1))
    weak = next((l.strip() for l in out.splitlines() if "약세 시나리오(" in l), "")
    RT.bot.log(f"📊 베이스 탐지율 {det}%\n⚠️ {weak}", "피처개선")
    return {"baseline": {"det": det, "weak": weak, "out": out}}


def _reco_prompt(weak: str, tried: list, base_det) -> str:
    avoid = ("\n시도했으나 효과없던 피처(회피): " + ", ".join(tried)) if tried else ""
    return (
        "AIS 이상탐지 DCdetect 의 피처 엔지니어다. 아래 약세 시나리오를 포착할 새 파생 피처를 "
        f"{RT.args.invent}개 발명하라. 베이스 탐지율 {base_det}%.{avoid}\n"
        f"약세: {weak}\n\n"
        "JSON 배열만 출력(설명 금지). 각 원소:\n"
        '{"name":"snake_case","desc":"한줄","lambda_src":"lambda seq,t: ...",'
        '"target_scenario":"타겟"}\n'
        '컬럼 접근 seq[t][_B["sog"]]. BASE 12: sog,cog,heading,status,dt,dist_km,'
        "cog_hdg_diff,sog_change,cog_hdg_change,speed_consistency,lat_speed,lon_speed. "
        "이전행 seq[t-1] 은 if t>0 else 0.0 가드, 0나눗셈 max(x,1e-6). 순수함수."
    )


def n_recommend(state: PipelineState) -> dict:
    """claude 피처 추천 → 검증 → dynamic_candidates.py 기록 → 후보풀 확장."""
    if not (RT.args.invent and RT.args.invent > 0):
        return {"candidates": []}     # reco 비활성 → 기존 후보 사용
    weak = state.get("baseline", {}).get("weak", "")
    tried = state.get("tried_feats", [])
    arr = _claude_json(_reco_prompt(weak, tried, state.get("baseline", {}).get("det")), timeout=240)
    cands = _validate_recos(arr if isinstance(arr, list) else [])
    if cands:
        _write_dynamic_candidates(cands)
        RT.args.candidates = [c["name"] for c in cands]
        RT.bot.log(f"🧬 *추천 {len(cands)}개*: " +
                   ", ".join(f"`{c['name']}`" for c in cands), "추천")
    else:
        RT.bot.log("⚠️ 추천 실패/0개 — 기존 후보로 진행", "추천")
    return {"candidates": [c["name"] for c in cands],
            "tried_feats": tried + [c["name"] for c in cands]}


def _validate_recos(arr: list) -> list:
    """추천 lambda 를 더미 시퀀스로 exec 검증 + dedup."""
    import math as _math
    ns = {"math": _math, "_ang_diff": lambda a, b: abs((a - b + 180) % 360 - 180)}
    ns["_B"] = {k: i for i, k in enumerate(steps.BASE_FEATURES)}
    dummy = [[0.0] * 12 for _ in range(10)]
    seen, valid = set(), []
    for c in arr:
        name, src = c.get("name"), c.get("lambda_src")
        if not name or not src or name in seen:
            continue
        try:
            fn = eval(src, dict(ns))            # lambda 컴파일
            float(fn(dummy, 5))                 # 실행 가능 검증
            seen.add(name)
            valid.append(c)
        except Exception as e:
            print(f"[추천] '{name}' 제외: {e}")
    return valid


def _write_dynamic_candidates(cands: list):
    body = ["# 자동 생성: orchestrator n_recommend (claude -p)",
            "# feature_engineer 네임스페이스에서 exec → _B/_ang_diff/math 사용 가능",
            "DYNAMIC_FEATURES = {"]
    for c in cands:
        body.append(f'    "{c["name"]}": ({json.dumps(c.get("desc",""), ensure_ascii=False)}, {c["lambda_src"]}),')
    body.append("}")
    with open(DYNAMIC_CAND_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")


def n_fe_train(state: PipelineState) -> dict:
    """scan→adopt→retrain→importance→finaleval→export (feature_engineer 1 subprocess)."""
    run_preprocess = state.get("first_iter", True) and not RT.args.skip_preprocess
    si = _step_info("피처 엔지니어링 학습", run_preprocess)
    steps.WORK_DIR.mkdir(parents=True, exist_ok=True)
    r = steps._fe_train_eval(RT.bot, RT.sheet, state["branch"], RT.args,
                             state["run_num"], state["current_extra"], steps.WORK_DIR, si)
    return {"r": r, "first_iter": False}


def n_build(state: PipelineState) -> dict:
    cf, bs = steps._fe_build_and_release(RT.bot, RT.sheet, state["branch"],
                                         RT.args, state["run_num"], state["r"])
    return {"commit_files": cf, "build_summary": bs}


def n_release(state: PipelineState) -> dict:
    steps._fe_commit_release(RT.bot, RT.sheet, state["branch"], RT.args,
                             state["run_num"], state["r"], state["commit_files"])
    return {}


def n_chain(state: PipelineState) -> dict:
    full_extra = state["r"]["full_extra"]
    steps._save_fe_initial_extra(full_extra)
    git.commit_results([steps.FE_STATE_FILE],
                       f"chore(fe): {state['branch']} fe_state 갱신 ({len(full_extra)}피처)",
                       branch=state["branch"])
    nxt = git.get_next_run_num(RT.args.model)
    RT.bot.log(f"🔁 채택 완료 ({', '.join(full_extra)}) → {RT.args.model}_{nxt:03d} 재시작", "피처개선")
    return {"current_extra": full_extra, "adopted_any": True}


def n_converge(state: PipelineState) -> dict:
    RT.bot.log_stage_result("파이프라인 완료 — 수렴",
                            [f"브랜치: {state['branch']}", "추가 채택 없음"], success=True)
    return {"terminate": True}


def n_user_stop(state: PipelineState) -> dict:
    try:
        RT.sheet.log_run_done(state.get("branch", "?"), RT.args.model, success=False)
    except Exception:
        pass
    RT.bot.log("⏹ 파이프라인 중단", "warning")
    return {"terminate": True}


# ─────────────────────────────────────────────
# 게이트 노드 + 라우팅
# ─────────────────────────────────────────────
def n_gate_deploy(state: PipelineState) -> dict:
    r = state["r"]
    prompt = (f"FE 평가 — `{', '.join(r['newly_adopted'])}` 채택 "
              f"(탐지율 {r['det_str']}%) → 배포 진행?")
    return {"decision": _gate(r["summary"], "deploy", prompt)}


def n_gate_release(state: PipelineState) -> dict:
    return {"decision": _gate(state["build_summary"], "release",
                              "배포 — git 커밋 + GitHub 릴리즈 진행?")}


def n_gate_converge(state: PipelineState) -> dict:
    return {"decision": _gate(state["r"]["summary"], "converge",
                              "피처 엔지니어링 — 수렴 → 종료?")}


def route_after_branch(state: PipelineState) -> str:
    if state.get("iters", 0) > RT.args.max_runs:
        RT.bot.log(f"⚠️ 안전 상한 {RT.args.max_runs} 도달", "warning")
        return "END"
    return "preprocess" if (state.get("first_iter", True) and not RT.args.skip_preprocess) else "fe_baseline"


def route_after_preprocess(state: PipelineState) -> str:
    return "END" if state.get("terminate") else "fe_baseline"


def route_after_fe(state: PipelineState) -> str:
    """하네스 verdict + 채택여부 결합 라우팅."""
    d = state.get("decision", "continue")
    if d == "stop":
        return "user_stop"
    if d == "retry":
        return "fe_baseline"
    r = state.get("r", {})
    if r.get("ret", 0) != 0:
        return "fe_baseline"           # 실패 → 재진단/재시도
    if r.get("newly_adopted"):
        return "gate_deploy"
    # 수렴: 추천 라운드 남으면 재추천, 아니면 종료 게이트
    if (RT.args.invent and RT.args.invent > 0
            and not state.get("adopted_any")
            and state.get("reco_round", 1) < RT.args.invent_rounds):
        return "reco_again"
    return "gate_converge"


def n_reco_again(state: PipelineState) -> dict:
    """수렴 → 라운드 올리고 재추천 진입 표시."""
    return {"reco_round": state.get("reco_round", 1) + 1}


def route_gate_deploy(state: PipelineState) -> str:
    return {"approve": "build", "retry": "fe_baseline"}.get(state["decision"], "user_stop")


def route_gate_release(state: PipelineState) -> str:
    return "release" if state["decision"] == "approve" else "user_stop"


def route_gate_converge(state: PipelineState) -> str:
    return "converge" if state["decision"] == "approve" else "user_stop"


# ─────────────────────────────────────────────
# 그래프
# ─────────────────────────────────────────────
def build_graph(checkpointer=None):
    g = StateGraph(PipelineState)

    # 코어
    for n, fn in [("new_branch", n_new_branch), ("preprocess", n_preprocess),
                  ("fe_baseline", n_fe_baseline), ("recommend", n_recommend),
                  ("reco_again", n_reco_again), ("fe_train", n_fe_train),
                  ("build", n_build), ("release", n_release), ("chain", n_chain),
                  ("converge", n_converge), ("user_stop", n_user_stop),
                  ("gate_deploy", n_gate_deploy), ("gate_release", n_gate_release),
                  ("gate_converge", n_gate_converge),
                  ("log_run_start", log_sheet("run_start")), ("log_fe", log_sheet("fe")),
                  ("log_run_done", log_sheet("run_done")), ("log_converge", log_sheet("converge"))]:
        g.add_node(n, fn)

    # 하네스 (코어 뒤)
    g.add_node("h_branch", claude_harness("new_branch", lambda s: (s.get("branch", ""), {})))
    g.add_node("h_base", claude_harness("baseline", lambda s: (s["baseline"]["out"], {"det": s["baseline"]["det"]})))
    g.add_node("h_reco", claude_harness("reco", lambda s: (", ".join(s.get("candidates", [])), {"n": len(s.get("candidates", []))})))
    g.add_node("h_fe", claude_harness("fe", lambda s: ("\n".join(s["r"].get("summary", [])), s["r"].get("fe_stats", {}))))
    g.add_node("h_build", claude_harness("build", lambda s: ("\n".join(s.get("build_summary", [])), {})))
    g.add_node("h_release", claude_harness("release", lambda s: (s["branch"], {"det": s["r"].get("det_str")})))
    g.add_node("h_chain", claude_harness("chain", lambda s: (", ".join(s.get("current_extra", [])), {})))

    # 배선
    g.add_edge(START, "new_branch")
    g.add_edge("new_branch", "log_run_start")
    g.add_edge("log_run_start", "h_branch")
    g.add_conditional_edges("h_branch", route_after_branch_h,
                            {"preprocess": "preprocess", "fe_baseline": "fe_baseline",
                             "user_stop": "user_stop", "END": END})
    g.add_conditional_edges("preprocess", route_after_preprocess,
                            {"fe_baseline": "fe_baseline", "END": END})

    g.add_edge("fe_baseline", "h_base")
    g.add_conditional_edges("h_base", _route_harness("recommend"),
                            {"recommend": "recommend", "fe_train": "fe_train", "user_stop": "user_stop"})
    g.add_edge("recommend", "h_reco")
    g.add_conditional_edges("h_reco", _route_harness("fe_train"),
                            {"fe_train": "fe_train", "user_stop": "user_stop"})

    g.add_edge("fe_train", "log_fe")
    g.add_edge("log_fe", "h_fe")
    g.add_conditional_edges("h_fe", route_after_fe,
                            {"gate_deploy": "gate_deploy", "gate_converge": "gate_converge",
                             "reco_again": "reco_again", "fe_baseline": "fe_baseline",
                             "user_stop": "user_stop"})
    g.add_edge("reco_again", "recommend")

    g.add_conditional_edges("gate_deploy", route_gate_deploy,
                            {"build": "build", "fe_baseline": "fe_baseline", "user_stop": "user_stop"})
    g.add_edge("build", "h_build")
    g.add_conditional_edges("h_build", _route_harness("gate_release"),
                            {"gate_release": "gate_release", "fe_train": "fe_train", "user_stop": "user_stop"})
    g.add_conditional_edges("gate_release", route_gate_release,
                            {"release": "release", "user_stop": "user_stop"})
    g.add_edge("release", "h_release")
    g.add_conditional_edges("h_release", _route_harness("log_run_done"),
                            {"log_run_done": "log_run_done", "fe_train": "fe_train", "user_stop": "user_stop"})
    g.add_edge("log_run_done", "chain")
    g.add_edge("chain", "h_chain")
    g.add_conditional_edges("h_chain", _route_harness("new_branch"),
                            {"new_branch": "new_branch", "fe_train": "fe_train", "user_stop": "user_stop"})

    g.add_conditional_edges("gate_converge", route_gate_converge,
                            {"converge": "converge", "user_stop": "user_stop"})
    g.add_edge("converge", "log_converge")
    g.add_edge("log_converge", END)
    g.add_edge("user_stop", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def route_after_branch_h(state: PipelineState) -> str:
    """new_branch 하네스 verdict 먼저(stop), 그다음 max_runs/전처리 분기."""
    if state.get("decision") == "stop":
        return "user_stop"
    return route_after_branch(state)


# ─────────────────────────────────────────────
# Runner + main
# ─────────────────────────────────────────────
def run_pipeline(graph, init: PipelineState, config: dict):
    out = graph.invoke(init, config=config)
    while "__interrupt__" in out:
        payload = out["__interrupt__"][0].value
        decision = RT.bot.wait_approval(payload["prompt"], payload["summary"])
        out = graph.invoke(Command(resume=decision), config=config)
    return out


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="dcdetect")
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--max_mmsi", type=int, default=500)
    p.add_argument("--base_dir", default="D:/")
    p.add_argument("--raw_dir", default="D:/ais_data/raw/2025")
    p.add_argument("--data_file", default="D:/ais_data/preprocessed/2025/ais_preprocessed_2025.csv")
    p.add_argument("--skip_preprocess", action="store_true")
    p.add_argument("--holdout_file", default=None)
    p.add_argument("--min_gain", type=float, default=3.0)
    p.add_argument("--max_candidates", type=int, default=None)
    p.add_argument("--scan_ratio", type=float, default=1.0)
    p.add_argument("--candidates", nargs="*", default=None)
    p.add_argument("--invent", type=int, default=0, help="claude 피처 추천 N개 (0=비활성)")
    p.add_argument("--invent_rounds", type=int, default=1, help="수렴 시 재추천 라운드 상한")
    p.add_argument("--n_anom", type=int, default=None)
    p.add_argument("--overall_tol", type=float, default=1.0)
    p.add_argument("--auto_approve", action="store_true")
    p.add_argument("--max_runs", type=int, default=50)
    p.add_argument("--build_plugin", action="store_true")
    p.add_argument("--no_harness", action="store_true", help="모든 하네스 끔")
    args = p.parse_args()

    steps._AUTO_APPROVE = args.auto_approve
    if args.no_harness:
        HARNESS_ON.clear()

    cfg = steps.load_config()
    RT.bot = _sb.SlackPipelineBot(cfg["slack"]["bot_token"], cfg["slack"]["app_token"],
                                  cfg["slack"]["channel"])
    RT.sheet = _sh.PipelineSheets(cfg["google_sheets"]["credentials_file"],
                                  cfg["google_sheets"]["sheet_id"])
    RT.args = args

    init: PipelineState = {
        "iters": 0, "first_iter": True, "reco_round": 1,
        "current_extra": steps._load_fe_initial_extra(),
        "tried_feats": [], "adopted_any": False, "terminate": False,
    }
    graph = build_graph()
    config = {"configurable": {"thread_id": "orchestrator"},
              "recursion_limit": max(80, args.max_runs * 25)}
    try:
        run_pipeline(graph, init, config)
    finally:
        try:
            git.checkout("develop")
        except Exception as e:
            print(f"[develop 복구 실패] {e}")


if __name__ == "__main__":
    main()
