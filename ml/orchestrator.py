"""
AIS 파이프라인 오케스트레이터 — LangGraph 완전판.

구조 (상세: ml/PIPELINE.md, 렌더: ml/pipeline_langgraph.png):
  - claude 피처 추천(reco) 노드: 약세 진단 → claude 가 새 후보 피처 발명·검증 → 후보풀 확장.
    수렴(채택0) 시 다른 각도로 재추천 루프(라운드 상한).
  - 노드별 판정: 각 compute 노드 뒤 claude -p 판정 → 분석·판정(continue/retry/stop).
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
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, TypedDict

sys.stdout.reconfigure(encoding="utf-8")

# .env 로드 (LANGCHAIN_* 등)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.strip() and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

import ml.pipeline_steps as steps
import ml.integrations.slack_bot as _sb
import ml.integrations.sheets as _sh
import ml.integrations.git_manager as git

# 판정(judge)을 켤 노드 (전체). 비용 줄이려면 일부만 남긴다.
JUDGE_ON = {"new_branch", "baseline", "reco", "fe", "build", "release", "chain"}

# 노드별 판정 포인트 — 같은 템플릿이지만 단계 고유의 관점을 주입해
# claude 가 그 단계에 맞는 기준으로 평가하게 한다 (일률 판정 방지).
STAGE_FOCUS = {
    "new_branch": "Whether branch creation and claude-session init are healthy. Almost always continue (stop only on fatal error).",
    "baseline":   "Whether the baseline detection rate is a valid FE starting point and the weak scenarios are addressable by feature recommendation. "
                  "If abnormally low (data/training defect) or broadly 0%, consider retry/stop.",
    "reco":       "Whether the invented candidates target the weak scenarios and are not duplicate or meaningless. "
                  "If 0 candidates or all unsuitable, retry.",
    "fe":         "Whether the adopted feature's objective-score gain is robust (suspect winner's curse / overfitting) "
                  "and there is no overall FP=1% regression. Zero adoption is a convergence signal, not an error.",
    "build":      "Whether the C++ patch (5 markers) was applied and the 3 model files were copied without omission, and the feature count (ML_FEATURE_COUNT) matches.",
    "release":    "Whether the commit and GitHub release artifacts are correct, with no missing attachments.",
    "chain":      "Whether the adopted feature set was saved to fe_state and the state is ready to continue to the next branch.",
}

# 추천 피처를 feature_engineer 가 읽는 동적 후보 파일 (feature_engineer 가 exec 로드)
DYNAMIC_CAND_PATH = "ml/dynamic_candidates.py"

# 채택된 동적 피처의 lambda 영속 파일 (git 추적). dynamic_candidates.py 는 매 추천마다
# 덮어써지므로, 채택분은 여기로 옮겨 보존해야 다음 run 의 --initial_extra 계산이 가능.
ADOPTED_FEATS_PATH = "ml/config/adopted_features.py"


class _Runtime:
    bot = None
    sheet = None
    args = None
    claude_sid = None        # 브랜치당 claude 세션 id (노드 간 맥락 누적)
    claude_started = False   # 해당 세션 첫 호출 여부 (--session-id vs --resume)
    knowledge = ""           # 세션 시작 시 주입할 도메인 지식 (team-vault, --knowledge)
    last_cands = {}          # 이번 브랜치 추천 후보 dict (name → {desc, lambda_src}) — 채택 영속화용

RT = _Runtime()


# ─────────────────────────────────────────────
# 파일 로깅 — stdout/stderr tee + 브랜치별 로그 파일 전환
# ─────────────────────────────────────────────
_LOG = {"f": None, "path": None}   # 현재 로그 파일 핸들/경로 (브랜치마다 교체)


def _switch_log(path):
    """로그 파일 전환 — 이전 파일 닫고 새 파일 열기. bot.log_file 도 동기화."""
    if _LOG["f"]:
        try:
            _LOG["f"].close()
        except Exception:
            pass
    _LOG["f"] = open(path, "a", encoding="utf-8", errors="replace")
    _LOG["path"] = str(path)
    if RT.bot:
        RT.bot.log_file = str(path)


class _Tee:
    """stdout/stderr 복제 — 콘솔 + 현재 _LOG 파일 동시 기록."""
    def __init__(self, stream):
        self.stream = stream
    def write(self, s):
        self.stream.write(s)
        if _LOG["f"]:
            _LOG["f"].write(s)
            _LOG["f"].flush()
    def flush(self):
        self.stream.flush()


# ─────────────────────────────────────────────
# 도메인 지식 (team-vault Notion→md) — 브랜치 세션 시작 시 1회 주입
# ─────────────────────────────────────────────
# ML/보안 관련 문서만 (OpenCPN C++ 빌드 매뉴얼은 FE/판정과 무관 → 제외).
KNOWLEDGE_FILES = [
    "team-vault/자료/머신러닝 기반 선박 AIS IDS 설계 및 구현.md",
    "team-vault/자료/WISA_2025_Poster_Design_and_Analysis_of_Dynamic_Flooding_Attack_Scenarios_Based_on_the_NMEA_Protocol.md",
    "team-vault/자료/해상 네트워크에서의 IDS 적용 가능성 연구 SCADA 환경과의 비교 분석.md",
    "team-vault/자료/프로젝트 중간발표.md",
]


def _load_knowledge(max_chars: int = 60000) -> str:
    """KNOWLEDGE_FILES 를 읽어 frontmatter 제거 후 합친다. 없는 파일은 건너뜀."""
    import re as _re
    parts = []
    for path in KNOWLEDGE_FILES:
        p = Path(path)
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        txt = _re.sub(r"^---\n.*?\n---\n", "", txt, count=1, flags=_re.S)  # YAML frontmatter 제거
        parts.append(f"## {p.stem}\n{txt.strip()}")
    return "\n\n".join(parts)[:max_chars]


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
    decision: str             # 판정/게이트 결정
    judge: dict             # 노드별 판정 결과
    adopted_any: bool
    terminate: bool


# ─────────────────────────────────────────────
# 판정(judge) 노드 팩토리 — claude -p 분석·판정
# ─────────────────────────────────────────────
def _claude_json(prompt: str, timeout: int = 120,
                 session: Optional[str] = None, first: bool = False,
                 model: Optional[str] = None) -> Optional[dict]:
    """claude -p --output-format json 호출 → dict. 실패 시 None.

    session 지정 시 브랜치 세션에 묶음: 첫 호출(first=True)은 --session-id 로
    세션 생성, 이후는 --resume 로 같은 대화를 이어가 맥락이 누적된다."""
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if session:
        cmd += (["--session-id", session] if first else ["--resume", session])
    try:
        out = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        if out.returncode == 0 and out.stdout.strip():
            # claude --output-format json 은 메타 래핑이 있을 수 있어 result 추출 시도
            raw = out.stdout.strip()
            data = json.loads(raw)
            if isinstance(data, dict) and "result" in data:
                inner = data["result"]
                # 모델이 ```json … ``` 코드펜스로 감싸 반환하면 json.loads 가 char 0 에서 실패한다.
                return json.loads(_strip_code_fence(inner)) if isinstance(inner, str) else inner
            return data
    except Exception as e:
        print(f"[판정] claude 호출 실패: {e}")
    return None


def _strip_code_fence(text: str) -> str:
    """모델 응답에서 ```json … ``` / ``` … ``` 코드펜스를 벗겨 순수 JSON 만 남긴다.
    펜스가 없으면 원문 그대로. 펜스 안에 JSON 이 있으면 그것만 반환."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl != -1 else t[3:]   # 첫 줄(```json) 제거
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]                  # 닫는 ``` 제거
    return t.strip()


