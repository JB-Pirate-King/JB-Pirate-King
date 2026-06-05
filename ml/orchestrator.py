"""
AIS 파이프라인 오케스트레이터 (DCdetect 피처 엔지니어링 자동화)

구조: 브랜치마다 Greedy 1피처 채택 → 채택 시 그 피처셋으로 재학습/평가 →
      배포모델 export + 플러그인 빌드 + 커밋 + 릴리즈 → fe_state 저장 →
      새 브랜치(dcdetect_001 → 002 → ...)로 자동 체이닝, 수렴(채택 없음)하면 종료.
  - 베이스라인 학습+평가는 FE 내부(feature_engineer.py)에서 수행 (별도 단계 없음).
  - 전처리는 --skip_preprocess 가 아니면 첫 브랜치에서 1회만.
  - 각 단계: 실행 → Claude 분석 → Slack 보고/승인(또는 --auto_approve) → Sheets 기록.
  - 모델 브랜치/커밋은 project(upstream) 저장소로 push.

사용법:
    python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 \
        --data_file D:/ais_data/preprocessed/ais_preprocessed_3yr.csv \
        --skip_preprocess --auto_approve
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import ml.integrations.slack_bot as _sb
import ml.integrations.sheets as _sh
import ml.integrations.git_manager as git

CONFIG_PATH = "ml/pipeline_config.json"

BASE_FEATURES = [
    "sog", "cog", "heading", "status", "dt", "dist_km",
    "cog_hdg_diff", "sog_change", "cog_hdg_change",
    "speed_consistency", "lat_speed", "lon_speed"
]

# feature_engineer.py 의 INITIAL_EXTRA 와 동기화
FE_INITIAL_EXTRA = ["accel", "heading_rate", "vec_sog_diff", "heading_change"]

# 파이프라인 작업용 임시 출력 디렉터리 (repo-local, gitignore 대상)
#   - FE 결과 JSON / 모델 export 는 여기에 쓰고 transient 하게만 사용.
#   - 영구 산출물: 지표→Google Sheets, 모델→브랜치(ais_ids_pi/data) 커밋.
#   - D 드라이브(base_dir)에는 더 이상 출력물을 남기지 않음 (입력 데이터/캐시만 D).
WORK_DIR = Path("ml/.pipeline_tmp")

# 피처 설명 (계산 방식 포함)
FEATURE_DESCRIPTIONS = {
    "sog":               "속력 — AIS 원본값 (knots)",
    "cog":               "항로각 — AIS 원본값 (0-360°)",
    "heading":           "선수방향 — AIS 원본값 (0-360°)",
    "status":            "운항상태 — AIS status 코드 (0-15)",
    "dt":                "시간차 — 이전 메시지와의 경과시간 (초)",
    "dist_km":           "이동거리 — 연속 위경도 간 Haversine 거리 (km)",
    "cog_hdg_diff":      "COG-HDG 차이 — |COG-Heading|을 [0,180]으로 정규화",
    "sog_change":        "속력변화 — |SOG(t) - SOG(t-1)|",
    "cog_hdg_change":    "방향불일치 변화율 — cog_hdg_diff의 시간 미분",
    "speed_consistency": "속력일관성 — SOG vs dist_km/dt 비율",
    "lat_speed":         "위도속도 — Δlat/dt (°/s)",
    "lon_speed":         "경도속도 — Δlon/dt (°/s)",
    # FE 추가 피처
    "accel":                 "가속도 — Δsog/dt (knots/s)",
    "heading_rate":          "선수변화율 — Δheading/dt (°/s)",
    "vec_sog_diff":          "벡터SOG차이 — 벡터분해 후 크기 차이",
    "heading_change":        "선수각변화 — |heading(t) - heading(t-1)|",
    "sog_vec_kn":            "GPS유도 속력 (노트) — lat/lon_speed로 계산한 실제 이동속력",
    "lowspeed_crab":         "저속 crab각 — cog_hdg_diff × max(0, 1-sog/3kn)",
    "cog_change":            "COG 변화량 — |COG(t) - COG(t-1)| (도)",
    "cog_move_diff":         "COG vs 실이동방향 차이 — AIS COG와 lat/lon 기반 실이동각 오차 (도)",
    "dist_speed_err":        "거리/속도 불일치 — |dist_km/dt×3600 - sog×1.852| (km/h)",
    "dist_speed_ratio":      "거리/속도 비율 — dist_km / max(sog×1.852×dt/3600, 1e-6)",
    "anchor_suspicion":      "정박의심 — 저속+Heading변화 복합 지표",
    "speed_ratio":           "상대 속도 변화율 — |sog_change| / max(sog, 0.5)",
    "anchored_excess_speed": "정박 중 초과속력 — status∈{1,5,6} × max(0, sog-1.5kn)",
}


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_cmd(cmd: list[str], progress_cb=None, interactive=False) -> tuple[int, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if interactive else None,
        text=True, encoding="utf-8", errors="replace", env=env
    )
    # 파싱은 후보표/중요도/마지막 N줄만 필요 → 상한 buffer 로 메모리 폭주 방지.
    # tqdm progress 줄은 live progress_cb 가 처리하므로 buffer 에 적재하지 않음.
    output_lines = deque(maxlen=20000)
    for line in proc.stdout:
        line = line.rstrip()
        is_tqdm = bool(re.search(r"Epoch\s+\d+/\s*\d+:\s+[\d.]+%\|", line))
        if not is_tqdm:
            print(line, flush=True)
            output_lines.append(line)
        if progress_cb:
            progress_cb(line, proc)
    proc.wait()
    return proc.returncode, "\n".join(output_lines)


# ─────────────────────────────────────────────
# 출력 파싱 헬퍼
# ─────────────────────────────────────────────

def _extract_lines(out: str, keywords: list[str], max_lines: int = 8) -> list[str]:
    hits = []
    for line in out.splitlines():
        s = line.strip()
        if s and any(k in s for k in keywords):
            hits.append(s)
    return hits[-max_lines:]


def _parse_preprocess(out: str) -> list[str]:
    return _extract_lines(out, ["MMSI", "행", "총", "입력", "출력", "완료", "처리"], max_lines=6)

def _parse_permutation_importance(out: str) -> list[tuple[str, float]]:
    """순열 중요도 파싱 → [(feat, pp), ...] 절댓값 내림차순 (더 음수 = 더 중요)"""
    scores: dict[str, float] = {}
    for line in out.splitlines():
        m = re.search(r"(\w+)\s+중요도:\s+(-?\d+\.?\d*)pp", line)
        if m:
            scores[m.group(1)] = float(m.group(2))
    return sorted(scores.items(), key=lambda x: x[1])  # 가장 음수 = 가장 중요


def _parse_greedy_candidates(out: str) -> list[tuple[str, str, float, float, float]]:
    """Greedy 후보 → [(feat, desc, det_gain_pp, obj_score, obj_gain), ...] obj_gain 내림차순"""
    candidates = []
    current_feat: str | None = None
    current_desc: str | None = None
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"\+\s+(\w+)\s+\((.+?)\)\s+→", s)
        if m:
            current_feat = m.group(1)
            current_desc = m.group(2)
        if current_feat and "전체평균" in s and "%" in s:
            m2 = re.search(
                r"전체평균\s+[\d.]+%\(([+-]?\d+\.?\d*)\)\s+점수\s+([\d.]+)\s*[▲▼─]([+-]?\d+\.?\d*)",
                s
            )
            if m2:
                candidates.append((current_feat, current_desc,
                                   float(m2.group(1)), float(m2.group(2)), float(m2.group(3))))
                current_feat = current_desc = None
    return sorted(candidates, key=lambda x: x[4], reverse=True)


def _parse_fe(out: str) -> list[str]:
    details = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        if "베이스라인" in s or ("전체 평균 탐지율" in s and "목적점수" in s):
            details.append(s)
        elif s.startswith("+ ") and "학습 중" in s:
            details.append(s)
        elif any(a in s for a in ["▲", "▼", "─"]) and "전체평균" in s:
            details.append(s)
        elif "✓ 채택" in s:
            details.append(s)
        elif ("전체평균" in s and "→" in s and "목적점수" in s):
            details.append(s)
        elif "✗ 개선 없음" in s or "이번 반복 채택" in s:
            details.append(s)
    return details[-20:]


# ─────────────────────────────────────────────
# Claude 분석 (claude CLI 호출)
# ─────────────────────────────────────────────

def claude_analyze(stage: str, out: str, success: bool, elapsed: float,
                   extra: dict = None) -> list[str]:
    """claude -p 로 단계 결과 분석 → Slack 메시지 라인 목록 반환.
    [피처개선] 단계는 매우 상세한 다중 섹션 분석, 그 외는 간결 분석."""
    extra_str = json.dumps(extra or {}, ensure_ascii=False)

    if stage == "피처개선":
        # FE 결과: 후보 평가표·시나리오·중요도가 출력 뒤쪽에 있어 충분히 길게 전달
        last_lines = "\n".join(out.splitlines()[-150:])
        prompt = (
            "당신은 AIS 선박 이상탐지(비지도 재구성 오토인코더 DCdetect) ML 파이프라인의 "
            "수석 분석가입니다. 방금 끝난 [피처 엔지니어링] 단계 결과를 **매우 상세히** 분석하세요.\n\n"
            f"성공: {'예' if success else '아니오'} | 소요: {elapsed:.0f}초\n"
            f"핵심 지표(JSON): {extra_str}\n\n"
            f"=== 실행 출력 (마지막 150줄: 베이스라인·후보별 탐지율/목적점수·채택·재학습·최종 FP1/5/10·순열중요도) ===\n"
            f"{last_lines}\n\n"
            "아래 5개 항목을 한국어로, 각 항목 3~5문장씩 **구체적 수치를 인용**하며 상세히 작성하세요 "
            "(전체 1500자 내외, 항목 번호와 제목 유지):\n"
            "1. 📊 결과 평가: 베이스라인→최종 탐지율 변화(pp), 채택 피처 수, FP=1/5/10 비교. "
            "이번 iter이 성공적인지/미미한지 판단.\n"
            "2. 🧬 채택 피처 분석: 어떤 피처가 채택됐고 목적점수가 왜 올랐는지, "
            "그 피처가 물리적으로 어떤 이상 패턴(예: 정박이동·저속crab·급가속)을 포착하는지 해석.\n"
            "3. ⚠️ 약세 시나리오 진단: 여전히 탐지율이 낮은(<50%) 시나리오와 그 원인 가설. "
            "재구성 오차 관점에서 왜 안 잡히는지.\n"
            "4. 🎯 다음 단계 전략: 다음 브랜치에서 어떤 '종류'의 파생 피처를 추가하면 약세를 줄일지 "
            "구체적으로 1~2개 제안(이름이 아니라 아이디어).\n"
            "5. ✅ 권고: continue / retry / stop 중 하나 + 명확한 근거(수치 기반).\n"
        )
        timeout = 180
    else:
        last_lines = "\n".join(out.splitlines()[-60:])
        prompt = (
            f"AIS 이상탐지 ML 파이프라인 [{stage}] 단계 결과를 분석해줘.\n\n"
            f"성공 여부: {'성공' if success else '실패'} | 소요: {elapsed:.0f}초\n"
            f"추가 정보: {extra_str}\n\n"
            f"실행 출력 (마지막 60줄):\n{last_lines}\n\n"
            f"아래 3가지를 한국어로 항목별 2~3문장씩 상세히 답해:\n"
            f"1. 결과 평가: 정상인지 문제가 있는지, 핵심 수치 해석\n"
            f"2. 원인/근거: 왜 이 결과가 나왔는지\n"
            f"3. 다음 행동 추천: continue / retry / stop 중 하나 + 이유\n"
        )
        timeout = 120

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = ["🤖 *Claude 상세 분석*"]
            for l in result.stdout.strip().splitlines():
                if l.strip():
                    lines.append(l.rstrip())
            return lines
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Claude 분석 오류] {e}")

    return ["🤖 Claude 분석 불가 (수동 확인 필요)"]


# ─────────────────────────────────────────────
# Claude 자동 피처 발명 (claude -p → 동적 후보)
# ─────────────────────────────────────────────

DYNAMIC_CAND_PATH = "ml/dynamic_candidates.py"


def _extract_python_block(text: str) -> str:
    """응답에서 ```python ... ``` 코드블록 추출 (없으면 DYNAMIC_FEATURES 줄부터)."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    i = text.find("DYNAMIC_FEATURES")
    return text[i:].strip() if i >= 0 else ""


