"""
AIS 파이프라인 — 공용 실행 함수 라이브러리 (steps).

오케스트레이션 제어흐름(그래프/노드/게이트)은 `ml/orchestrator.py`(LangGraph)에 있고,
이 모듈은 그 노드들이 호출하는 **무거운 실행 함수 + 통합 + 파서**를 제공한다:
  - run_cmd / 출력 파서(_parse_*) / claude_analyze
  - stage_preprocess / stage_build_plugin / stage_release
  - _fe_train_eval (greedy 1스텝 학습+평가+파싱+로깅)
  - _fe_build_and_release / _fe_commit_release
  - fe_state 로드·저장, 상수(BASE_FEATURES, FEATURE_DESCRIPTIONS, WORK_DIR ...)

> 직접 실행 진입점 아님. `python -m ml.orchestrator` 로 그래프를 돌린다.
"""
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

import ml.integrations.git_manager as git

CONFIG_PATH = "ml/config/pipeline_config.json"

from ml.core.constants import BASE_FEATURES   # 단일 출처 (ml/core/constants.py)

# 브랜치 claude 세션 id — orchestrator.n_new_branch 가 매 브랜치마다 세팅.
# claude_analyze 가 이 값으로 --resume 해서 하네스/추천/지식주입과 같은 세션에 누적된다.
_CLAUDE_SID = None

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
                   extra: dict = None, model: str = None) -> list[str]:
    """claude -p 로 단계 결과 분석 → Slack 메시지 라인 목록 반환.
    [피처개선] 단계는 매우 상세한 다중 섹션 분석, 그 외는 간결 분석."""
    extra_str = json.dumps(extra or {}, ensure_ascii=False)

    if stage == "피처개선":
        # FE 결과: 후보 평가표·시나리오·중요도가 출력 뒤쪽에 있어 충분히 길게 전달
        last_lines = "\n".join(out.splitlines()[-150:])
        prompt = (
            "You are the lead analyst of an AIS ship anomaly-detection ML pipeline "
            "(unsupervised reconstruction autoencoder, DCdetect). Analyze the just-finished "
            "[feature engineering] stage result in **great detail**.\n\n"
            f"Success: {'yes' if success else 'no'} | Elapsed: {elapsed:.0f}s\n"
            f"Key metrics (JSON): {extra_str}\n\n"
            "=== Run output (last 150 lines: baseline, per-candidate detection/objective, "
            "adoption, retrain, final FP1/5/10, permutation importance) ===\n"
            f"{last_lines}\n\n"
            "Write the answer in KOREAN (shown to the operator in Slack), each item 3-5 sentences "
            "**citing concrete numbers**, ~1500 chars total, keeping the item numbers and titles:\n"
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
            f"Analyze the [{stage}] stage result of the AIS anomaly-detection ML pipeline.\n\n"
            f"Success: {'success' if success else 'failure'} | Elapsed: {elapsed:.0f}s\n"
            f"Extra info: {extra_str}\n\n"
            f"Run output (last 60 lines):\n{last_lines}\n\n"
            "Answer in KOREAN (shown to the operator in Slack), 2-3 sentences per item:\n"
            "1. 결과 평가: 정상인지 문제가 있는지, 핵심 수치 해석\n"
            "2. 원인/근거: 왜 이 결과가 나왔는지\n"
            "3. 다음 행동 추천: continue / retry / stop 중 하나 + 이유\n"
        )
        timeout = 120

    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    if _CLAUDE_SID:
        cmd += ["--resume", _CLAUDE_SID]   # 브랜치 세션 이어감 (지식+앞 하네스 맥락 상속)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
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
        analysis = claude_analyze("전처리", out, ret == 0, elapsed,
                                  model=getattr(args, "claude_model", None))

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

    # 릴리즈 산출물 영구 보관 — WORK_DIR(.pipeline_tmp)은 휘발이므로 ml/deploy/{branch}/ 에 복사
    # 후 run 브랜치에 커밋 (git 추적 아카이브). tar.gz 는 용량상 커밋 제외, 복사만.
    deploy_dir = Path("ml/deploy") / branch
    deploy_dir.mkdir(parents=True, exist_ok=True)
    deploy_files = []
    for src in model_files:
        dst = deploy_dir / Path(src).name
        shutil.copy2(src, dst)
        deploy_files.append(str(dst))
    if tarball:
        shutil.copy2(tarball, deploy_dir / Path(tarball).name)
    if deploy_files:
        git.commit_results(deploy_files,
                           f"chore(deploy): {branch} 릴리즈 산출물 보관 ({len(deploy_files)}개)",
                           branch=branch)
        bot.log(f"📦 릴리즈 산출물 보관+커밋: `{deploy_dir}` ({len(deploy_files)}개 모델 파일"
                + (" + tar.gz(커밋 제외)" if tarball else "") + ")", "릴리스")

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