def _branch_claude(prompt: str, timeout: int = 120, heavy: bool = False) -> Optional[dict]:
    """브랜치당 claude 세션 1개를 유지하며 호출 — 노드 간 맥락 누적 + 캐시 재사용.

    첫 호출은 --session-id 로 RT.claude_sid 세션을 만들고, 이후는 --resume 로
    이어붙인다. 같은 브랜치의 추천/판정가 베이스라인·채택 결과를 기억한 채 판정한다.
    세션 id 가 없으면(미설정) stateless 단발 호출로 폴백.

    heavy=True 면 창의/심층 작업용 모델(--claude_model_heavy, 기본 opus) 사용 —
    같은 세션을 턴마다 다른 모델로 resume 해도 맥락은 유지된다 (검증됨)."""
    sid = RT.claude_sid
    first = not RT.claude_started
    model = getattr(RT.args, "claude_model_heavy" if heavy else "claude_model", None)
    out = _claude_json(prompt, timeout, session=sid, first=first, model=model)
    if sid:
        RT.claude_started = True   # 생성 시도 후엔 항상 resume (턴은 이미 기록됨)
    return out


def _prime_session(knowledge: str):
    """브랜치 세션을 도메인 지식으로 시드 — 첫 호출(--session-id)로 지식을 넣어두면
    이후 판정/추천이 --resume 로 그 지식을 알고 판정·발명한다.
    JSON 파싱 불필요(응답 무시)하므로 _claude_json 대신 직접 호출."""
    if not (knowledge and RT.claude_sid):
        return
    prompt = (
        "You are joining an AIS anomaly-detection ML pipeline as its analyst and feature engineer. "
        "Study the project domain knowledge below (research notes on AIS spoofing, ML-based IDS design, "
        "NMEA flooding attack scenarios). Use it in every later judgment and feature invention in THIS session.\n"
        "After studying, reply with a SHORT KOREAN summary (3-4 bullet lines, no preamble) of the key points "
        "you will use: main attack types, detection approach, and 1-2 concrete feature ideas.\n\n"
        "=== PROJECT KNOWLEDGE ===\n" + knowledge
    )
    cmd = ["claude", "-p", prompt, "--session-id", RT.claude_sid]
    model = getattr(RT.args, "claude_model", None)
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=240)
        RT.claude_started = True   # 세션 생성됨 → 이후는 resume
        summary = r.stdout.strip() if r.returncode == 0 else ""
        RT.bot.log(
            f"📚 *도메인 지식 주입* ({len(knowledge):,}자 · {len(KNOWLEDGE_FILES)}개 문서) → 세션 시드"
            + (f"\n{summary}" if summary else ""),
            "지식",
        )
    except Exception as e:
        print(f"[지식주입] 실패(무시): {e}")


