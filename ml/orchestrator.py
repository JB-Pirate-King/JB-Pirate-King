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
import threading
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

# 판정(judge)을 LLM 으로 돌릴 노드 — **분석형만**. 남기는 건 진짜 추론이 필요한
#   baseline(약세진단)·reco(중복/유효성)·fe(채택/회귀 분석)뿐 → 판정 노드 j_base/j_reco/j_fe.
#   new_branch/release/chain 은 자명한 사실(브랜치/커밋/저장 성공)이라 LLM 도 노드도 무가치 →
#   판정 노드 제거, 직전 노드에서 조건부 엣지로 직결.
#   build 는 빈 commit_files 가 예외 없이 통과할 수 있어 결정적 검증이 필요하지만 LLM 은 불필요 →
#   라우팅 함수 route_after_build 로 직결(commit_files>0 → gate_release, else user_stop).
JUDGE_ON = {"baseline", "reco", "fe"}

# judge verdict=retry 의 단계별 재시도 상한 — 초과 시 continue 로 강등(플랩 방지).
MAX_JUDGE_RETRY = 2

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

# 채택 피처의 C++ 표현식 영속 파일 (git 추적). patch_plugin 이 import 시 병합해
# EXTRA_FEAT_CPP 에 없는 동적 피처도 C++ 자동 생성 → 플러그인 빌드 완전 자동화.
ADOPTED_CPP_PATH = "ml/config/adopted_features_cpp.py"


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
# 노드 in/out 로깅 — 모든 노드를 _logged_node 로 감싸 입력 state·출력 delta 기록
#   ① 압축 한 줄 → tee 로그(ml/logs/{branch}.log) [NODE→]/[NODE←] (Slack 서술과 섞임)
#   ② 전체 레코드 → ml/logs/nodes_{ts}.jsonl (머신 파싱용, 큰 문자열만 컷)
#   ③ in/out 한 줄만 모은 실시간 스트림 → ml/logs/nodeio_{ts}.log (tail -f 용, 노드 IO만)
# ─────────────────────────────────────────────
_NODE_LOG = {"f": None, "live": None}   # f=jsonl 핸들, live=nodeio 스트림 핸들 (run 단위 1개)


def _open_node_log(jsonl_path, live_path=None):
    _NODE_LOG["f"] = open(jsonl_path, "a", encoding="utf-8", errors="replace")
    if live_path is not None:
        _NODE_LOG["live"] = open(live_path, "a", encoding="utf-8", errors="replace")


def _node_line(line: str):
    """[NODE→]/[NODE←] 한 줄을 stdout(tee)·전용 nodeio 스트림 양쪽에 즉시 기록."""
    print(line)
    if _NODE_LOG["live"]:
        try:
            _NODE_LOG["live"].write(line + "\n")
            _NODE_LOG["live"].flush()
        except Exception:
            pass


def _io_summary(obj, maxlen: int = 300) -> str:
    """노드 in/out 압축 표현 — 긴 str/list/dict 를 잘라 한 줄 로그용으로."""
    def s(v, ml):
        if isinstance(v, str):
            v1 = v.replace("\n", "⏎")
            return v1 if len(v1) <= ml else v1[:ml] + f"…(+{len(v1)-ml})"
        if isinstance(v, dict):
            return "{" + ", ".join(f"{k}:{s(x, 80)}" for k, x in list(v.items())[:12]) + "}"
        if isinstance(v, (list, tuple)):
            head = ", ".join(s(x, 60) for x in list(v)[:8])
            more = f"…+{len(v)-8}" if len(v) > 8 else ""
            return f"[{head}{more}]"
        return repr(v)
    return s(obj, maxlen)


def _trunc(v, maxlen: int = 8000):
    """jsonl 전체기록용 — 거대 문자열(서브프로세스 출력 등)만 잘라 파일 폭주 방지."""
    if isinstance(v, str) and len(v) > maxlen:
        return v[:maxlen] + f"…(+{len(v)-maxlen}자 생략)"
    if isinstance(v, dict):
        return {k: _trunc(x, maxlen) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_trunc(x, maxlen) for x in v]
    return v