FE_STATE_FILE = "ml/config/fe_state.json"


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


def _fe_train_eval(bot, sheet, branch, args, run_num, current_initial_extra, fe_dir, step_info):
    """Greedy FE 1스텝: 학습 + 평가 + 파싱 + Slack/Sheets 로깅 (게이트/빌드/릴리즈 제외).

    반환 dict:
      ret           — feature_engineer 반환코드 (0=성공)
      summary       — Slack 요약 라인 목록 (게이트에 그대로 전달)
      fe_stats      — {baseline_det, det_rate, det_fp5, det_fp10, threshold}
      newly_adopted — 이번 스텝 신규 채택 피처 (없으면 [])
      full_extra    — 누적 extra 피처 전체 (성공 시) / None
      det_rate,det_str,n_feat,n_feat_s,scaler_path — 빌드/릴리즈/게이트 메시지용

    게이트(승인)는 호출측(_fe_run 래퍼 또는 LangGraph 게이트 노드)에서 수행한다.
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
            bot.log_metrics("최종 탐지율 (FP 레벨별)", {
                "FP = 1%  (배포 기준)": f"{mfp.group(1)}%",
                "FP = 5%": f"{mfp.group(2)}%",
                "FP = 10%": f"{mfp.group(3)}%",
            }, emoji="📈")
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
        analysis = claude_analyze("피처개선", out, False, elapsed,
                                  model=getattr(args, "claude_model", None))
        summary = (["❌ 피처 엔지니어링 실패", f"소요: {elapsed:.0f}s"]
                   + fe_details + ["─"] + analysis)
        bot.log_stage_result("피처개선", summary, success=False)
        sheet.log_fe(branch, run_num, "실패", elapsed_sec=elapsed)
        return {"ret": ret, "summary": summary, "fe_stats": {},
                "newly_adopted": [], "full_extra": None,
                "det_rate": None, "det_str": "?", "n_feat": None,
                "n_feat_s": "?", "scaler_path": None}

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
    }, model=getattr(args, "claude_model", None))
    bot.log("\n".join(analysis), "피처개선")

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

    det_str  = f"{det_rate:.1f}" if det_rate is not None else "?"
    n_feat_s = str(n_feat) if n_feat else "?"
    scaler_path = str(WORK_DIR / "model" / f"scaler_{args.model}.json")
    return {
        "ret": 0, "summary": summary, "fe_stats": fe_stats,
        "newly_adopted": newly_adopted, "full_extra": full_extra,
        "det_rate": det_rate, "det_str": det_str,
        "n_feat": n_feat, "n_feat_s": n_feat_s, "scaler_path": scaler_path,
    }


def _fe_build_and_release(bot, sheet, branch, args, run_num, r: dict) -> bool:
    """게이트① 통과 후: 플러그인 빌드 → (게이트② 호출측) → 커밋 + 릴리즈.

    이 함수는 빌드만 수행하고 build_summary 를 bot 에 로깅한다. 커밋/릴리즈는
    게이트②를 거쳐야 하므로 _fe_run 래퍼/LangGraph 노드에서 분리 호출한다.
    반환: commit_files (빌드 산출 파일 목록).
    """
    commit_files = stage_build_plugin(bot, args, r["scaler_path"])
    build_summary = [
        "✅ 플러그인 빌드 완료" if commit_files else "⚠️ 빌드 산출 없음(부분 실패 가능)",
        f"커밋 대상 파일 {len(commit_files)}개 (C++ 패치 + 모델)",
        f"채택 피처셋 {r['n_feat_s']}개 · 탐지율 {r['det_str']}%",
    ]
    bot.log_stage_result("플러그인빌드", build_summary, success=bool(commit_files))
    return commit_files, build_summary


def _fe_commit_release(bot, sheet, branch, args, run_num, r: dict, commit_files: list):
    """게이트② 통과 후: git 커밋 + GitHub 릴리즈."""
    if commit_files:
        git.commit_results(
            commit_files,
            f"feat(fe): {args.model} iter{run_num:03d} "
            f"det={r['det_str']}% feat={r['n_feat_s']} ({len(r['newly_adopted'])}개 채택)",
            branch=branch,
        )
    stage_release(bot, args, branch, run_num, r["full_extra"], r["det_rate"])