def _judge_prompt(stage: str, text: str, extra: dict) -> str:
    focus = STAGE_FOCUS.get(stage, "")
    return (
        f"Analyze the [{stage}] node result of the AIS anomaly-detection ML pipeline and decide the next action.\n\n"
        + (f"Judging focus for this stage: {focus}\n\n" if focus else "")
        + f"Key metrics (JSON): {json.dumps(extra, ensure_ascii=False)}\n\n"
        f"=== Node output (tail) ===\n{text[-2500:]}\n\n"
        "Output ONLY the JSON below (no prose). Write assessment/evidence/reason/suggestion in KOREAN "
        "(they are shown to the operator in Slack):\n"
        '{"assessment":"수치 요약 1~2문장","evidence":"근거","verdict":"continue|retry|stop",'
        '"reason":"판정 근거","suggestion":"있으면 다음 개선 아이디어"}\n'
        "verdict rules: normal progress=continue / transient error, rerun advised=retry / fatal, stop advised=stop."
    )


def claude_judge(stage: str, ctx_fn):
    """노드 뒤에 붙는 claude -p 판정 노드 생성.
    ctx_fn(state) -> (분석 텍스트, extra dict). JUDGE_ON 에 없으면 무판정(continue)."""
    def node(state: PipelineState) -> dict:
        if stage not in JUDGE_ON:
            return {"decision": "continue"}
        text, extra = ctx_fn(state)
        v = _branch_claude(_judge_prompt(stage, text, extra)) or {
            "assessment": "분석 불가(claude 없음)", "verdict": "continue", "reason": "fallback"}
        verdict = v.get("verdict", "continue")
        sug = (v.get("suggestion") or "").strip()
        has_sug = sug and sug.lower() not in ("없음", "none", "n/a", "-", "null", "없음.")
        RT.bot.log(
            f"🤖 *[{stage}] 판정* — {v.get('assessment','')}\n"
            f"  → *{verdict}*: {v.get('reason','')}"
            + (f"\n  💡 {sug}" if has_sug else ""),
            "판정",
        )
        return {"judge": {**state.get("judge", {}), stage: v}, "decision": verdict}
    return node