def _logged_node(name: str, fn):
    """노드 in/out 로깅 래퍼. interrupt()(게이트)는 예외로 전파되므로 감싸지 않음
    (try/except 로 삼키면 LangGraph 일시정지가 깨짐). 정상 반환만 [NODE←]/jsonl 기록."""
    def wrapped(state: PipelineState) -> dict:
        br = state.get("branch", "-")
        _node_line(f"┌─[NODE→] {name} [{br}] | in={_io_summary(state)}")
        t0 = time.time()
        out = fn(state)                        # interrupt 시 여기서 GraphInterrupt 전파 → 아래 생략
        dt = time.time() - t0
        _node_line(f"└─[NODE←] {name} [{br}] ({dt:.1f}s) | out={_io_summary(out)}")
        if _NODE_LOG["f"]:
            try:
                rec = {"ts": time.strftime("%H:%M:%S"), "branch": br, "node": name,
                       "elapsed_s": round(dt, 2),
                       "in": {k: _trunc(v) for k, v in state.items()},
                       "out": {k: _trunc(v) for k, v in (out or {}).items()}}
                _NODE_LOG["f"].write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                _NODE_LOG["f"].flush()
            except Exception as e:
                print(f"[nodelog] 기록 실패(무시): {e}")
        return out
    return wrapped


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
    obj_hist: list            # 브랜치 내 reco 라운드별 best 목적gain (추세 조기수렴용)
    retry_count: dict         # 단계별 judge retry 횟수 (MAX_JUDGE_RETRY 상한)


# ─────────────────────────────────────────────
# 판정(judge) 노드 팩토리 — claude -p 분석·판정
# ─────────────────────────────────────────────
def _claude_json(prompt: str, timeout: int = 120,
                 session: Optional[str] = None, first: bool = False,
                 model: Optional[str] = None) -> Optional[dict]:
    """claude -p --output-format json 호출 → dict. 실패 시 None.

    session 지정 시 브랜치 세션에 묶음: 첫 호출(first=True)은 --session-id 로
    세션 생성, 이후는 --resume 로 같은 대화를 이어가 맥락이 누적된다."""
    cmd = [steps.claude_exe(), "-p", prompt, "--output-format", "json"]
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


def _prev_run_log_tail(max_lines: int = 150, max_chars: int = 12000) -> str:
    """직전 동일모델 런의 nodeio 로그 tail — 이전 채택/수렴/탐지율 맥락.
    현재 런 로그가 최신이므로 [-2]가 직전 런. 첫 런이면 빈 문자열."""
    try:
        logs = sorted(Path("ml/logs").glob(f"nodeio_{RT.args.model}_*.log"))
        if len(logs) < 2:
            return ""
        lines = logs[-2].read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-max_lines:])
        return tail[-max_chars:]
    except Exception:
        return ""