def claude_invent_features(analysis_text: str, weak_line: str, n: int,
                           failed: list = None) -> list:
    """약세 진단 → claude -p 로 새 피처 N개 발명 → DYNAMIC_CAND_PATH 저장 → 이름 목록 반환.

    failed: 이전 라운드에서 효과 없던 피처(이름,설명) 목록 → 회피 + 다른 접근 유도.
    실패(코드 없음/exec 오류) 시 빈 목록. 생성 코드는 exec 으로 검증 후 저장."""
    fail_block = ""
    if failed:
        items = "\n".join(f"  - {nm}" for nm in failed)
        fail_block = (
            "\n=== 이전에 시도했으나 탐지율을 못 올린 피처 (회피 + 다른 접근 필수) ===\n"
            f"{items}\n"
            "위와 **같은 발상/수식 계열은 금지**. 약세를 전혀 다른 각도(예: 시퀀스 "
            "통계·분포·비율·상호작용항)에서 포착하는 새 피처를 만드세요.\n"
        )
    prompt = (
        "당신은 AIS 선박 이상탐지 DCdetect 모델의 피처 엔지니어입니다. 아래 약세 진단을 "
        f"바탕으로 현재 모델이 못 잡는 시나리오를 포착할 **새 파생 피처 {n}개**를 발명하세요.\n\n"
        f"=== 약세 시나리오 ===\n{weak_line}\n\n=== Claude 분석 ===\n{analysis_text}\n"
        f"{fail_block}\n"
        "출력 규칙 (엄수):\n"
        "- 오직 **하나의 파이썬 코드블록**만 출력. 설명 산문 금지.\n"
        "- 코드블록은 `DYNAMIC_FEATURES` 라는 dict 하나. 형식:\n"
        "    DYNAMIC_FEATURES = {\n"
        '        "feature_name": ("한 줄 설명", lambda seq, t: <식>),\n'
        "    }\n"
        "- 컬럼 접근은 `seq[t][_B[\"sog\"]]`. BASE 12: sog, cog, heading, status, dt, "
        "dist_km, cog_hdg_diff, sog_change, cog_hdg_change, speed_consistency, lat_speed, "
        "lon_speed\n"
        "- 이전 행 `seq[t-1]`, 반드시 `if t > 0 else 0.0` 가드. 0나눗셈 방지 `max(x,1e-6)`.\n"
        "- `_ang_diff(a,b)` 와 `math` 사용 가능. 순수함수(부작용 없음).\n"
        "- 기존 피처와 **수식이 실질적으로 다른** 새 신호. 주석으로 타겟 시나리오 명시.\n"
        f"- 정확히 {n}개. 이름은 영문 snake_case, 서로 달라야 함.\n"
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=240,
        )
    except Exception as e:
        print(f"[발명] claude 호출 실패: {e}")
        return []
    if result.returncode != 0 or not result.stdout.strip():
        print("[발명] claude 응답 없음")
        return []

    code = _extract_python_block(result.stdout)
    if "DYNAMIC_FEATURES" not in code:
        print("[발명] DYNAMIC_FEATURES 코드 없음")
        return []

    # exec 검증: feature_engineer 와 같은 헬퍼(_ang_diff, math, _B) 컨텍스트로 안전 확인
    import math as _math
    ns = {"math": _math, "_ang_diff": lambda a, b: abs((a - b + 180) % 360 - 180)}
    # _B 더미 (이름→인덱스) — BASE 12
    base = ["sog", "cog", "heading", "status", "dt", "dist_km", "cog_hdg_diff",
            "sog_change", "cog_hdg_change", "speed_consistency", "lat_speed", "lon_speed"]
    ns["_B"] = {k: i for i, k in enumerate(base)}
    try:
        exec(code, ns)
        feats = ns.get("DYNAMIC_FEATURES", {})
        # 더미 시퀀스로 각 lambda 실행 가능 검증
        dummy = [[0.0] * 12 for _ in range(10)]
        valid = {}
        for name, (desc, fn) in feats.items():
            try:
                float(fn(dummy, 5))
                valid[name] = (desc, fn)
            except Exception as fe:
                print(f"[발명] '{name}' 실행 실패 제외: {fe}")
        if not valid:
            print("[발명] 유효 피처 0개")
            return []
    except Exception as e:
        print(f"[발명] 코드 검증 실패: {e}")
        return []

    # 동적 후보 파일 저장 (feature_engineer 가 import; _B/_ang_diff/math 는 그쪽 globals)
    header = (
        "# 자동 생성: orchestrator claude_invent_features (claude -p)\n"
        "# feature_engineer 네임스페이스에서 exec 됨 → _B / _ang_diff / math 사용 가능\n\n"
    )
    with open(DYNAMIC_CAND_PATH, "w", encoding="utf-8") as f:
        f.write(header + code + "\n")
    print(f"[발명] {len(valid)}개 발명 → {DYNAMIC_CAND_PATH}: {list(valid)}")
    return [(name, desc) for name, (desc, fn) in valid.items()]