def _route_judge(continue_to: str, retry_to: str):
    """판정 verdict → continue_to / retry_to / user_stop(stop).

    retry 는 **직전 노드 재실행** — 예전엔 일률 fe_train 으로 보내
    j_base retry 가 후보 0인 채 스캔에 진입하는 등 비논리적이었다."""
    def route(state: PipelineState) -> str:
        d = state.get("decision", "continue")
        if d == "stop":
            return "user_stop"
        if d == "retry":
            return retry_to
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
    # 새 브랜치마다 새 claude 세션 — 노드 간 맥락은 누적하되 브랜치 간엔 격리.
    RT.claude_sid = str(uuid.uuid4())
    RT.claude_started = False
    steps._CLAUDE_SID = RT.claude_sid   # claude_analyze(steps)도 같은 세션 --resume
    RT.bot.current_branch = branch      # 파일 로그 라인의 [브랜치] 접두사
    # 브랜치별 로그 파일로 전환: ml/logs/{branch}_{시각}.log
    _switch_log(Path("ml/logs") / f"{branch}_{time.strftime('%Y%m%d_%H%M%S')}.log")
    # 로그 파일에 브랜치 구분선 + 풀 세션 uuid (stdout tee 로 기록됨)
    print(f"\n{'='*70}\n===== {branch} 시작 — claude session {RT.claude_sid} =====\n{'='*70}")
    _prime_session(RT.knowledge)        # 도메인 지식 시드 (--knowledge, team-vault)
    RT.bot.log_run_start(branch, {
        "모델": RT.args.model, "epochs": RT.args.epochs, "max_mmsi": RT.args.max_mmsi,
        "데이터": RT.args.data_file, "base_dir": RT.args.base_dir,
        "베이스 피처": f"{len(steps.BASE_FEATURES)}개",
        "출발 피처": f"{len(state.get('current_extra', []))}개 (기채택)",
        "claude세션": RT.claude_sid[:8],
    })
    # reco_round 는 브랜치 단위 리셋 — 각 브랜치가 invent_rounds 만큼 재추천 기회를 가짐
    return {"run_num": run_num, "branch": branch,
            "iters": state.get("iters", 0) + 1, "reco_round": 1}


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
    avoid = ("\nFeatures already tried without effect (avoid): " + ", ".join(tried)) if tried else ""
    return (
        "You are the feature engineer for the AIS anomaly-detection model DCdetect. "
        f"Invent {RT.args.invent} new derived features that capture the weak scenarios below. "
        f"Baseline detection rate {base_det}%.{avoid}\n"
        f"Weak scenarios: {weak}\n\n"
        "Output ONLY a JSON array (no prose). Each element (write `desc` in KOREAN — it is shown in Slack):\n"
        '{"name":"snake_case","desc":"한줄 설명","lambda_src":"lambda seq,t: ...",'
        '"target_scenario":"target"}\n'
        'Column access seq[t][_B["sog"]]. BASE 12: sog,cog,heading,status,dt,dist_km,'
        "cog_hdg_diff,sog_change,cog_hdg_change,speed_consistency,lat_speed,lon_speed. "
        "Guard previous row seq[t-1] with `if t>0 else 0.0`; guard zero-division with max(x,1e-6). Pure function."
    )


def n_recommend(state: PipelineState) -> dict:
    """claude 피처 추천 → 검증 → dynamic_candidates.py 기록 → 후보풀 확장."""
    weak = state.get("baseline", {}).get("weak", "")
    tried = state.get("tried_feats", [])
    arr = _branch_claude(_reco_prompt(weak, tried, state.get("baseline", {}).get("det")),
                         timeout=240, heavy=True)   # 피처 발명 = 창의 작업 → opus
    cands = _validate_recos(arr if isinstance(arr, list) else [])
    if cands:
        RT.last_cands.update({c["name"]: c for c in cands})   # 채택 시 lambda 영속화용 보관
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