def _prime_session(knowledge: str):
    """브랜치 세션을 도메인 지식 + 직전 런 로그로 시드 — 첫 호출(--session-id)로 넣어두면
    이후 판정/추천이 --resume 로 그 맥락을 알고 판정·발명한다.
    JSON 파싱 불필요(응답 무시)하므로 _claude_json 대신 직접 호출."""
    prev = _prev_run_log_tail()
    if not ((knowledge or prev) and RT.claude_sid):
        return
    prompt = (
        "You are joining an AIS anomaly-detection ML pipeline as its analyst and feature engineer. "
        "Study the project domain knowledge below (research notes on AIS spoofing, ML-based IDS design, "
        "NMEA flooding attack scenarios). Use it in every later judgment and feature invention in THIS session.\n"
        "After studying, reply with a SHORT KOREAN summary (3-4 bullet lines, no preamble) of the key points "
        "you will use: main attack types, detection approach, and 1-2 concrete feature ideas.\n\n"
        "=== PROJECT KNOWLEDGE ===\n" + knowledge
    )
    if prev:
        prompt += (
            "\n\n=== 직전 런 로그 (tail · 같은 모델 이전 실험의 노드 IO) ===\n"
            "이전 런에서 무엇을 채택/기각했고 어느 시나리오가 약했는지 파악해, 같은 실패를 반복하지 말고 "
            "다른 각도의 피처를 제안하라.\n" + prev
        )
    # 프롬프트는 stdin 으로 전달 — knowledge(26K) + 직전런 tail 이 합쳐지면 Windows 명령줄
    # 길이 한계(~32K, WinError 206)를 넘으므로 arg 가 아닌 stdin 으로 넣는다.
    cmd = [steps.claude_exe(), "-p", "--session-id", RT.claude_sid]
    model = getattr(RT.args, "claude_model", None)
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
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
        out = {"judge": {**state.get("judge", {}), stage: v}}
        # retry 예산 — 같은 단계 retry 가 MAX_JUDGE_RETRY 초과면 continue 로 강등(무한 플랩 방지)
        if verdict == "retry":
            rc = dict(state.get("retry_count", {}))
            n = rc.get(stage, 0) + 1
            if n > MAX_JUDGE_RETRY:
                RT.bot.log(f"⚠️ [{stage}] judge retry {MAX_JUDGE_RETRY}회 초과 — continue 강등", "warning")
                verdict = "continue"
            rc[stage] = n
            out["retry_count"] = rc
        sug = (v.get("suggestion") or "").strip()
        has_sug = sug and sug.lower() not in ("없음", "none", "n/a", "-", "null", "없음.")
        RT.bot.log(
            f"🤖 *[{stage}] 판정* — {v.get('assessment','')}\n"
            f"  → *{verdict}*: {v.get('reason','')}"
            + (f"\n  💡 {sug}" if has_sug else ""),
            "판정",
        )
        out["decision"] = verdict
        return out
    return node


def _route_judge(continue_to: str, retry_to: str):
    """판정 verdict → continue_to / retry_to / user_stop(stop).

    retry 는 **직전 노드 재실행** — 예전엔 일률 fe_train 으로 보내
    j_base retry 가 후보 0인 채 스캔에 진입하는 등 비논리적이었다.
    (retry 횟수 상한은 claude_judge 가 MAX_JUDGE_RETRY 로 강등 처리.)"""
    def route(state: PipelineState) -> str:
        d = state.get("decision", "continue")
        if d == "stop":
            return "user_stop"
        if d == "retry":
            return retry_to
        return continue_to
    return route


def route_after_build(state: PipelineState) -> str:
    """build 산출 결정적 검증 — claude judge 도 노드도 불필요, 라우팅 함수로 직결.
    커밋 파일 있으면 릴리즈 게이트, 없으면(부분 실패) user_stop.
    (_fe_build_and_release 는 빈 commit_files 를 예외 없이 반환할 수 있어 명시 체크가 필요.)"""
    if len(state.get("commit_files", [])) > 0:
        return "gate_release"
    RT.bot.log("⚠️ [build] 커밋 산출 파일 0개 — 빌드 부분 실패로 간주 → stop", "warning")
    return "user_stop"


# ─────────────────────────────────────────────
# Sheets 로깅 DRY 팩토리
# ─────────────────────────────────────────────
# Sheets API 는 그래프 임계경로에서 떼어 비동기 발사 — 노드는 즉시 리턴.
# 단일 gspread 클라이언트 동시호출 경쟁 방지로 lock 직렬화. 관찰용이라 best-effort.
_SHEET_LOCK = threading.Lock()