def fe_adopted_analysis(out: str, adopted: list[str], det_rate,
                        baseline_det, weak_names: list[str]) -> list[str]:
    """FE 채택 피처 상세 분석 (claude_analyze 보완용)"""
    desc_map = {}
    gain_map = {}
    last_feat = None
    for line in out.splitlines():
        s = line.strip()
        m = re.search(r"✓ 채택: \[(.+?)\]\s*\((.+?)\)", s)
        if m:
            last_feat = m.group(1)
            desc_map[last_feat] = m.group(2)
        if last_feat and "목적점수" in s:
            m2 = re.search(r"목적점수 \+(\d+\.?\d*)", s)
            if m2 and last_feat not in gain_map:
                gain_map[last_feat] = float(m2.group(1))
    lines = []
    if adopted:
        lines.append(f"*채택된 피처 {len(adopted)}개*")
        for feat in adopted:
            desc = desc_map.get(feat, "설명 없음")
            gain = gain_map.get(feat)
            gain_str = f"  (목적점수 +{gain:.1f})" if gain else ""
            lines.append(f"  • `{feat}` — {desc}{gain_str}")
    else:
        lines.append("이번 iter 채택 피처 없음")

    if det_rate is not None and baseline_det is not None:
        delta = det_rate - baseline_det
        sign = "▲" if delta > 0 else ("▼" if delta < 0 else "─")
        lines.append(f"탐지율: {baseline_det:.1f}% → {det_rate:.1f}%  {sign}{abs(delta):.1f}pp")

    if weak_names:
        lines.append(f"*약세 시나리오*: {', '.join(weak_names[:4])}")
        lines.append("  → 다음 iter에서 이 시나리오를 타겟하는 피처 추가 검토")

    return lines


# ─────────────────────────────────────────────
# 단계별 실행 함수
# ─────────────────────────────────────────────

_AUTO_APPROVE = False  # main()에서 --auto_approve 플래그로 설정


def _wait(bot, stage: str, summary: list[str]) -> str:
    """auto_approve 모드면 즉시 approve, 아니면 Slack 대기. 실패(❌) 시엔 stop."""
    if _AUTO_APPROVE:
        if any("❌" in s for s in summary):
            bot.log(f"🤖 [auto_approve] {stage} ❌ → stop", stage)
            return "stop"
        bot.log(f"🤖 [auto_approve] {stage} → approve", stage)
        return "approve"
    return bot.wait_approval(stage, summary)


def _step_header(cur: int, total: int, name: str, next_name: str) -> str:
    bar = "".join("■" if i <= cur else "□" for i in range(1, total + 1))
    return f"📍 [{cur}/{total}] {bar}  *{name}*  →  다음: {next_name}"


def stage_preprocess(bot, sheet, branch, args, step_info: tuple):
    cur, total, next_name = step_info
    while True:
        bot.log_stage_start("전처리",
            f"{_step_header(cur, total, '전처리', next_name)}\n{args.raw_dir}")
        t0 = time.time()
        ret, out = run_cmd(
            [sys.executable, "ml/core/preprocess.py", args.raw_dir, "--output", args.data_file]
        )
        elapsed = time.time() - t0
        details  = _parse_preprocess(out)
        analysis = claude_analyze("전처리", out, ret == 0, elapsed)

        hdr = _step_header(cur, total, "전처리", next_name)
        if ret != 0:
            summary = [hdr, "❌ 전처리 실패", f"소요: {elapsed:.0f}s"] + details + ["─"] + analysis
            bot.log_stage_result("전처리", summary, success=False)
            sheet.log(branch, "전처리", "실패", elapsed_sec=elapsed)
        else:
            summary = [hdr, f"소요: {elapsed:.0f}s"] + details + ["─"] + analysis
            bot.log_stage_result("전처리", summary, success=True)
            sheet.log(branch, "전처리", "완료", elapsed_sec=elapsed)

        decision = _wait(bot,"전처리", summary)
        if decision == "approve":
            return True
        if decision == "stop":
            return False

# ─────────────────────────────────────────────
# 플러그인 자동 빌드
# ─────────────────────────────────────────────

def _win_to_wsl(win_path: str) -> str:
    """C:\\Users\\foo\\bar → /mnt/c/Users/foo/bar"""
    p = win_path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        return f"/mnt/{drive}{p[2:]}"
    return p


def stage_build_plugin(bot, args, scaler_path: str) -> list[str]:
    """FE 채택 후 플러그인 자동 빌드.

    단계:
      1. patch_plugin.py  → C++ 코드 자동 패치 (ML_FEATURE_COUNT / PushFeature)
      2. 모델 파일        → ais_ids_pi/data/ 복사 (model.onnx / scaler.json / threshold.txt)
      3. WSL cmake+make   → ais_ids_pi/*.tar.gz 생성

    반환: git에 포함할 파일 경로 목록 (실패 단계까지 수집한 것 반환)
    """
    plugin_dir = Path("ais_ids_pi")
    data_dir   = plugin_dir / "data"
    data_dir.mkdir(exist_ok=True)
    model_dir  = WORK_DIR / "model"

    # ── 1. C++ 패치 ──────────────────────────────────────────────────
    bot.log("🔧 플러그인 C++ 코드 패치 중...", "플러그인빌드")
    ret, out = run_cmd([
        sys.executable, "ml/core/patch_plugin.py",
        "--scaler", scaler_path,
        "--root", ".",
    ])
    if ret != 0:
        bot.log(
            f"❌ patch_plugin 실패 (플러그인 빌드 생략)\n{out[-300:]}",
            "플러그인빌드",
        )
        return []
    bot.log("✅ C++ 패치 완료", "플러그인빌드")

    # ── 2. 모델 파일 복사 → ais_ids_pi/data/ ─────────────────────────
    copy_ok = True
    for src_name, dst_name in [
        (f"model_{args.model}.onnx",    "model.onnx"),
        (f"scaler_{args.model}.json",   "scaler.json"),
        (f"threshold_{args.model}.txt", "threshold.txt"),
    ]:
        src = model_dir / src_name
        dst = data_dir / dst_name
        if src.exists():
            shutil.copy2(src, dst)
        else:
            bot.log(f"⚠ 모델 파일 없음: {src}", "플러그인빌드")
            copy_ok = False

    cpp_files = [
        "ais_ids_pi/include/ais_ml.h",
        "ais_ids_pi/src/ais_ml.cpp",
        "ais_ids_pi/src/ais_ids.cpp",
    ]
    data_files = [
        str(data_dir / "model.onnx"),
        str(data_dir / "scaler.json"),
        str(data_dir / "threshold.txt"),
    ]

    if not copy_ok:
        bot.log("❌ 모델 파일 복사 실패 (WSL 빌드 생략)", "플러그인빌드")
        return [f for f in cpp_files + data_files if Path(f).exists()]
    bot.log("✅ 모델 파일 복사 완료 → ais_ids_pi/data/", "플러그인빌드")

    # ── 3. (선택) WSL 빌드 ───────────────────────────────────────────
    #   정책상 플러그인 빌드/배포는 native Linux 가 정본 (CLAUDE.md).
    #   Windows 운용 중 tar.gz 가 필요할 때만 --build_plugin 으로 WSL 빌드 시도.
    #   기본 off → C++ 패치 + 모델 파일만 커밋, tar.gz 는 native Linux 에서 빌드.
    if not getattr(args, "build_plugin", False):
        bot.log(
            "ℹ️ WSL 빌드 생략 (--build_plugin 미지정) — C++패치+모델만 커밋. "
            "tar.gz 는 native Linux 에서 `./local-build-package.sh` 로 빌드.",
            "플러그인빌드",
        )
        return [f for f in cpp_files + data_files if Path(f).exists()]

    bot.log("🔨 WSL 플러그인 빌드 시작 (cmake+make)...", "플러그인빌드")
    repo_abs   = str(Path(".").resolve())
    wsl_repo   = _win_to_wsl(repo_abs)
    wsl_script = f"{wsl_repo}/ml/build_plugin_wsl.sh"

    ret, out = run_cmd(["wsl", "-d", "Ubuntu-24.04", "bash", wsl_script, wsl_repo])
    if ret != 0:
        bot.log(
            f"❌ WSL 빌드 실패 (tar.gz 없음, C++패치+모델만 커밋)\n{out[-400:]}",
            "플러그인빌드",
        )
        return [f for f in cpp_files + data_files if Path(f).exists()]

    # 마지막 ".tar.gz" 줄 캡처 → Windows 경로 변환
    tarball_wsl = ""
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.endswith(".tar.gz"):
            tarball_wsl = line
            break

    tarball_win = None
    if tarball_wsl.startswith("/mnt/"):
        parts = tarball_wsl[5:].split("/", 1)
        if len(parts) == 2:
            drive, rest = parts[0].upper(), parts[1].replace("/", "\\")
            tarball_win = f"{drive}:\\{rest}"

    if not tarball_win or not Path(tarball_win).exists():
        found = sorted(plugin_dir.glob("*.tar.gz"))
        if found:
            tarball_win = str(found[-1])

    if tarball_win and Path(tarball_win).exists():
        # tar.gz 는 *.tar.gz gitignore 규칙 적용 → 커밋 대상 아님
        # 릴리스 시 `gh release create` 로 직접 첨부
        bot.log(
            f"✅ 플러그인 빌드 완료: {Path(tarball_win).name}\n"
            f"  📦 릴리스 시 첨부: `gh release create vX.X.X {tarball_win} ...`",
            "플러그인빌드",
        )
    else:
        bot.log("⚠ tar.gz 위치 특정 불가 (빌드 성공했을 수 있음)", "플러그인빌드")

    return [f for f in cpp_files + data_files if Path(f).exists()]