def _persist_adopted(names: list) -> bool:
    """채택된 피처의 lambda 를 ADOPTED_FEATS_PATH 에 병합 저장 (이전 내용 유지).
    feature_engineer 가 시작 시 이 파일을 로드해 initial_extra 계산에 쓴다."""
    entries = {}
    p = Path(ADOPTED_FEATS_PATH)
    if p.exists():   # 기존 채택분 로드 (exec → ADOPTED_FEATURES) — desc/lambda_src 원문은
        try:         # 재구성 불가하므로 파일 텍스트 파싱 대신 소스 라인 보존 방식 사용
            existing = p.read_text(encoding="utf-8").splitlines()
            entries = {l.split('"')[1]: l for l in existing
                       if l.strip().startswith('"') and '": (' in l}
        except Exception as e:
            print(f"[채택영속화] 기존 파일 파싱 실패(새로 작성): {e}")
    added = False
    for n in names:
        c = RT.last_cands.get(n)
        if not c or n in entries:
            continue
        entries[n] = (f'    "{n}": ({json.dumps(c.get("desc", ""), ensure_ascii=False)}, '
                      f'{c["lambda_src"]}),')
        added = True
    if not entries:
        return False
    body = ["# 채택된 동적 피처 lambda 영속 보관 — orchestrator n_chain 이 채택 시 병합 기록.",
            "# feature_engineer 가 시작 시 exec 로드 (initial_extra 계산에 필수). git 추적.",
            "ADOPTED_FEATURES = {"] + list(entries.values()) + ["}"]
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return added


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


def n_readme(state: PipelineState) -> dict:
    """릴리즈 후 루트 README.md 의 Run Results 표에 이번 run 결과 행 추가 (claude 노드).

    수치는 FE 산출 JSON 에서 코드로 뽑고, Note 한 줄은 브랜치 세션(지식·판정·분석 누적)을
    --resume 한 claude 가 작성. README 는 run 브랜치에 커밋된다."""
    r = state.get("r", {})
    fe = r.get("fe_stats", {})
    # FP5/10·시나리오별 탐지율은 fe_stats 에 없어 FE JSON 에서 보강
    det5 = det10 = None
    d = {}
    fe_json = steps.WORK_DIR / "feat_eng_iter01.json"
    if fe_json.exists():
        try:
            d = json.loads(fe_json.read_text(encoding="utf-8"))
            det5, det10 = d.get("det_fp5"), d.get("det_fp10")
        except Exception:
            pass

    v = _branch_claude(
        "Write ONE short Korean sentence (<=80 chars) summarizing this run's outcome and "
        "the adopted feature's significance, for the project README results table. "
        'Output ONLY JSON: {"note":"..."}', timeout=60) or {}
    note = (v.get("note") or "").replace("|", "/").strip()

    base, det = fe.get("baseline_det"), fe.get("det_rate")
    fmt = lambda x, s="%": (f"{x:.1f}{s}" if isinstance(x, (int, float)) else "-")
    row = ("| {b} | {d} | {a} | {fp1} | {fp5} | {fp10} | {th} | {nf} | {note} |").format(
        b=state["branch"], d=time.strftime("%Y-%m-%d"),
        a=", ".join(f"`{f}`" for f in r.get("newly_adopted", [])) or "-",
        fp1=(f"{fmt(base)}→{fmt(det)}" + (f" ({det-base:+.1f}pp)" if isinstance(base,(int,float)) and isinstance(det,(int,float)) else "")),
        fp5=fmt(det5), fp10=fmt(det10),
        th=(f"{fe.get('threshold'):.6f}" if isinstance(fe.get("threshold"), (int, float)) else "-"),
        nf=r.get("n_feat", "-"), note=note or "-")

    detail = _readme_detail_block(state["branch"], r, fe, d, note)

    try:
        p = Path("README.md")
        txt = p.read_text(encoding="utf-8")
        begin, end = "<!-- RUN_RESULTS:BEGIN -->", "<!-- RUN_RESULTS:END -->"
        head, rest = txt.split(begin, 1)
        block, tail = rest.split(end, 1)
        lines = [l for l in block.strip().splitlines() if l.strip()]
        table_head, rows = lines[:2], lines[2:]          # 헤더 2줄 유지, 최신 행을 위로
        new_block = "\n".join(table_head + [row] + rows)
        txt = head + begin + "\n" + new_block + "\n" + end + tail
        # 상세 블록 (시나리오별 FP1/5/10) — RUN_DETAILS 마커에 최신순 prepend
        db, de = "<!-- RUN_DETAILS:BEGIN -->", "<!-- RUN_DETAILS:END -->"
        if detail and db in txt:
            head2, rest2 = txt.split(db, 1)
            old_details, tail2 = rest2.split(de, 1)
            txt = head2 + db + "\n" + detail + "\n" + old_details.strip("\n") + "\n" + de + tail2
        p.write_text(txt, encoding="utf-8")
        git.commit_results(["README.md"],
                           f"docs: {state['branch']} run result → README", branch=state["branch"])
        RT.bot.log(f"📝 README Run Results 갱신 — {state['branch']}"
                   + (f"\n  └ {note}" if note else ""), "지식")
    except Exception as e:
        print(f"[readme] 갱신 실패(무시): {e}")
    return {}


