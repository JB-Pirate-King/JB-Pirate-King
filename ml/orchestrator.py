"""
AIS 파이프라인 오케스트레이터

전처리 → 학습 → 평가 → 피처개선
각 단계마다 Claude 분석 보고서 + Slack 승인 게이트 + Google Sheets 기록

사용법:
    python ml/orchestrator.py --model dcdetect --epochs 5 --max_mmsi 500
    python ml/orchestrator.py --model dcdetect --skip_preprocess
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
    output_lines = []
    for line in proc.stdout:
        line = line.rstrip()
        # tqdm progress bar lines are handled by progress_cb; skip printing to avoid flooding stdout
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


def _parse_train(out: str) -> list[str]:
    details = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        # 데이터 통계
        if any(k in s for k in ["고유 MMSI", "총 시퀀스", "시퀀스:", "학습:", "검증:"]):
            # tqdm 진행줄 제외 (Epoch N/M: 로 시작하는 줄)
            if re.match(r"Epoch\s+\d+/\d+:", s):
                continue
            details.append(s)
        # epoch 완료 요약줄: "Epoch N/M | train=X val=Y"
        elif re.search(r"Epoch\s+\d+/\d+\s*\|", s) and "train=" in s:
            details.append(s)
        # 최종 완료/실패 줄
        elif any(k in s for k in ["학습 완료", "✓", "✗ dcdetect", "전체 소요", "임계값:", "ONNX 저장"]):
            if "it/s" not in s:   # tqdm 줄 제외
                details.append(s)
    return details[-10:]


def _parse_eval(out: str) -> list[str]:
    details = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        if "%" in s and any(k in s for k in ["탐지", "overall", "FP", "holdout", "train"]):
            details.append(s)
        elif "|" in s and "%" in s:
            details.append(s)
        elif any(k in s for k in ["FP≈", "FP =", "탐지율"]):
            details.append(s)
    return details[-12:]


def _parse_eval_full(out: str) -> dict:
    """평가 출력 전체 파싱 → {saved/fp1/fp5/fp10: {scenarios, avg}}"""
    sections: dict = {}
    current = None
    for line in out.splitlines():
        s = line.strip()
        if "저장 임계값 기준" in s:
            current = "saved"; sections[current] = {"scenarios": {}, "avg": None}
        elif re.search(r"FP\s*[≈=]\s*1%", s):
            current = "fp1";  sections[current] = {"scenarios": {}, "avg": None}
        elif re.search(r"FP\s*[≈=]\s*5%", s):
            current = "fp5";  sections[current] = {"scenarios": {}, "avg": None}
        elif re.search(r"FP\s*[≈=]\s*10%", s):
            current = "fp10"; sections[current] = {"scenarios": {}, "avg": None}
        if not current:
            continue
        m = re.match(r"(.+?)\s{2,}(\d+\.?\d*)%\s*$", s)
        if m:
            name = m.group(1).strip()
            if name and not re.match(r"[─═▶dcdetect]", name) and \
               "오탐율" not in name and "탐지율" not in name and \
               "평균" not in name and len(name) > 1:
                sections[current]["scenarios"][name] = float(m.group(2))
        if "전체 평균" in s or "학습 평균" in s:
            m2 = re.search(r"(\d+\.?\d*)%", s)
            if m2: sections[current]["avg"] = float(m2.group(1))
    return sections


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
    """claude -p 로 단계 결과 분석 → Slack 메시지 라인 목록 반환"""
    last_lines = "\n".join(out.splitlines()[-60:])
    extra_str  = json.dumps(extra or {}, ensure_ascii=False)

    prompt = (
        f"AIS 이상탐지 ML 파이프라인 [{stage}] 단계 결과를 분석해줘.\n\n"
        f"성공 여부: {'성공' if success else '실패'} | 소요: {elapsed:.0f}초\n"
        f"추가 정보: {extra_str}\n\n"
        f"실행 출력 (마지막 60줄):\n{last_lines}\n\n"
        f"아래 3가지를 한국어로, 항목별 1~2문장씩, 전체 200자 이내로 답해:\n"
        f"1. 결과 평가: 정상인지 문제가 있는지, 핵심 수치 해석\n"
        f"2. 원인/근거: 왜 이 결과가 나왔는지 (피처, 데이터 양, 수렴 여부 등)\n"
        f"3. 다음 행동 추천: continue / retry / stop 중 하나 + 이유\n"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = ["🤖 *Claude 분석*"]
            for l in result.stdout.strip().splitlines():
                if l.strip():
                    lines.append(f"  {l.strip()}")
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
            ["python", "ml/core/preprocess.py", args.raw_dir, "--output", args.data_file]
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


def stage_train(bot, sheet, branch, args, step_info: tuple):
    cur, total, next_name = step_info
    current_extra = _load_fe_initial_extra()
    all_feats = BASE_FEATURES + current_extra
    while True:
        feat_lines = "\n".join(
            f"  • `{f}` — {FEATURE_DESCRIPTIONS.get(f, '')}" for f in all_feats
        )
        bot.log_stage_start(
            "학습",
            f"{_step_header(cur, total, '베이스학습', next_name)}\n"
            f"【베이스 학습】 {args.model} / epochs={args.epochs} / max_mmsi={args.max_mmsi}\n"
            f"사용 피처: {len(all_feats)}개 (베이스 {len(BASE_FEATURES)} + 기채택 {len(current_extra)})\n"
            f"{feat_lines}\n"
            f"목적: 기준 성능(baseline) 측정 — 이 모델은 배포용이 아님\n"
            f"배포용 모델은 피처 엔지니어링 학습 단계에서 생성됨"
        )
        t0 = time.time()
        # 에폭 진행 → Slack 알림
        last_epoch_reported = [0]
        def train_progress(line, proc=None):
            m = re.search(r"Epoch\s+(\d+)/\s*(\d+)\s*\|.*train=", line)
            if m:
                cur, total = int(m.group(1)), int(m.group(2))
                pct = int(cur / total * 100)
                milestone = (pct // 10) * 10
                if milestone > last_epoch_reported[0]:
                    last_epoch_reported[0] = milestone
                    elapsed_now = time.time() - t0
                    bot.log(
                        f"🧠 학습 진행 {milestone}% — Epoch {cur}/{total}  "
                        f"(경과 {elapsed_now:.0f}s)",
                        "학습"
                    )

        train_cmd = [
            "python", "ml/core/pipeline.py", "--train",
            "--models", args.model,
            "--epochs", str(args.epochs),
            "--max_mmsi", str(args.max_mmsi),
            "--data_file", args.data_file,
            "--base_dir", args.base_dir,
        ]
        if current_extra:
            train_cmd += ["--extra_features"] + current_extra

        ret, out = run_cmd(train_cmd, progress_cb=train_progress)
        elapsed = time.time() - t0
        details  = _parse_train(out)
        analysis = claude_analyze("학습", out, ret == 0, elapsed, {
            "model": args.model, "epochs": args.epochs, "max_mmsi": args.max_mmsi
        })

        hdr = _step_header(cur, total, "베이스학습", next_name)
        if ret != 0:
            summary = [hdr, "❌ 학습 실패", f"소요: {elapsed:.0f}s"] + details + ["─"] + analysis
            bot.log_stage_result("학습", summary, success=False)
            sheet.log_train(branch, args.model, args.epochs, args.max_mmsi,
                            "실패", elapsed_sec=elapsed)
        else:
            summary = [hdr, f"모델: {args.model}", f"소요: {elapsed:.0f}s"] + details + ["─"] + analysis
            bot.log_stage_result("학습", summary, success=True)
            sheet.log_train(branch, args.model, args.epochs, args.max_mmsi,
                            "완료", elapsed_sec=elapsed)

        decision = _wait(bot,"학습", summary)
        if decision == "approve":
            return True
        if decision == "stop":
            return False


def stage_eval(bot, sheet, branch, args, step_info: tuple):
    cur, total, next_name = step_info
    while True:
        bot.log_stage_start("평가",
            f"{_step_header(cur, total, '평가', next_name)}\n"
            f"{args.model} / FP목표=1%,5%,10%")
        t0 = time.time()

        # FP 구간별 탐지율 완성 시 중간 알림
        eval_sections_done = [0]
        def eval_progress(line, proc=None):
            s = line.strip()
            if "전체 평균" in s and "%" in s:
                eval_sections_done[0] += 1
                m = re.search(r"(\d+\.?\d*)%", s)
                avg = m.group(1) if m else "?"
                fp_label = ["저장임계값", "FP≈1%", "FP≈5%", "FP≈10%"][
                    min(eval_sections_done[0] - 1, 3)
                ]
                elapsed_now = time.time() - t0
                bot.log(
                    f"📊 평가 [{fp_label}] 전체평균 탐지율: *{avg}%*  (경과 {elapsed_now:.0f}s)",
                    "평가"
                )

        current_extra = _load_fe_initial_extra()
        eval_cmd = ["python", "ml/core/pipeline.py", "--eval",
                    "--models", args.model,
                    "--base_dir", args.base_dir,
                    "--data_file", args.data_file]
        if current_extra:
            eval_cmd += ["--extra_features"] + current_extra
        ret, out = run_cmd(eval_cmd, progress_cb=eval_progress)
        elapsed = time.time() - t0
        details  = _parse_eval(out)

        det_rate = None
        for line in out.splitlines():
            if "overall" in line.lower() or "탐지율" in line.lower():
                m = re.search(r"(\d+\.?\d*)%", line)
                if m:
                    det_rate = float(m.group(1))

        analysis = claude_analyze("평가", out, ret == 0, elapsed, {
            "model": args.model, "det_rate": det_rate
        })

        # 전체 파싱
        eval_data = _parse_eval_full(out)

        if ret != 0:
            summary = ["❌ 평가 실패", f"소요: {elapsed:.0f}s"] + details + ["─"] + analysis
            bot.log_stage_result("평가", summary, success=False)
            sheet.log_eval(branch, args.model, "실패", elapsed_sec=elapsed)
        else:
            fp1  = eval_data.get("fp1", {})
            fp5  = eval_data.get("fp5", {})
            fp10 = eval_data.get("fp10", {})

            fp1_avg  = fp1.get("avg")
            fp5_avg  = fp5.get("avg")
            fp10_avg = fp10.get("avg")

            hdr = _step_header(cur, total, "평가", next_name)
            summary = [
                hdr,
                "─── FP 기준별 탐지율 ───",
                f"FP≈1%   {fp1_avg:.1f}%"  if fp1_avg  else "FP≈1%   -",
                f"FP≈5%   {fp5_avg:.1f}%"  if fp5_avg  else "FP≈5%   -",
                f"FP≈10%  {fp10_avg:.1f}%" if fp10_avg else "FP≈10%  -",
                f"소요: {elapsed:.0f}s",
                "─",
            ] + analysis
            bot.log_stage_result("평가", summary, success=True)

            # ── 약세 시나리오 (FP≈1% 기준 50% 미만) ──
            if fp1.get("scenarios"):
                weak = {k: v for k, v in fp1["scenarios"].items() if v < 50.0}
                ok   = {k: v for k, v in fp1["scenarios"].items() if v >= 50.0}
                weak_rows = []
                for name, rate in sorted(weak.items(), key=lambda x: x[1]):
                    weak_rows.append(f"  ❌ {name:<22} {rate:>6.1f}%")
                for name, rate in sorted(ok.items(), key=lambda x: x[1]):
                    weak_rows.append(f"  ✅ {name:<22} {rate:>6.1f}%")
                if weak_rows:
                    bot.log_table("FP≈1% 전체 시나리오 탐지율", weak_rows, "📊")

            # ── FP별 요약 표 ──
            fp_rows = ["구간       전체평균"]
            fp_rows.append("─" * 22)
            for label, sec in [("FP≈1% ", fp1), ("FP≈5% ", fp5), ("FP≈10%", fp10)]:
                avg = f"{sec['avg']:.1f}%" if sec.get("avg") else "  -  "
                fp_rows.append(f"{label}    {avg:>7}")
            bot.log_table("FP 기준별 탐지율 요약", fp_rows, "📈")

            sheet.log_eval(branch, args.model, "완료",
                           det_fp1=fp1_avg, det_fp5=fp5_avg,
                           det_fp10=fp10_avg,
                           elapsed_sec=elapsed)
            if fp1.get("scenarios"):
                sheet.log_scenarios(branch, args.model, "FP1%", fp1["scenarios"])

        decision = _wait(bot,"평가", summary)
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
    model_dir  = Path(args.base_dir) / "ais_models" / args.model

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

    # ── 3. WSL 빌드 ──────────────────────────────────────────────────
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
    model_dir  = Path(args.base_dir) / "ais_models" / args.model

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
    det_str   = f"{det_rate:.1f}" if det_rate else "?"

    notes = (
        f"## 모델\n- {args.model}  (epochs={args.epochs})\n\n"
        f"## 학습 데이터\n- {args.data_file}\n\n"
        f"## 피처 ({n_feat}개)\n- 기본 12개 + 추가: {feat_list}\n\n"
        f"## 성능 (FP≈1%)\n- {args.model}: 탐지율 {det_str}%\n\n"
        f"## 브랜치\n- {branch}\n\n"
        f"🤖 Generated by orchestrator.py"
    )

    assets = (([tarball] if tarball else []) + model_files)

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
    fe_baseline_det = [None]
    fe_pending_adoption: list = []
    def fe_progress(line, proc=None):
        s = line.strip()
        if "전체 평균 탐지율" in s and "목적점수" in s:
            m = re.search(r"탐지율\s+([\d.]+)%", s)
            if m:
                fe_baseline_det[0] = float(m.group(1))
        if fe_pending_adoption and "전체평균" in s and "→" in s:
            feat_name, feat_desc = fe_pending_adoption.pop()
            m = re.search(r"([\d.]+)%\s*→\s*([\d.]+)%.*목적점수\s*([+-][\d.]+)", s)
            if m:
                before, after, obj_gain = m.group(1), m.group(2), m.group(3)
                bot.log(
                    f"✅ 채택! `{feat_name}` — {feat_desc}\n"
                    f"  FP=1% 탐지율: {before}% → *{after}%*  |  목적점수 {obj_gain}",
                    "피처개선"
                )
            else:
                bot.log(f"✅ 채택! `{feat_name}` — {feat_desc}  {s}", "피처개선")
            return
        me = re.search(r"Epoch\s+(\d+)/\s*(\d+)\s*\|.*train=", line)
        if me:
            ep, total_ep = int(me.group(1)), int(me.group(2))
            pct = int(ep / total_ep * 100)
            milestone = (pct // 25) * 25
            if milestone > 0 and ep == round(total_ep * milestone / 100):
                elapsed_now = time.time() - t0
                bot.log(
                    f"🧠 FE 학습 {milestone}% — Epoch {ep}/{total_ep}  (경과 {elapsed_now:.0f}s)",
                    "피처개선"
                )
        if s.startswith("+ ") and "학습 중" in s:
            fe_candidate_count[0] += 1
            elapsed_now = time.time() - t0
            m = re.search(r"\+\s+(\w+)\s+\(", s)
            feat_name = m.group(1) if m else "?"
            feat_desc = FEATURE_DESCRIPTIONS.get(feat_name, "")
            bot.log(
                f"🔬 후보 #{fe_candidate_count[0]} 학습 중: "
                f"`{feat_name}` ({feat_desc})  (경과 {elapsed_now:.0f}s)",
                "피처개선"
            )
        elif "✓ 채택" in s:
            m = re.search(r"✓ 채택: \[?(\w+)\]?", s)
            feat_name = m.group(1) if m else "?"
            feat_desc = FEATURE_DESCRIPTIONS.get(feat_name, "")
            fe_pending_adoption.append((feat_name, feat_desc))

    ret, out = run_cmd(
        ["python", "ml/core/feature_engineer.py",
         "--input", args.data_file,
         "--base_dir", args.base_dir,
         "--epochs", str(args.epochs),
         "--max_mmsi", str(args.max_mmsi),
         "--out_json", out_json,
         "--initial_extra"] + current_initial_extra + [
         "--export_dir", str(Path(args.base_dir) / "ais_models" / args.model),
         "--min_gain", str(args.min_gain)]
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

    analysis       = claude_analyze("피처개선", out, bool(newly_adopted), elapsed, {
        "newly_adopted": newly_adopted,
        "det_rate": det_rate, "baseline_det": baseline_det, "n_feat": n_feat
    })
    adopted_detail = fe_adopted_analysis(out, newly_adopted, det_rate, baseline_det, weak_names)
    candidates     = _parse_greedy_candidates(out)
    importance     = _parse_permutation_importance(out)

    gain_pp = (det_rate - baseline_det) if (det_rate and baseline_det) else 0.0
    result_title = (f"✅ 채택 {len(newly_adopted)}개: {', '.join(newly_adopted)}"
                    if newly_adopted else "수렴 — 신규 채택 없음")

    summary = (
        [
            result_title,
            f"FP=1% 탐지율: {baseline_det:.1f}% → {det_rate:.1f}%  ({gain_pp:+.1f}pp)" if det_rate else "탐지율: -",
            f"총 피처 수: {n_feat}개  |  소요: {elapsed:.0f}s",
            "─",
        ]
        + adopted_detail
        + ["─"]
        + analysis
    )
    bot.log_stage_result("피처개선", summary, success=True)

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
        det_str  = f"{det_rate:.1f}" if det_rate else "?"
        n_feat_s = str(n_feat) if n_feat else "?"

        commit_files = []
        if Path(out_json).exists():
            commit_files.append(out_json)
        model_dir = Path(args.base_dir) / "ais_models" / args.model
        for fname in [f"model_{args.model}.onnx",
                      f"scaler_{args.model}.json",
                      f"threshold_{args.model}.txt"]:
            p = model_dir / fname
            if p.exists():
                commit_files.append(str(p))

        scaler_path  = str(model_dir / f"scaler_{args.model}.json")
        plugin_files = stage_build_plugin(bot, args, scaler_path)
        commit_files += plugin_files

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
    fe_dir = Path(args.base_dir) / "ais_output" / "feat_eng_iter"
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
    parser.add_argument("--auto_approve",    action="store_true",
                        help="Slack 승인 대기 없이 모든 단계 자동 approve (테스트용)")
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
        cfg["google_sheets"]["sheet_id"]
    )

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

    stages = []
    if not args.skip_preprocess: stages.append("전처리")
    stages.append("피처 엔지니어링 학습")
    total_steps = len(stages)

    def make_step_info(name: str) -> tuple:
        idx = stages.index(name)
        nxt = stages[idx + 1] if idx + 1 < total_steps else "파이프라인 종료"
        return (idx + 1, total_steps, nxt)

    if not args.skip_preprocess:
        if not stage_preprocess(bot, sheet, branch, args, make_step_info("전처리")):
            bot.log("파이프라인 중단", "warning")
            git.checkout("develop")
            return

    fe_result = stage_fe(bot, sheet, branch, args, run_num,
                         make_step_info("피처 엔지니어링 학습"))

    sheet.log_run_done(branch, args.model, success=(fe_result is not None))

    if fe_result is None:
        bot.log("파이프라인 중단", "warning")
    elif fe_result:
        bot.log_stage_result(
            "파이프라인 완료",
            [f"브랜치: {branch}",
             f"채택 피처 {len(fe_result)}개: {', '.join(fe_result)}"],
            success=True
        )
    else:
        bot.log_stage_result(
            "파이프라인 완료 — 수렴",
            [f"브랜치: {branch}", "모든 후보 탐색 완료 — 추가 채택 없음"],
            success=True
        )

    git.checkout("develop")


if __name__ == "__main__":
    main()