def log_sheet(kind: str):
    """kind: run_start|fe|run_done|converge. 동일 로깅 노드 복제 대신 하나로.
    Sheets 쓰기는 데몬 스레드로 발사하고 노드는 즉시 {} 반환(핫패스 비차단)."""
    def node(state: PipelineState) -> dict:
        s, r, a = RT.sheet, state.get("r", {}), RT.args
        # 스레드에서 쓸 값은 호출 시점에 스냅샷(이후 state 변화와 무관하게).
        branch = state.get("branch", "?")
        run_num = state.get("run_num")
        current_extra = state.get("current_extra")
        fe = dict(r.get("fe_stats", {}))
        # FE crash 시 r 의 값들이 None 일 수 있음(키는 존재) → `or []` 로 방어.
        # (log_fe 가 여기서 죽으면 route_after_fe 의 ret!=0 재시도 분기까지 못 감.)
        newly_adopted = list(r.get("newly_adopted") or [])
        full_extra = list(r.get("full_extra") or [])
        n_feat = r.get("n_feat")

        def _do():
            with _SHEET_LOCK:
                try:
                    if kind == "run_start":
                        s.log_run_start(branch, a.model, a.epochs, a.max_mmsi,
                                        data_file=a.data_file)
                    elif kind == "fe":
                        s.log_fe(branch, run_num, "완료",
                                 model=a.model, fe_step=len(newly_adopted),
                                 baseline_det=fe.get("baseline_det"), best_det=fe.get("det_rate"),
                                 n_features=n_feat, adopted=newly_adopted,
                                 all_features=full_extra,
                                 threshold=fe.get("threshold"))
                    elif kind == "run_done":
                        s.log_run_done(branch, a.model, success=True)
                    elif kind == "converge":
                        s.update_run_summary(notes="수렴 완료", adopted=current_extra)
                except Exception as e:
                    print(f"[Sheets:{kind}] 로깅 실패(무시): {e}")

        threading.Thread(target=_do, daemon=True, name=f"sheet-{kind}").start()
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
    # 도메인 지식 시드는 별도 노드(n_prime)로 분리 — new_branch → prime → log_run_start.
    RT.bot.log_run_start(branch, {
        "모델": RT.args.model, "epochs": RT.args.epochs, "max_mmsi": RT.args.max_mmsi,
        "데이터": RT.args.data_file, "base_dir": RT.args.base_dir,
        "베이스 피처": f"{len(steps.BASE_FEATURES)}개",
        "출발 피처": f"{len(state.get('current_extra', []))}개 (기채택)",
        "claude세션": RT.claude_sid[:8],
    })
    # reco_round·obj_hist·retry_count 는 브랜치 단위 리셋 — 각 브랜치가 invent_rounds 만큼
    # 재추천 기회를 갖고, 추세/재시도 카운트가 이전 브랜치로 누설되지 않게 한다.
    return {"run_num": run_num, "branch": branch,
            "iters": state.get("iters", 0) + 1, "reco_round": 1,
            "obj_hist": [], "retry_count": {}}


def n_prime(state: PipelineState) -> dict:
    """브랜치 claude 세션에 도메인 지식(team-vault) 1회 시드 — new_branch 직후 별도 노드.
    이후 판정/추천/분석이 --resume 로 이 지식을 안고 동작한다. --no_knowledge 면 무동작.
    상태 변경 없음(세션 시드는 RT 부수효과) → {} 반환."""
    _prime_session(RT.knowledge)
    return {}


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
        '"cpp_expr":"<C++ expression>","target_scenario":"target"}\n'
        'Column access seq[t][_B["sog"]]. BASE 12: sog,cog,heading,status,dt,dist_km,'
        "cog_hdg_diff,sog_change,cog_hdg_change,speed_consistency,lat_speed,lon_speed. "
        "Guard previous row seq[t-1] with `if t>0 else 0.0`; guard zero-division with max(x,1e-6). Pure function.\n"
        "`cpp_expr`: the SAME computation as one C++ float expression for the OpenCPN plugin "
        "(deployed inference). Available C++ vars at the current row: (float)cur.sog,(float)cur.cog,"
        "(float)cur.hdg,(float)cur.navStatus,dt,dist_km,cog_hdg_diff,sog_change,cog_hdg_change,"
        "speed_consistency,lat_speed,lon_speed; previous row: (float)prev.sog,(float)prev.cog,"
        "(float)prev.hdg,(float)prev.navStatus,prev.lat,prev.lon. Helpers: std::abs,std::max,std::min,"
        "std::sqrt,std::sin,std::cos,M_PI. Use ternary `?:` for conditionals, std::max(x,1e-6f) for "
        "zero-division, float literals (5.0f). It must MATCH lambda_src numerically. Example: "
        '`std::abs(dist_km / std::max(dt,1e-6f) - (float)cur.sog)`.'
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
        if n in entries:
            continue
        c = RT.last_cands.get(n)
        if not c:
            # lambda_src 소스는 last_cands 에만 있음(dynamic_candidates 는 exec 된 함수라 재구성 불가).
            # 여기서 못 찾으면 adopted_features.py 에 안 남아 다음 브랜치 --initial_extra 가 KeyError.
            print(f"[채택영속화] ⚠️ '{n}' 의 lambda_src 를 last_cands 에서 못 찾음 — "
                  f"adopted_features.py 미저장(다음 브랜치 KeyError 위험). last_cands keys={list(RT.last_cands)[:8]}")
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