def _readme_detail_block(branch: str, r: dict, fe: dict, d: dict, note: str) -> str:
    """run 상세 — FP별 + 시나리오(공격 유형)별 탐지율을 접이식 블록으로.
    d = FE 산출 JSON (scenario_fp1/fp5/fp10, feature_descriptions 포함). 비면 생략."""
    sc1 = d.get("scenario_fp1") or {}
    if not sc1:
        return ""
    sc5, sc10 = d.get("scenario_fp5") or {}, d.get("scenario_fp10") or {}
    descs = d.get("feature_descriptions", {})
    adopted = r.get("newly_adopted", [])
    base, det = fe.get("baseline_det"), fe.get("det_rate")
    fmt = lambda x: (f"{x:.1f}%" if isinstance(x, (int, float)) else "-")

    L = [f"<details>",
         f"<summary><b>{branch}</b> — " + (", ".join(f"<code>{f}</code>" for f in adopted) or "no adoption")
         + f" · FP=1% {fmt(base)}→{fmt(det)} · {time.strftime('%Y-%m-%d')}</summary>", ""]
    for f in adopted:
        if descs.get(f):
            L.append(f"- `{f}` — {descs[f]}")
    if note:
        L.append(f"- 🤖 {note}")
    L += ["", "| Scenario (attack type) | FP=1% | FP=5% | FP=10% | |",
          "|---|---|---|---|---|"]
    for name, v1 in sorted(sc1.items(), key=lambda kv: kv[1]):
        flag = "⚠️ weak" if v1 < 50 else ""
        L.append(f"| {name} | {v1:.1f}% | {fmt(sc5.get(name))} | {fmt(sc10.get(name))} | {flag} |")
    L += ["", "</details>", ""]
    return "\n".join(L)