def stage_release(bot, args, branch: str, run_num: int,
                  full_extra: list, det_rate) -> None:
    """FE 채택 커밋 후 GitHub 릴리스 자동 생성.

    태그: run/{model}_{NNN} (브랜치명 기반, prerelease).
    첨부: tar.gz (존재하면) + 모델 3파일.
    """
    plugin_dir = Path("ais_ids_pi")
    model_dir  = WORK_DIR / "model"

    tag = f"run/{args.model}_{run_num:03d}"

    # commit SHA를 target으로 사용 (브랜치명은 422 오류 발생)
    sha_ret, sha_out = run_cmd(["git", "rev-parse", "HEAD"])
    commit_sha = sha_out.strip() if sha_ret == 0 else branch

    tarballs = sorted(plugin_dir.glob("*.tar.gz"))
    tarball  = str(tarballs[-1]) if tarballs else None

    model_files = []
    for fname in [f"model_{args.model}.onnx",
                  f"scaler_{args.model}.json",
                  f"threshold_{args.model}.txt"]:
        p = model_dir / fname
        if p.exists():
            model_files.append(str(p))

    n_feat    = len(BASE_FEATURES) + len(full_extra)
    feat_list = ", ".join(full_extra) if full_extra else "-"
    det_str   = f"{det_rate:.1f}" if det_rate is not None else "?"

    notes = (
        f"## 모델\n- {args.model}  (epochs={args.epochs})\n\n"
        f"## 학습 데이터\n- {args.data_file}\n\n"
        f"## 피처 ({n_feat}개)\n- 기본 12개 + 추가: {feat_list}\n\n"
        f"## 성능 (FP≈1%)\n- {args.model}: 탐지율 {det_str}%\n\n"
        f"## 브랜치\n- {branch}\n\n"
        f"## 배포 (Deployment)\n"
        f"이 릴리스의 모델 3파일은 학습 산출물 이름이라 그대로는 플러그인이 로드하지 않음.\n"
        f"**런타임 로드 위치**(`g_pData`)로 옮기고 **고정 이름으로 리네임**해야 함:\n"
        f"- 런타임 경로 (Linux): `~/.opencpn/plugins/ais_ids_pi/data/`\n"
        f"- 로드 규칙 (`ais_ids.cpp`): `ensemble_config.json` 없으면 → `model.onnx`/`scaler.json`/`threshold.txt` 단일모델 폴백\n\n"
        f"```bash\n"
        f"DEST=\"$HOME/.opencpn/plugins/ais_ids_pi/data\"\n"
        f"mkdir -p \"$DEST\"\n"
        f"cp model_{args.model}.onnx     \"$DEST/model.onnx\"\n"
        f"cp scaler_{args.model}.json    \"$DEST/scaler.json\"\n"
        f"cp threshold_{args.model}.txt  \"$DEST/threshold.txt\"\n"
        f"```\n"
        f"> 또는 native Linux 에서 `ais_ids_pi/local-build-package.sh` 실행 시 "
        f"`ais_ids_pi/data/` 전체가 위 경로로 자동 설치됨 (단, repo `data/` 에 미리 위 3파일을 넣어둘 것).\n\n"
        f"🤖 Generated by orchestrator.py"
    )

    assets = (([tarball] if tarball else []) + model_files)

    # 기존 동일 태그 릴리즈가 있으면 먼저 삭제(+태그) → 재실행 시 덮어쓰기 (충돌로 누락 방지).
    run_cmd(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"])

    cmd = ["gh", "release", "create", tag,
           "--title", f"{tag} — {args.model} iter{run_num:03d}",
           "--notes", notes,
           "--target", commit_sha,
           "--prerelease"]
    cmd += assets

    bot.log(f"📦 GitHub 릴리스 생성 중: {tag}  ({len(assets)}개 파일 첨부)", "릴리스")
    ret, out = run_cmd(cmd)
    if ret == 0:
        url_line = next((l for l in out.splitlines() if "github.com" in l), out.strip())
        bot.log(f"✅ 릴리스 완료: {url_line}", "릴리스")
    else:
        bot.log(f"⚠ 릴리스 실패 (수동 생성 필요)\n{out[-300:]}", "릴리스")


FE_STATE_FILE = "ml/fe_state.json"


def _load_fe_initial_extra() -> list[str]:
    """이전 run에서 저장된 채택 피처 로드. 없으면 기본값 반환."""
    try:
        with open(FE_STATE_FILE, encoding="utf-8") as f:
            return json.load(f).get("initial_extra", FE_INITIAL_EXTRA)
    except FileNotFoundError:
        return FE_INITIAL_EXTRA


def _save_fe_initial_extra(adopted: list[str]):
    """이번 run 채택 피처를 다음 run(다음 브랜치)을 위해 저장."""
    with open(FE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"initial_extra": adopted}, f, ensure_ascii=False, indent=2)