def _persist_adopted_cpp(names: list) -> bool:
    """채택 피처의 C++ 표현식을 ADOPTED_CPP_PATH 에 병합 저장.
    patch_plugin 이 import 시 읽어 EXTRA_FEAT_CPP 에 병합 → 동적 피처 C++ 자동 생성.
    cpp_expr 가 없으면 미저장 → patch_plugin --strict 가 빌드를 막아 깨진 산출물 방지."""
    p = Path(ADOPTED_CPP_PATH)
    existing: dict = {}
    if p.exists():
        try:
            ns: dict = {}
            exec(p.read_text(encoding="utf-8"), ns)
            existing = dict(ns.get("ADOPTED_CPP", {}))
        except Exception as e:
            print(f"[CPP영속화] 기존 파싱 실패(새로 작성): {e}")
    added = False
    for n in names:
        if n in existing:
            continue
        c = RT.last_cands.get(n)
        if not c or not c.get("cpp_expr"):
            print(f"[CPP영속화] ⚠️ '{n}' cpp_expr 없음 — patch_plugin EXTRA_FEAT_CPP 수동 등록 필요"
                  " (strict 빌드가 차단함).")
            continue
        existing[n] = (c.get("desc", ""), c["cpp_expr"])
        added = True
    if not existing:
        return False
    body = ["# 채택 동적 피처 C++ 표현식 — orchestrator n_chain 기록, patch_plugin 이 병합.",
            "# {name: (desc, cpp_expr)} → patch_plugin 이 'float <name> = (<cpp_expr>);' 로 생성.",
            "ADOPTED_CPP = {"]
    for k, (d, e) in existing.items():
        body.append(f"    {json.dumps(k)}: ({json.dumps(d, ensure_ascii=False)}, "
                    f"{json.dumps(e, ensure_ascii=False)}),")
    body.append("}")
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
    # 이번 라운드 best 후보 목적gain 을 추세 히스토리에 누적(조기수렴 판단용).
    hist = list(state.get("obj_hist", []))
    bog = r.get("best_obj_gain")
    if bog is not None:
        hist.append(bog)
    return {"r": r, "first_iter": False, "obj_hist": hist}


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
    fe_json = steps.WORK_DIR / f"feat_eng_iter{state['run_num']:02d}.json"
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
    newly = state["r"].get("newly_adopted", [])
    if _persist_adopted(newly):
        commit_files.append(ADOPTED_FEATS_PATH)
    if _persist_adopted_cpp(newly):   # C++ 표현식 영속화 → patch_plugin 자동 생성
        commit_files.append(ADOPTED_CPP_PATH)
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
    상한 도달 시 빈 브랜치를 만들지 않고 종료, 아니면 다음 브랜치 사이클.
    (j_chain 판정 노드는 무조건 continue 라 제거 — 여기서 곧장 분기한다.)"""
    if state.get("iters", 0) >= RT.args.max_runs:
        RT.bot.log(f"⚠️ 안전 상한 {RT.args.max_runs} 도달 — 체이닝 종료", "warning")
        return "END"
    return "new_branch"


def route_after_preprocess(state: PipelineState) -> str:
    return "END" if state.get("terminate") else "fe_baseline"


def route_after_fe(state: PipelineState) -> str:
    """판정 verdict + 채택여부 결합 라우팅.

    종료 경로 일원화: 미채택 상황의 'stop' 은 하드 user_stop 이 아니라 converge 게이트로
    보낸다 (수렴 로깅/Sheets 를 거쳐 정상 종료). 하드 stop 은 '채택했는데 judge 가 불신'
    하는 경우(과적합 의심)로 한정 — 두 종료 결정자(j_fe verdict ↔ converge)의 중복 제거."""
    d = state.get("decision", "continue")
    r = state.get("r", {})
    if d == "retry":
        return "fe_baseline"
    if r.get("ret", 0) != 0:
        return "fe_baseline"           # 실패 → 재진단/재시도
    if r.get("newly_adopted"):
        # 채택 + judge stop = 채택 불신(winner's curse/과적합) → 하드 정지. 아니면 배포.
        return "user_stop" if d == "stop" else "gate_deploy"
    # ── 미채택 경로 ──
    if d == "stop":                    # judge 가 수렴 권고 → converge 로 일원화
        RT.bot.log("⛔ [fe] judge stop(미채택) — 수렴 게이트로", "피처개선")
        return "gate_converge"
    # 추세 조기수렴: 최근 2라운드 best 목적gain 이 모두 ≤0 이면 라운드 남아도 수렴
    # (음수 2연속이면 회복 확률 낮음 — 낭비 fe_train 라운드 컷).
    hist = state.get("obj_hist", [])
    if len(hist) >= 2 and max(hist[-2:]) <= 0:
        RT.bot.log(f"⛔ [fe] 최근 2라운드 목적gain ≤0 {hist[-2:]} — 추세 조기수렴", "피처개선")
        return "gate_converge"
    # 횟수 기준: 미채택이라도 invent_rounds 까지 다른 각도로 재추천
    # (라운드 비용은 baseline_cache 로 후보 학습만). 단발 미채택 = 즉시 종료 방지.
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
def _make_checkpointer(persist: bool):
    """persist=True → SqliteSaver(크래시 후 resume). 불가/미설치 시 MemorySaver 폴백."""
    if not persist:
        return MemorySaver()
    try:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        Path("ml/.pipeline_tmp").mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect("ml/.pipeline_tmp/checkpoints.db", check_same_thread=False)
        print("[checkpoint] SqliteSaver 활성 — ml/.pipeline_tmp/checkpoints.db")
        return SqliteSaver(conn)
    except Exception as e:
        print(f"[checkpoint] Sqlite 불가({e}) → MemorySaver 폴백")
        return MemorySaver()


def build_graph(checkpointer=None):
    g = StateGraph(PipelineState)

    # 코어
    for n, fn in [("new_branch", n_new_branch), ("prime", n_prime), ("preprocess", n_preprocess),
                  ("fe_baseline", n_fe_baseline), ("recommend", n_recommend),
                  ("reco_again", n_reco_again), ("fe_train", n_fe_train),
                  ("build", n_build), ("release", n_release), ("readme", n_readme),
                  ("chain", n_chain),
                  ("converge", n_converge), ("user_stop", n_user_stop),
                  ("gate_deploy", n_gate_deploy), ("gate_release", n_gate_release),
                  ("gate_converge", n_gate_converge),
                  ("log_run_start", log_sheet("run_start")), ("log_fe", log_sheet("fe")),
                  ("log_run_done", log_sheet("run_done")), ("log_converge", log_sheet("converge"))]:
        g.add_node(n, _logged_node(n, fn))

    # 판정 (코어 뒤)
    # 실제 claude 판정은 baseline/reco/fe 3개뿐(JUDGE_ON). 무조건 continue 였던 패스스루
    # 판정(j_branch/j_release/j_chain)은 노드를 두지 않고 직결 — 라우팅은 조건부 엣지로 직접 건다.
    g.add_node("j_base", _logged_node("j_base", claude_judge("baseline", lambda s: (s["baseline"]["out"], {"det": s["baseline"]["det"]}))))
    g.add_node("j_reco", _logged_node("j_reco", claude_judge("reco", lambda s: (", ".join(s.get("candidates", [])), {"n": len(s.get("candidates", []))}))))
    g.add_node("j_fe", _logged_node("j_fe", claude_judge("fe", lambda s: ("\n".join(s["r"].get("summary", [])), s["r"].get("fe_stats", {})))))

    # 배선
    g.add_edge(START, "new_branch")
    g.add_edge("new_branch", "prime")
    g.add_edge("prime", "log_run_start")
    # log_run_start 후 곧장 분기(첫 브랜치만 preprocess, max_runs 가드) — 판정 노드 없음.
    g.add_conditional_edges("log_run_start", route_after_branch,
                            {"preprocess": "preprocess", "fe_baseline": "fe_baseline", "END": END})
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
    # build 후 곧장 결정적 검증 분기 (commit_files>0) — 판정 노드 없음.
    g.add_conditional_edges("build", route_after_build,
                            {"gate_release": "gate_release", "user_stop": "user_stop"})
    g.add_conditional_edges("gate_release", route_gate_release,
                            {"release": "release", "user_stop": "user_stop"})
    g.add_edge("release", "log_run_done")   # 판정 없이 직결 (j_release 제거)
    g.add_edge("log_run_done", "readme")
    g.add_edge("readme", "chain")
    # 체인 후 곧장 분기(max_runs 가드 → END, 아니면 다음 브랜치) — 판정 노드 없음.
    g.add_conditional_edges("chain", route_after_chain,
                            {"new_branch": "new_branch", "END": END})

    g.add_conditional_edges("gate_converge", route_gate_converge,
                            {"converge": "converge", "user_stop": "user_stop"})
    g.add_edge("converge", "log_converge")
    g.add_edge("log_converge", END)
    g.add_edge("user_stop", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


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
    p.add_argument("--persist", action="store_true",
                   help="SqliteSaver 체크포인트(ml/.pipeline_tmp/checkpoints.db) — 게이트 대기중 "
                        "크래시/재시작해도 재학습 없이 resume. 기본 off(MemorySaver, 인프로세스).")
    args = p.parse_args()

    # ── 영구 파일 로깅: stdout/stderr tee + Slack 서술 로그 ──
    # 시작 시 ml/logs/run_*.log, 브랜치 진입마다 ml/logs/{branch}_*.log 로 전환(_switch_log).
    Path("ml/logs").mkdir(parents=True, exist_ok=True)
    _ts0 = time.strftime("%Y%m%d_%H%M%S")
    _m = args.model                                              # 파일명에 모델명 삽입(식별)
    _switch_log(Path("ml/logs") / f"run_{_m}_{_ts0}.log")
    _open_node_log(Path("ml/logs") / f"nodes_{_m}_{_ts0}.jsonl",  # 노드 in/out 전체 레코드(jsonl)
                   Path("ml/logs") / f"nodeio_{_m}_{_ts0}.log")   # in/out 한 줄 실시간 스트림
    sys.stdout = _Tee(sys.stdout)
    sys.stderr = _Tee(sys.stderr)
    print(f"[로그] {_LOG['path']}  |  노드IO: ml/logs/nodes_{_m}_{_ts0}.jsonl  |  실시간: ml/logs/nodeio_{_m}_{_ts0}.log")

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
    graph = build_graph(checkpointer=_make_checkpointer(args.persist))
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