def n_chain(state: PipelineState) -> dict:
    full_extra = state["r"]["full_extra"]
    steps._save_fe_initial_extra(full_extra)
    # 채택 피처 lambda 영속화 — 안 하면 다음 run 의 dynamic_candidates 덮어쓰기로
    # lambda 유실 → feature_engineer KeyError (initial_extra 계산 불가).
    commit_files = [steps.FE_STATE_FILE]
    if _persist_adopted(state["r"].get("newly_adopted", [])):
        commit_files.append(ADOPTED_FEATS_PATH)
    git.commit_results(commit_files,
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


def route_after_chain(state: PipelineState) -> str:
    """체인 후 라우팅 — 다음 브랜치를 **생성하기 전에** max_runs 가드.
    판정 verdict 우선(stop/retry), 그다음 상한 도달 시 빈 브랜치를 만들지 않고 종료.
    (전엔 new_branch 가 빈 브랜치를 만든 뒤 route_after_branch 가 END 처리해 낭비 브랜치가 생겼다.)"""
    d = state.get("decision", "continue")
    if d == "stop":
        return "user_stop"
    if d == "retry":
        return "chain"   # 직전 노드 재실행 (fe_state 저장은 멱등)
    if state.get("iters", 0) >= RT.args.max_runs:
        RT.bot.log(f"⚠️ 안전 상한 {RT.args.max_runs} 도달 — 체이닝 종료", "warning")
        return "END"
    return "new_branch"


def route_after_preprocess(state: PipelineState) -> str:
    return "END" if state.get("terminate") else "fe_baseline"


def route_after_fe(state: PipelineState) -> str:
    """판정 verdict + 채택여부 결합 라우팅."""
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
    # 수렴 판단에 횟수 기준: 이 브랜치에서 미채택이라도 invent_rounds 까지
    # 다른 각도로 재추천 (라운드 비용은 baseline_cache 로 후보 학습만).
    # 모든 라운드 소진 후에야 수렴 게이트로 — 단발 미채택 = 즉시 종료 방지.
    if (RT.args.invent and RT.args.invent > 0
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
                  ("build", n_build), ("release", n_release), ("readme", n_readme),
                  ("chain", n_chain),
                  ("converge", n_converge), ("user_stop", n_user_stop),
                  ("gate_deploy", n_gate_deploy), ("gate_release", n_gate_release),
                  ("gate_converge", n_gate_converge),
                  ("log_run_start", log_sheet("run_start")), ("log_fe", log_sheet("fe")),
                  ("log_run_done", log_sheet("run_done")), ("log_converge", log_sheet("converge"))]:
        g.add_node(n, fn)

    # 판정 (코어 뒤)
    g.add_node("j_branch", claude_judge("new_branch", lambda s: (s.get("branch", ""), {})))
    g.add_node("j_base", claude_judge("baseline", lambda s: (s["baseline"]["out"], {"det": s["baseline"]["det"]})))
    g.add_node("j_reco", claude_judge("reco", lambda s: (", ".join(s.get("candidates", [])), {"n": len(s.get("candidates", []))})))
    g.add_node("j_fe", claude_judge("fe", lambda s: ("\n".join(s["r"].get("summary", [])), s["r"].get("fe_stats", {}))))
    g.add_node("j_build", claude_judge("build", lambda s: ("\n".join(s.get("build_summary", [])), {})))
    g.add_node("j_release", claude_judge("release", lambda s: (s["branch"], {"det": s["r"].get("det_str")})))
    g.add_node("j_chain", claude_judge("chain", lambda s: (", ".join(s.get("current_extra", [])), {})))

    # 배선
    g.add_edge(START, "new_branch")
    g.add_edge("new_branch", "log_run_start")
    g.add_edge("log_run_start", "j_branch")
    g.add_conditional_edges("j_branch", route_after_branch_j,
                            {"preprocess": "preprocess", "fe_baseline": "fe_baseline",
                             "user_stop": "user_stop", "END": END})
    g.add_conditional_edges("preprocess", route_after_preprocess,
                            {"fe_baseline": "fe_baseline", "END": END})

    g.add_edge("fe_baseline", "j_base")
    g.add_conditional_edges("j_base", _route_judge("recommend", retry_to="fe_baseline"),
                            {"recommend": "recommend", "fe_baseline": "fe_baseline", "user_stop": "user_stop"})
    g.add_edge("recommend", "j_reco")
    g.add_conditional_edges("j_reco", _route_judge("fe_train", retry_to="recommend"),
                            {"fe_train": "fe_train", "recommend": "recommend", "user_stop": "user_stop"})

    g.add_edge("fe_train", "log_fe")
    g.add_edge("log_fe", "j_fe")
    g.add_conditional_edges("j_fe", route_after_fe,
                            {"gate_deploy": "gate_deploy", "gate_converge": "gate_converge",
                             "reco_again": "reco_again", "fe_baseline": "fe_baseline",
                             "user_stop": "user_stop"})
    g.add_edge("reco_again", "recommend")

    g.add_conditional_edges("gate_deploy", route_gate_deploy,
                            {"build": "build", "fe_baseline": "fe_baseline", "user_stop": "user_stop"})
    g.add_edge("build", "j_build")
    g.add_conditional_edges("j_build", _route_judge("gate_release", retry_to="build"),
                            {"gate_release": "gate_release", "build": "build", "user_stop": "user_stop"})
    g.add_conditional_edges("gate_release", route_gate_release,
                            {"release": "release", "user_stop": "user_stop"})
    g.add_edge("release", "j_release")
    g.add_conditional_edges("j_release", _route_judge("log_run_done", retry_to="release"),
                            {"log_run_done": "log_run_done", "release": "release", "user_stop": "user_stop"})
    g.add_edge("log_run_done", "readme")
    g.add_edge("readme", "chain")
    g.add_edge("chain", "j_chain")
    g.add_conditional_edges("j_chain", route_after_chain,
                            {"new_branch": "new_branch", "chain": "chain",
                             "user_stop": "user_stop", "END": END})

    g.add_conditional_edges("gate_converge", route_gate_converge,
                            {"converge": "converge", "user_stop": "user_stop"})
    g.add_edge("converge", "log_converge")
    g.add_edge("log_converge", END)
    g.add_edge("user_stop", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def route_after_branch_j(state: PipelineState) -> str:
    """new_branch 판정 verdict 먼저(stop), 그다음 max_runs/전처리 분기."""
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
    p.add_argument("--invent", type=int, default=5, help="claude 피처 추천 N개")
    p.add_argument("--invent_rounds", type=int, default=3,
                   help="브랜치당 추천 라운드 상한 — 미채택이어도 이 횟수까지 다른 각도로 "
                        "재추천 후에야 수렴 처리 (기본 3; 라운드 비용은 baseline_cache 덕에 후보 학습만)")
    p.add_argument("--n_anom", type=int, default=None)
    p.add_argument("--overall_tol", type=float, default=1.0)
    p.add_argument("--auto_approve", action="store_true")
    p.add_argument("--max_runs", type=int, default=50)
    p.add_argument("--build_plugin", action="store_true")
    p.add_argument("--no_judge", action="store_true", help="모든 판정 끔")
    p.add_argument("--claude_model", default="sonnet",
                   help="경량 claude 모델 — 판정 verdict·지식주입/요약. 기본 'sonnet'.")
    p.add_argument("--claude_model_heavy", default="opus",
                   help="심층 claude 모델 — 피처 발명(recommend)·FE 상세분석(claude_analyze). "
                        "기본 'opus'. 같은 브랜치 세션을 모델만 바꿔 resume (맥락 유지).")
    p.add_argument("--knowledge", action=argparse.BooleanOptionalAction, default=True,
                   help="team-vault 도메인 지식(ML IDS·공격 시나리오)을 브랜치 세션에 주입. "
                        "끄려면 --no_knowledge.")
    args = p.parse_args()

    # ── 영구 파일 로깅: stdout/stderr tee + Slack 서술 로그 ──
    # 시작 시 ml/logs/run_*.log, 브랜치 진입마다 ml/logs/{branch}_*.log 로 전환(_switch_log).
    Path("ml/logs").mkdir(parents=True, exist_ok=True)
    _switch_log(Path("ml/logs") / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log")
    sys.stdout = _Tee(sys.stdout)
    sys.stderr = _Tee(sys.stderr)
    print(f"[로그] {_LOG['path']}")

    steps._AUTO_APPROVE = args.auto_approve
    if args.no_judge:
        JUDGE_ON.clear()

    cfg = steps.load_config()
    RT.bot = _sb.SlackPipelineBot(cfg["slack"]["bot_token"], cfg["slack"]["app_token"],
                                  cfg["slack"]["channel"])
    RT.bot.log_file = _LOG["path"]   # Slack 서술 로그도 같은 파일에

    RT.sheet = _sh.PipelineSheets(cfg["google_sheets"]["credentials_file"],
                                  cfg["google_sheets"]["sheet_id"])
    RT.args = args
    RT.knowledge = _load_knowledge() if args.knowledge else ""
    if RT.knowledge:
        print(f"[지식] team-vault {len(RT.knowledge):,}자 로드 — 브랜치 세션마다 주입")

    init: PipelineState = {
        "iters": 0, "first_iter": True, "reco_round": 1,
        "current_extra": steps._load_fe_initial_extra(),
        "tried_feats": [], "adopted_any": False, "terminate": False,
    }
    graph = build_graph()
    # thread_id 를 run 마다 분리 — 고정값이면 LangSmith Threads 뷰에서 모든 run 이
    # 한 스레드의 턴으로 합쳐져 새 run 이 안 보이는 것처럼 보인다.
    thread_id = f"orchestrator-{time.strftime('%Y%m%d_%H%M%S')}"
    print(f"[LangGraph] thread_id={thread_id}")
    config = {"configurable": {"thread_id": thread_id},
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