def _fe_run(bot, sheet, branch, args, run_num, current_initial_extra, fe_dir, step_info):
    """Greedy FE 수렴까지 전체 실행 + Slack 보고.
    반환: ('approve' | 'retry' | 'stop', full_extra | None, fe_stats)
    """
    cur, total, next_name = step_info
    fe_start_total = len(BASE_FEATURES) + len(current_initial_extra)
    init_feat_lines = "\n".join(
        f"  • `{f}` — {FEATURE_DESCRIPTIONS.get(f, '')}" for f in current_initial_extra
    ) or "  (없음 — 베이스 12개만)"
    bot.log_stage_start(
        "피처개선",
        f"{_step_header(cur, total, '피처 엔지니어링 학습', next_name)}\n"
        f"iter {run_num:03d} / epochs={args.epochs} / max_mmsi={args.max_mmsi}\n"
        f"베이스 {len(BASE_FEATURES)}개 + 기채택 {len(current_initial_extra)}개 = 출발점 {fe_start_total}개\n"
        f"기채택 피처:\n{init_feat_lines}\n"
        f"─\n"
        f"📐 목적함수 (FP=1% 기준): `전체평균 + 1.0 × 약세평균`\n"
        f"  채택 조건: 목적점수 +{args.min_gain} 이상  |  수렴 시 자동 종료"
    )
    t0 = time.time()

    out_json = str(fe_dir / f"feat_eng_iter{run_num:02d}.json")

    fe_candidate_count = [0]
    fe_total_cand      = [0]
    fe_baseline_det    = [None]
    fe_baseline_done   = [False]
    fe_final_phase     = [False]  # 채택 후 재학습/최종평가 단계
    fe_cur_cand: list  = [None]   # 현재 평가 중인 후보 (feat, desc)
    fe_pending_adoption: list = []

    def fe_progress(line, proc=None):
        s = line.strip()

        # 후보 총 개수 (헤더 "후보: 20개")
        mt = re.search(r"후보:\s*(\d+)개", s)
        if mt:
            fe_total_cand[0] = int(mt.group(1))

        # 베이스라인 결과: "→ 전체 평균 탐지율 40.6%  (목적점수 77.2)"
        if "전체 평균 탐지율" in s and "목적점수" in s:
            m = re.search(r"탐지율\s+([\d.]+)%.*목적점수\s+([\d.]+)", s)
            if m:
                fe_baseline_det[0]  = float(m.group(1))
                fe_baseline_done[0] = True
                bot.log(
                    f"📊 *베이스라인* ({fe_start_total}피처): "
                    f"FP=1% 탐지율 *{m.group(1)}%*  ·  목적점수 {m.group(2)}\n"
                    f"  이제 후보 {fe_total_cand[0] or '?'}개를 하나씩 추가해 평가합니다 "
                    f"(채택 기준: 목적점수 +{args.min_gain} 이상)",
                    "피처개선"
                )
            return

        # 약세 시나리오 목록
        mw = re.search(r"약세 시나리오\((\d+)개\):\s*(.+)", s)
        if mw:
            bot.log(f"⚠️ 약세 시나리오 {mw.group(1)}개 (베이스 탐지율<50%): {mw.group(2)[:250]}", "피처개선")
            return

        # ── 채택 후: 최적 피처셋으로 재학습 시작 ──
        mrt = re.search(r"최적 피처셋\((\d+)개\)으로 재학습", s)
        if mrt:
            fe_final_phase[0] = True
            bot.log(
                f"🔁 *채택 피처셋({mrt.group(1)}개)으로 최종 모델 재학습 시작*\n"
                f"  (이 모델이 배포본 — 순열중요도 + FP=1%/5%/10% 평가 + threshold 산출)",
                "피처개선"
            )
            return

        # 최종 재학습 검증 손실 (최종 단계만)
        if fe_final_phase[0] and "최적 검증 MSE" in s:
            m = re.search(r"최적 검증 MSE:\s*([\d.]+)", s)
            if m:
                bot.log(f"  ✅ 최종 모델 학습 완료 — 최적 검증 MSE {m.group(1)}", "피처개선")
            return

        # 최종 다중 FP 평가 시작
        if "최종 모델 다중 FP 평가" in s:
            bot.log("📊 *최종 모델 평가 중* (FP=1%/5%/10% + 시나리오별 탐지율)...", "피처개선")
            return

        # 최종 FP별 평균: "FP=1%: X%  FP=5%: Y%  FP=10%: Z%"
        mfp = re.search(r"FP=1%:\s*([\d.]+)%\s+FP=5%:\s*([\d.]+)%\s+FP=10%:\s*([\d.]+)%", s)
        if mfp:
            bot.log(
                f"📈 *최종 탐지율* — FP=1%: *{mfp.group(1)}%*  ·  "
                f"FP=5%: {mfp.group(2)}%  ·  FP=10%: {mfp.group(3)}%",
                "피처개선"
            )
            return

        # 최종 [FP≈N%] 평균 (참고)
        mfpavg = re.search(r"\[FP≈(\d+)%\]\s*평균 탐지율\s*([\d.]+)%", s)
        if mfpavg:
            bot.log(f"  · FP≈{mfpavg.group(1)}% 평균 탐지율 {mfpavg.group(2)}%", "피처개선")
            return

        # 배포 임계값
        mth = re.search(r"threshold \(FP=1% 기준\):\s*([\d.]+)", s)
        if mth:
            bot.log(f"🎯 배포 임계값(FP=1% 정상 99퍼센타일): `{mth.group(1)}`", "피처개선")
            return

        # 채택 확정 follow-up: "전체평균 X% → Y%  |  목적점수 +g"
        if fe_pending_adoption and "전체평균" in s and "→" in s:
            feat_name, feat_desc = fe_pending_adoption.pop()
            m = re.search(r"([\d.]+)%\s*→\s*([\d.]+)%.*목적점수\s*([+-][\d.]+)", s)
            if m:
                bot.log(
                    f"🏆 *채택 확정!* `{feat_name}` — {feat_desc}\n"
                    f"  FP=1% 탐지율 {m.group(1)}% → *{m.group(2)}%*  |  목적점수 {m.group(3)}",
                    "피처개선"
                )
            else:
                bot.log(f"🏆 *채택 확정!* `{feat_name}` — {feat_desc}", "피처개선")
            return

        # 후보 평가 시작: "+ turn_rate (설명) → 16개 학습 중..."
        if s.startswith("+ ") and "학습 중" in s:
            fe_candidate_count[0] += 1
            m = re.search(r"\+\s+(\w+)\s+\(", s)
            feat_name = m.group(1) if m else "?"
            feat_desc = FEATURE_DESCRIPTIONS.get(feat_name, "")
            fe_cur_cand[0] = (feat_name, feat_desc)
            tot = f"/{fe_total_cand[0]}" if fe_total_cand[0] else ""
            bot.log(
                f"🔬 후보 #{fe_candidate_count[0]}{tot} `{feat_name}` 평가 중 — {feat_desc}",
                "피처개선"
            )
            return

        # 후보 결과: "전체평균  33.6%(-6.7)  점수   62.0 ▼-11.9  [0.3분]"
        mr = re.search(
            r"전체평균\s+([\d.]+)%\(([+-][\d.]+)\)\s+점수\s+([\d.]+)\s+[▲▼─]\s*([+-]?[\d.]+)", s)
        if mr and fe_cur_cand[0]:
            det, det_g, score, obj_g = mr.group(1), mr.group(2), mr.group(3), mr.group(4)
            feat_name, _ = fe_cur_cand[0]
            try:
                passed = float(obj_g) >= args.min_gain
            except ValueError:
                passed = False
            verdict = f"✅ 기준충족(≥+{args.min_gain})" if passed else "⬜ 미달"
            bot.log(
                f"   └ `{feat_name}`: FP=1% 탐지율 {det}% ({det_g}pp)  ·  "
                f"목적점수 {score} ({obj_g})  →  {verdict}",
                "피처개선"
            )
            fe_cur_cand[0] = None
            return

        # 채택 마커
        if "✓ 채택" in s:
            m = re.search(r"✓ 채택: \[?(\w+)\]?", s)
            feat_name = m.group(1) if m else "?"
            feat_desc = FEATURE_DESCRIPTIONS.get(feat_name, "")
            fe_pending_adoption.append((feat_name, feat_desc))
            return

        # epoch 진행 — 베이스라인 또는 최종 재학습 단계만 (후보 스캔 중엔 스팸이라 생략)
        if not fe_baseline_done[0] or fe_final_phase[0]:
            me = re.search(r"Epoch\s+(\d+)/\s*(\d+)\s*\|.*train=", line)
            if me:
                ep, total_ep = int(me.group(1)), int(me.group(2))
                pct = int(ep / total_ep * 100)
                milestone = (pct // 25) * 25
                if milestone > 0 and ep == round(total_ep * milestone / 100):
                    elapsed_now = time.time() - t0
                    label = "최종 모델 학습" if fe_final_phase[0] else "베이스라인 학습"
                    mtl = re.search(r"train=([\d.]+)\s*\|?\s*val=([\d.]+)", line)
                    loss = f"  (train={mtl.group(1)} val={mtl.group(2)})" if mtl else ""
                    bot.log(
                        f"🧠 {label} {milestone}% — Epoch {ep}/{total_ep}{loss}  "
                        f"(경과 {elapsed_now:.0f}s)",
                        "피처개선"
                    )

    ret, out = run_cmd(
        [sys.executable, "ml/core/feature_engineer.py",
         "--model", args.model,
         "--input", args.data_file,
         "--base_dir", args.base_dir,
         "--epochs", str(args.epochs),
         "--max_mmsi", str(args.max_mmsi),
         "--out_json", out_json,
         "--max_steps", "1",
         "--initial_extra"] + current_initial_extra + [
         "--export_dir", str(WORK_DIR / "model"),
         "--min_gain", str(args.min_gain),
         "--overall_tol", str(args.overall_tol),
         "--n_anom", str(args.n_anom if args.n_anom else args.max_mmsi),
         "--scan_ratio", str(args.scan_ratio)]
        + (["--max_candidates", str(args.max_candidates)] if args.max_candidates else [])
        + (["--candidates"] + args.candidates if args.candidates else [])
        + (["--holdout_file", args.holdout_file] if args.holdout_file else []),
        progress_cb=fe_progress
    )
    elapsed = time.time() - t0

    if ret != 0:
        fe_details = _parse_fe(out)
        analysis = claude_analyze("피처개선", out, False, elapsed)
        summary = (["❌ 피처 엔지니어링 실패", f"소요: {elapsed:.0f}s"]
                   + fe_details + ["─"] + analysis)
        bot.log_stage_result("피처개선", summary, success=False)
        sheet.log_fe(branch, run_num, "실패", elapsed_sec=elapsed)
        decision = _wait(bot, "피처 엔지니어링 실패", summary)
        return decision, None, {}

    # 결과 파싱
    det_rate, det_fp5, det_fp10, n_feat, threshold = None, None, None, None, None
    full_extra, baseline_det, baseline_score, scenario_fp1 = [], None, None, {}
    if Path(out_json).exists():
        with open(out_json, encoding="utf-8") as f:
            result = json.load(f)
        det_rate       = result.get("best_det")
        det_fp5        = result.get("det_fp5")
        det_fp10       = result.get("det_fp10")
        threshold      = result.get("threshold")
        n_feat         = len(result.get("best_extra", [])) + len(BASE_FEATURES)
        _best = result.get("best_extra", [])
        full_extra = list(dict.fromkeys(current_initial_extra + _best))
        baseline_det   = result.get("baseline_det")
        baseline_score = result.get("baseline_score")
        scenario_fp1   = result.get("scenario_fp1", {})

    newly_adopted = [f for f in full_extra if f not in current_initial_extra]

    weak_names = []
    for line in out.splitlines():
        m = re.search(r"약세 시나리오\(.+?\): (.+)", line.strip())
        if m:
            weak_names = [s.strip() for s in m.group(1).split(",")]

    adopted_detail = fe_adopted_analysis(out, newly_adopted, det_rate, baseline_det, weak_names)
    candidates     = _parse_greedy_candidates(out)
    importance     = _parse_permutation_importance(out)

    gain_pp = ((det_rate - baseline_det)
               if (det_rate is not None and baseline_det is not None) else 0.0)
    result_title = (f"✅ 채택 {len(newly_adopted)}개: {', '.join(newly_adopted)}"
                    if newly_adopted else "수렴 — 신규 채택 없음")

    summary = (
        [
            result_title,
            (f"FP=1% 탐지율: {baseline_det:.1f}% → {det_rate:.1f}%  ({gain_pp:+.1f}pp)"
             if (det_rate is not None and baseline_det is not None) else "탐지율: -"),
            f"총 피처 수: {n_feat}개  |  소요: {elapsed:.0f}s",
        ]
        + adopted_detail
    )
    bot.log_stage_result("피처개선", summary, success=True)

    # ── Claude 분석 → 별도 메시지로 명확히 표시 ──
    bot.log("🤖 Claude가 FE 결과 분석 중... (최대 2분)", "피처개선")
    analysis = claude_analyze("피처개선", out, bool(newly_adopted), elapsed, {
        "newly_adopted": newly_adopted,
        "det_rate": det_rate, "baseline_det": baseline_det, "n_feat": n_feat
    })
    bot.log("\n".join(analysis), "피처개선")

    # Ralph 닫힌 루프용: Claude 분석 + 약세 시나리오를 파일로 저장.
    #   ralph_feature_invention.md 가 이 파일을 읽어 진단된 약세를 정조준해 새 피처 발명.
    weak_line = next((l.strip() for l in out.splitlines()
                      if "약세 시나리오(" in l), "")
    try:
        (WORK_DIR / "claude_fe_analysis.md").write_text(
            f"# Claude FE 분석 (branch {branch}, iter {run_num:03d})\n\n"
            f"채택: {', '.join(newly_adopted) or '없음'} | "
            f"탐지율 {baseline_det}→{det_rate} | 총 {n_feat}피처\n\n"
            f"## 약세 시나리오\n{weak_line}\n\n"
            f"## Claude 분석\n" + "\n".join(analysis) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[claude_fe_analysis 저장 실패] {e}")

    if candidates:
        baseline_info = ""
        if baseline_det is not None and baseline_score is not None:
            baseline_info = f"베이스라인: FP=1% 탐지율 {baseline_det:.1f}%  목적점수 {baseline_score:.1f}  → 채택기준 +{args.min_gain}"
        cand_rows = ([baseline_info] if baseline_info else []) + ["─" * 70,
                      f"{'피처':<22} {'탐지율gain(FP1%)':>16}  {'목적점수':>8}  {'목적gain':>8}  설명",
                      "─" * 70]
        for feat, desc, det_gain, obj_score, obj_gain in candidates:
            mark = "✅" if feat in newly_adopted else "  "
            arrow = "▲" if obj_gain > 0 else ("▼" if obj_gain < 0 else "─")
            cand_rows.append(
                f"{mark}{feat:<20} {det_gain:>+8.1f}pp  {obj_score:>8.1f}  {arrow}{obj_gain:>+6.1f}  {desc[:18]}"
            )
        bot.log_table("Greedy 후보 평가 결과", cand_rows, "🔬")

    if importance:
        imp_rows = [f"{'피처':<22} {'중요도':>10}", "─" * 38]
        for feat, pp in importance:
            bar = "★" * min(int(abs(pp) / 5) + 1, 5)
            desc = FEATURE_DESCRIPTIONS.get(feat, "")
            imp_rows.append(f"{feat:<22} {pp:>+8.2f}pp  {bar}")
            if desc:
                imp_rows.append(f"  └ {desc}")
        bot.log_table("피처 중요도 (순열 기반)", imp_rows, "🔑")

    sheet.log_fe(branch, run_num, "완료",
                 model=args.model, fe_step=len(newly_adopted),
                 baseline_det=baseline_det, best_det=det_rate,
                 n_features=n_feat, adopted=newly_adopted,
                 all_features=full_extra,
                 threshold=threshold, elapsed_sec=elapsed)
    if importance:
        sheet.log_importance(branch, 1, importance, FEATURE_DESCRIPTIONS)

    fe_stats = {
        "baseline_det": baseline_det, "det_rate": det_rate,
        "det_fp5": det_fp5, "det_fp10": det_fp10, "threshold": threshold,
    }
    if scenario_fp1:
        sheet.log_scenarios(branch, args.model, "FP=1%", scenario_fp1)

    if newly_adopted:
        det_str  = f"{det_rate:.1f}" if det_rate is not None else "?"
        n_feat_s = str(n_feat) if n_feat else "?"

        # ── 게이트 ①: FE 평가 결과 → 배포(빌드·커밋·릴리즈) 진행 여부 ──
        #   summary 에 Claude 분석이 포함되어 있어, 승인 버튼과 함께 표시됨.
        gate1 = _wait(
            bot,
            f"FE 평가 — `{', '.join(newly_adopted)}` 채택 (탐지율 {det_str}%) → 배포 진행?",
            summary,
        )
        if gate1 == "retry":
            return "retry", None, fe_stats
        if gate1 == "stop":
            bot.log("⏹ 채택했으나 배포 중단 (사용자 stop) — 모델 미커밋", "피처개선")
            return "stop", None, fe_stats

        # ── 배포 단계: 플러그인 빌드 ──
        #   모델/JSON은 WORK_DIR(gitignore, 임시). 영구 보존: 모델→ais_ids_pi/data 커밋.
        model_dir    = WORK_DIR / "model"
        scaler_path  = str(model_dir / f"scaler_{args.model}.json")
        commit_files = stage_build_plugin(bot, args, scaler_path)

        # ── 게이트 ②: 빌드 결과 → 커밋 + GitHub 릴리즈 진행 여부 ──
        build_summary = [
            "✅ 플러그인 빌드 완료" if commit_files else "⚠️ 빌드 산출 없음(부분 실패 가능)",
            f"커밋 대상 파일 {len(commit_files)}개 (C++ 패치 + 모델)",
            f"채택 피처셋 {n_feat_s}개 · 탐지율 {det_str}%",
        ]
        bot.log_stage_result("플러그인빌드", build_summary, success=bool(commit_files))
        gate2 = _wait(bot, "배포 — git 커밋 + GitHub 릴리즈 진행?", build_summary)
        if gate2 == "stop":
            bot.log("⏹ 빌드까지 완료, 커밋/릴리즈 중단 (사용자 stop)", "피처개선")
            return "stop", None, fe_stats

        if commit_files:
            git.commit_results(
                commit_files,
                f"feat(fe): {args.model} iter{run_num:03d} "
                f"det={det_str}% feat={n_feat_s} ({len(newly_adopted)}개 채택)",
                branch=branch
            )

        stage_release(bot, args, branch, run_num, full_extra, det_rate)
        return "approve", full_extra, fe_stats
    else:
        decision = _wait(bot, "피처 엔지니어링 — 수렴 완료 → 파이프라인 종료?", summary)
        return decision, None, fe_stats


def stage_fe(bot, sheet, branch, args, run_num, step_info: tuple):
    """FE 수렴까지 전체 실행.
    반환값:
      list  — 채택된 전체 extra 피처 목록
      []    — 수렴 (신규 채택 없음)
      None  — 사용자 중단
    """
    current_initial_extra = _load_fe_initial_extra()
    fe_dir = WORK_DIR
    fe_dir.mkdir(parents=True, exist_ok=True)

    while True:  # retry 전용 루프
        decision, new_extra, fe_stats = _fe_run(
            bot, sheet, branch, args, run_num,
            current_initial_extra=current_initial_extra,
            fe_dir=fe_dir, step_info=step_info
        )

        if decision == "stop":
            return None
        if decision == "retry":
            continue

        n_adopted = len(new_extra) - len(current_initial_extra) if new_extra else 0
        sheet.update_run_summary(
            fe_steps=n_adopted,
            fe_baseline=fe_stats.get("baseline_det"),
            fe_det=fe_stats.get("det_rate"),
            fe_det_fp5=fe_stats.get("det_fp5"),
            fe_det_fp10=fe_stats.get("det_fp10"),
            fe_n_feat=len(BASE_FEATURES) + len(new_extra) if new_extra else None,
            adopted=new_extra or current_initial_extra,
            threshold=fe_stats.get("threshold"),
            notes="완료" if new_extra else "수렴 완료"
        )
        return new_extra if new_extra is not None else []


def diagnose_baseline(bot, args) -> tuple:
    """베이스(12피처)만 학습/평가 → (analysis_lines, weak_line, base_det) 반환. 라운드 무관 1회."""
    bot.log("🧪 *자동 피처 발명* — 베이스라인 진단 (12피처)", "피처개선")
    out_json = str(WORK_DIR / "baseline_diag.json")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    _diag_done = {"phase2": False}

    def diag_progress(line, proc=None):
        """진단 단계 Slack 라이브 보고 (마일스톤만, 스팸 방지)."""
        s = line.strip()
        m = re.search(r"Epoch\s+(\d+)/\s*(\d+)\s*\|.*val=([\d.]+)", line)
        if m:
            ep, tot = int(m.group(1)), int(m.group(2))
            if ep == 1 or ep == tot or ep == max(1, tot // 2):
                label = "재학습" if _diag_done["phase2"] else "베이스 진단"
                bot.log(f"🧠 {label} 학습 Epoch {ep}/{tot} (val={m.group(3)})  "
                        f"[{(time.time()-t0)/60:.0f}분]", "피처개선")
            return
        if "전체 평균 탐지율" in s:
            m2 = re.search(r"탐지율\s+([\d.]+)%.*목적점수\s+([\d.]+)", s)
            if m2:
                bot.log(f"📊 베이스라인(12피처): *FP=1% 탐지율 {m2.group(1)}%*  ·  "
                        f"목적점수 {m2.group(2)}", "피처개선")
            else:
                bot.log(f"📊 베이스라인(12피처): {s}", "피처개선")
            return
        if "약세 시나리오(" in s:
            bot.log(f"⚠️ {s}", "피처개선"); return
        if "최적 피처셋" in s and "재학습" in s:
            _diag_done["phase2"] = True
            bot.log("🔁 진단 모델 재학습 + 순열중요도 계산...", "피처개선"); return
        if "Permutation Importance 계산" in s:
            bot.log("🔑 순열 중요도 계산 중...", "피처개선"); return
        mfp = re.search(r"FP=1%:\s*([\d.]+)%\s+FP=5%:\s*([\d.]+)%\s+FP=10%:\s*([\d.]+)%", s)
        if mfp:
            bot.log(f"📈 진단 최종 — FP1 {mfp.group(1)}% / FP5 {mfp.group(2)}% / "
                    f"FP10 {mfp.group(3)}%", "피처개선"); return

    ret, out = run_cmd(
        [sys.executable, "ml/core/feature_engineer.py",
         "--model", args.model,
         "--input", args.data_file,
         "--base_dir", args.base_dir,
         "--epochs", str(args.epochs),
         "--max_mmsi", str(args.max_mmsi),
         "--n_anom", str(args.n_anom if args.n_anom else args.max_mmsi),
         # 누적: 현 채택셋(fe_state)을 시작점으로 진단 → 이미 채택된 피처가 가린 약세 말고
         #   '아직 남은' 약세를 정조준해 발명 (순수 베이스 아님). fe_state 비면 베이스 12.
         "--initial_extra"] + _load_fe_initial_extra() + [
         "--candidates",                       # 빈값 = 후보 0 (진단만)
         "--diagnose_only",                    # 약세 진단까지만 (재학습/순열중요도 생략)
         "--out_json", out_json]
        + (["--holdout_file", args.holdout_file] if args.holdout_file else []),
        progress_cb=diag_progress,
    )
    elapsed = time.time() - t0
    if ret != 0:
        bot.log("❌ 베이스라인 진단 실패 — 발명 생략", "warning")
        return []

    weak_line = next((l.strip() for l in out.splitlines() if "약세 시나리오(" in l), "")
    base_det = None
    m = re.search(r"전체 평균 탐지율\s+([\d.]+)%", out)
    if m:
        base_det = float(m.group(1))
    bot.log(f"📊 베이스라인 진단: 탐지율 {base_det}%\n{weak_line}", "피처개선")

    analysis = claude_analyze("피처개선", out, True, elapsed,
                              {"baseline_det": base_det, "phase": "invent_diagnosis"})
    bot.log("🤖 *발명 근거 분석*\n" + "\n".join(analysis), "피처개선")
    return analysis, weak_line, base_det


def stage_invent(bot, args, analysis: list, weak_line: str,
                 failed: list, round_n: int) -> list:
    """진단(analysis/weak_line) 기반 claude -p 피처 발명 (라운드별). 발명 이름 목록 반환.
    failed: 이전 라운드 실패 피처 이름 → 회피. round_n: 현재 라운드(로그)."""
    bot.log(f"🧬 *발명 라운드 {round_n}* — claude 가 약세 정조준 {args.invent}개 생성"
            + (f" (이전 실패 {len(failed)}개 회피)" if failed else ""), "피처개선")
    pairs = claude_invent_features("\n".join(analysis), weak_line, args.invent, failed=failed)
    if pairs:
        lines = [f"🧬 *발명 완료* {len(pairs)}개 (계산식):"]
        for nm, ds in pairs:
            lines.append(f"  • `{nm}` — {ds}")
        bot.log("\n".join(lines), "피처개선")
    else:
        bot.log("⚠️ 발명 실패", "warning")
    return [nm for nm, _ in pairs]


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",           default="dcdetect")
    parser.add_argument("--epochs",          type=int, default=5)
    parser.add_argument("--max_mmsi",        type=int, default=500)
    parser.add_argument("--base_dir",        default="D:/")
    parser.add_argument("--raw_dir",         default="D:/ais_data/raw/2025")
    parser.add_argument("--data_file",       default="D:/ais_data/preprocessed/2025/ais_preprocessed_2025.csv")
    parser.add_argument("--skip_preprocess", action="store_true")
    parser.add_argument("--holdout_file",    default=None,
                        help="FP=1%% 측정용 별도 전처리 파일 (학습 데이터와 완전 분리)")
    parser.add_argument("--min_gain",        type=float, default=3.0,
                        help="FE 채택 임계 목적점수 향상량 (기본: 3.0, 테스트 시 0.1)")
    parser.add_argument("--max_candidates",  type=int, default=None,
                        help="Greedy 후보 수 제한 (앞 N개만 탐색, 속도용)")
    parser.add_argument("--scan_ratio",      type=float, default=1.0,
                        help="후보 스캔 학습 표본 비율 (예 0.4, 채택본은 풀 재학습). 1.0=풀")
    parser.add_argument("--candidates",      nargs="*", default=None,
                        help="탐색 후보 피처 명시 (Ralph/큐레이션 추천 10개 등). 미지정=전체 20개")
    parser.add_argument("--invent",          type=int, default=0,
                        help="첫 브랜치 전 베이스라인 진단 → claude -p 로 약세 정조준 피처 N개 "
                             "자동 발명 후 그 N개를 후보로 사용 (0=비활성)")
    parser.add_argument("--invent_rounds",   type=int, default=1,
                        help="발명 라운드 상한. 채택 0이면 실패 피드백으로 재발명 (기본 1)")
    parser.add_argument("--n_anom",          type=int, default=None,
                        help="시나리오당 이상 시퀀스 수. 미지정 시 max_mmsi 와 동일 (노이즈↓)")
    parser.add_argument("--overall_tol",     type=float, default=1.0,
                        help="채택 회귀 가드: 전체 FP1 이 이 값(pp) 넘게 하락하면 채택 거부")
    parser.add_argument("--auto_approve",    action="store_true",
                        help="Slack 승인 대기 없이 모든 단계 자동 approve (테스트용)")
    parser.add_argument("--max_runs",        type=int, default=50,
                        help="브랜치 체이닝 안전 상한 (수렴 전 무한 반복 방지, 기본 50)")
    parser.add_argument("--build_plugin",    action="store_true",
                        help="WSL 로 플러그인 tar.gz 빌드 시도 (기본 off — 정본 빌드는 native Linux)")
    args = parser.parse_args()

    global _AUTO_APPROVE
    _AUTO_APPROVE = args.auto_approve

    cfg = load_config()

    bot   = _sb.SlackPipelineBot(
        cfg["slack"]["bot_token"],
        cfg["slack"]["app_token"],
        cfg["slack"]["channel"]
    )
    sheet = _sh.PipelineSheets(
        cfg["google_sheets"]["credentials_file"],
        cfg["google_sheets"]["sheet_id"],
    )

    # FE-only 체이닝: 브랜치마다 Greedy 1피처 채택 → fe_state 갱신 → 새 브랜치
    #   (dcdetect_001→002→...) → 수렴(채택 없음)하면 종료.
    #   베이스라인 학습+평가는 FE 내부에서 수행. 전처리는 첫 브랜치에서만(--skip_preprocess 아니면).
    # 자동 피처 발명 (다라운드): 베이스 진단(1회) → 발명 → FE → 채택 0 이면 실패 피드백으로
    #   재발명(invent_rounds 까지). 채택되면 그 피처셋으로 정상 체이닝.
    invent = {"on": bool(args.invent and args.invent > 0),
              "analysis": None, "weak": "", "failed": [], "round": 0,
              "adopted_any": False}
    if invent["on"]:
        invent["analysis"], invent["weak"], _ = diagnose_baseline(bot, args)
        invent["round"] = 1
        invented = stage_invent(bot, args, invent["analysis"], invent["weak"],
                                invent["failed"], invent["round"])
        if invented:
            args.candidates = invented
        else:
            invent["on"] = False   # 발명 실패 → 발명 모드 끔

    first_iter = True
    iters = 0
    try:
        while iters < args.max_runs:
            iters += 1
            run_num = git.get_next_run_num(args.model)
            branch  = git.create_branch(args.model, run_num)

            sheet.log_run_start(branch, args.model, args.epochs, args.max_mmsi,
                                data_file=args.data_file)
            bot.log_run_start(branch, {
                "모델": args.model,
                "epochs": args.epochs,
                "max_mmsi": args.max_mmsi,
                "데이터": args.data_file,
                "base_dir": args.base_dir,
                "베이스 피처": f"{len(BASE_FEATURES)}개",
                "출발 피처": f"{len(_load_fe_initial_extra())}개 (기채택)",
            })

            run_preprocess = first_iter and not args.skip_preprocess
            stages = (["전처리"] if run_preprocess else []) + ["피처 엔지니어링 학습"]
            total_steps = len(stages)

            def make_step_info(name: str) -> tuple:
                idx = stages.index(name)
                nxt = stages[idx + 1] if idx + 1 < total_steps else "파이프라인 종료"
                return (idx + 1, total_steps, nxt)

            if run_preprocess:
                if not stage_preprocess(bot, sheet, branch, args, make_step_info("전처리")):
                    bot.log("파이프라인 중단", "warning")
                    return
            first_iter = False

            fe_result = stage_fe(bot, sheet, branch, args, run_num,
                                 make_step_info("피처 엔지니어링 학습"))

            sheet.log_run_done(branch, args.model, success=(fe_result is not None))

            if fe_result is None:
                # 사용자 중단
                bot.log("파이프라인 중단", "warning")
                return
            elif fe_result:
                # 채택 발생 → 발명모드 충족 표시(이후 수렴은 진짜 종료, 재발명 안 함).
                #   재발명은 '한 번도 채택 안 됐을 때'만 → 채택된 발명피처가 dynamic 에서
                #   사라지는 KeyError 방지.
                invent["adopted_any"] = True
                # fe_state 저장 + 이 브랜치에 커밋(체이닝이 git 히스토리로 누적되도록)
                #   → 다음 브랜치는 이 run 브랜치를 base 로 분기 (create_branch 기본 동작).
                _save_fe_initial_extra(fe_result)
                git.commit_results(
                    [FE_STATE_FILE],
                    f"chore(fe): {branch} fe_state 갱신 ({len(fe_result)}피처 누적)",
                    branch=branch,
                )
                next_run = git.get_next_run_num(args.model)
                bot.log(
                    f"🔁 채택 완료 ({', '.join(fe_result)}) → "
                    f"{args.model}_{next_run:03d} 브랜치로 자동 재시작 (base={branch})",
                    "피처개선"
                )
                continue
            else:
                # 수렴 — 채택 없음. 발명 모드 + **아직 한 번도 채택 안 됨** + 라운드 남음 →
                #   실패 피드백으로 재발명 후 재시도. (한 번이라도 채택됐으면 진짜 수렴 종료.)
                if (invent["on"] and not invent["adopted_any"]
                        and invent["round"] < args.invent_rounds):
                    invent["failed"] += (args.candidates or [])
                    invent["round"] += 1
                    bot.log(f"🔁 채택 0 → *재발명 라운드 {invent['round']}* "
                            f"(실패 {len(invent['failed'])}개 회피)", "피처개선")
                    re_inv = stage_invent(bot, args, invent["analysis"], invent["weak"],
                                          invent["failed"], invent["round"])
                    if re_inv:
                        args.candidates = re_inv
                        # 재발명은 같은 run 번호 유지 → 실패 브랜치 삭제 후 재생성.
                        #   (채택돼야 번호 증가; 실패 라운드는 번호 안 먹음)
                        git.delete_branch(branch)
                        iters -= 1   # 이 run 은 재시도라 카운트 미반영
                        continue
                # 재발명 불가/실패 또는 비발명 모드 → 종료
                note = (f"발명 {invent['round']}라운드 시도, 채택 없음"
                        if invent["on"] else "모든 후보 탐색 완료 — 추가 채택 없음")
                bot.log_stage_result(
                    "파이프라인 완료 — 수렴",
                    [f"브랜치: {branch}", note],
                    success=True
                )
                return
        else:
            # max_runs 도달 (수렴 전 안전 상한)
            bot.log(f"⚠️ 안전 상한 도달: {args.max_runs} run 실행 후 종료 (--max_runs 로 조정)",
                    "warning")
    finally:
        # 크래시/정상/중단 어느 경로든 작업 브랜치를 develop 으로 복구
        try:
            git.checkout("develop")
        except Exception as e:
            print(f"[develop 복구 실패] {e}")


if __name__ == "__main__":
    main()
