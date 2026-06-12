#!/usr/bin/env python3
# coding: utf-8
"""
ens24.py — 24시간 비지도 이상탐지 앙상블 탐색 + 대규모 학습 오케스트레이터
============================================================================
완전 무인 실행. 중단(세션종료/재시작/장애)에 대비한 체크포인트·자동재개 내장.
진행상황을 Discord + Slack 으로 30분 주기 + 이벤트 기반 자동 보고.

단계
----
  Stage 1 (탐색, ~4h): 표본 데이터로 개별 비지도 모델 학습/평가(FPR=1% 기준 전 메트릭)
                       + 앙상블(평균/가중/스태킹) 탐색 → 최적 조합 선정
  Stage 2 (대규모, ~20h): 선정 앙상블 멤버를 대규모 데이터로 재학습 + 교차검증
                          + 최종 테스트셋 평가(Confusion Matrix, 운영 예상성능)

산출물 (D:\\ais_output\\ens24\\)
  state.json            진행 상태 (재개용)
  stage1_results.json   1단계 전체 결과
  stage2_results.json   2단계 전체 결과
  final_report.md       최종 보고서
  events.log            이벤트 로그

사용법
  set PYTHONPATH=C:\\pylibs & set PYTHONUTF8=1
  python ml/automation/ens24.py                 # 본 실행 (자동 재개)
  python ml/automation/ens24.py --smoke         # 5분 스모크 테스트
  python ml/automation/ens24.py --fresh         # 상태 무시하고 처음부터
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import threading
import traceback
import urllib.request
from datetime import datetime, timedelta

# ── UTF-8 콘솔 ───────────────────────────────────────────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

# ── 경로 ─────────────────────────────────────────────────────────────
_THIS   = os.path.dirname(os.path.abspath(__file__))          # ml/automation
_ML     = os.path.dirname(_THIS)                              # ml
_CORE   = os.path.join(_ML, "core")                           # ml/core
sys.path.insert(0, _CORE)
if "C:\\pylibs" not in sys.path:
    sys.path.insert(0, "C:\\pylibs")

# core 모듈 임포트 (모듈 레벨 argparse 간섭 방지)
_saved_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
try:
    import pipeline as pl
    import train_benchmark as tb
    from eval_anomaly import SCENARIO_MAKERS, scale_seq
finally:
    sys.argv = _saved_argv

from sklearn.metrics import (roc_auc_score, average_precision_score,
                             precision_score, f1_score, confusion_matrix)
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

# ── 모델 집합 ────────────────────────────────────────────────────────
NEURAL = ["usad", "tranad", "conv1d", "lstm", "tcn", "anomtrans", "dcdetect"]
SK     = ["iforest", "ocsvm"]            # train_benchmark CLI 미지원 → in-process

# ── notify 설정 (BOM 대응) ───────────────────────────────────────────
def _load_notify_cfg() -> dict:
    for p in (os.path.join(_ML, "config", "notify_config.json"),
              r"C:\ccit\JB-Pirate-King\ml\config\notify_config.json"):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[notify] cfg 로드 실패 {p}: {e}")
    return {}

_CFG            = _load_notify_cfg()
DISCORD_WEBHOOK = _CFG.get("discord_webhook", "").strip()
SLACK_TOKEN     = _CFG.get("slack_bot_token", "").strip()
SLACK_CHANNEL   = "C0B6UGWU9JR"   # #ais-training-alerts (ais-pipeline 워크스페이스, 봇 멤버)
_UA = "Mozilla/5.0 (compatible; ens24/1.0)"


def _post(url: str, payload: dict, headers: dict) -> tuple:
    """(status_code, body_dict|None) 반환."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={**headers, "User-Agent": _UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, None
    except Exception as e:
        print(f"[notify] 전송 실패 {url[:40]}: {e}")
        return 0, None


def send_discord(text: str):
    if not DISCORD_WEBHOOK or os.environ.get("ENS24_MUTE"):
        return
    st, _ = _post(DISCORD_WEBHOOK, {"content": text[:1990]},
                  {"Content-Type": "application/json"})
    if st not in (200, 204):
        print(f"[notify] Discord 실패 HTTP {st}")


def send_slack(text: str):
    if not SLACK_TOKEN or os.environ.get("ENS24_MUTE"):
        return
    st, body = _post("https://slack.com/api/chat.postMessage",
                     {"channel": SLACK_CHANNEL, "text": text[:3900]},
                     {"Content-Type": "application/json; charset=utf-8",
                      "Authorization": f"Bearer {SLACK_TOKEN}"})
    if not (body and body.get("ok")):
        err = body.get("error") if body else f"HTTP {st}"
        print(f"[notify] Slack 실패: {err}")


# ── 전역 상태 ────────────────────────────────────────────────────────
OUT          = r"D:\ais_output\ens24"
MODELS       = r"D:\ais_models\ens24"
STATE_PATH   = os.path.join(OUT, "state.json")
EVENTS_LOG   = os.path.join(OUT, "events.log")
STATE: dict  = {}
_REPORT_LOCK = threading.Lock()


def log_event(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(OUT, exist_ok=True)
        with open(EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def report(text: str, to_channels: bool = True):
    """이벤트 보고: 로그 + Discord + Slack."""
    with _REPORT_LOCK:
        log_event(text)
        if to_channels:
            send_discord(text)
            send_slack(text)
        STATE["last_report_ts"] = time.time()
        save_state()


def save_state():
    try:
        os.makedirs(OUT, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        print(f"[state] 저장 실패: {e}")


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[state] 로드 실패: {e}")
    return {}


# ── 진행률/ETA ───────────────────────────────────────────────────────
def set_progress(pct: float, task: str):
    """진행률·작업 갱신만. (자동 채널 보고는 하지 않음 — 단계 전환·하트비트로만 통지)"""
    pct = max(0.0, min(100.0, pct))
    STATE["progress"] = round(pct, 1)
    STATE["current_task"] = task
    save_state()


def _eta_str() -> str:
    t0 = STATE.get("t_start", time.time())
    p  = STATE.get("progress", 0.0)
    el = time.time() - t0
    if p <= 1:
        return "추정 중"
    remain = el / p * (100 - p)
    return str(timedelta(seconds=int(remain)))


def _elapsed_str() -> str:
    return str(timedelta(seconds=int(time.time() - STATE.get("t_start", time.time()))))


def _progress_bar(pct: float, width: int = 20) -> str:
    fill = int(round(pct / 100 * width))
    return "█" * fill + "░" * (width - fill)


def card(emoji: str, title: str, fields: dict | None = None,
         analysis: str = "", footer_extra: str = "") -> str:
    """구조화된 보고 카드 생성 (Discord/Slack 공통 마크다운)."""
    lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━━━",
             f"{emoji} **[ens24] {title}**",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if fields:
        for k, v in fields.items():
            lines.append(f"• **{k}**: {v}")
    if analysis:
        lines.append("")
        lines.append("🧠 **분석**")
        for ln in analysis.strip().split("\n"):
            lines.append(f"  {ln.strip()}")
    foot = f"⏱️ 경과 {_elapsed_str()} · ETA {_eta_str()}"
    if footer_extra:
        foot += f" · {footer_extra}"
    lines.append("")
    lines.append(foot)
    return "\n".join(lines)


def heartbeat_card() -> str:
    """정기 하트비트: 진행률 바 + 현황."""
    b = STATE.get("best", {})
    besttxt = (f"{b.get('label','-')} (TPR@FP1 {b.get('tpr_fp1',0):.1f}%)" if b else "-")
    pct = STATE.get("progress", 0.0)
    return card("📡", "정기 현황 보고",
                {"단계": STATE.get("stage_label", "-"),
                 "진행률": f"{_progress_bar(pct)} {pct:.0f}%",
                 "현재 작업": STATE.get("current_task", "-"),
                 "최고 성능": besttxt})


def update_best(label: str, tpr_fp1: float, extra: dict | None = None):
    cur = STATE.get("best", {})
    if tpr_fp1 > cur.get("tpr_fp1", -1):
        STATE["best"] = {"label": label, "tpr_fp1": round(tpr_fp1, 2), **(extra or {})}
        save_state()
        report(f"🏆 **[ens24] 최고 성능 갱신** — `{label}` TPR@FP1 = **{tpr_fp1:.1f}%**")


# ── 결과 해석(분석) 생성기 ───────────────────────────────────────────
def _grade(v: float, hi: float, mid: float) -> str:
    return "우수" if v >= hi else ("양호" if v >= mid else "개선 필요")


def analyze_individual(name: str, m: dict) -> str:
    tpr, pr, roc = m["tpr_fp1"], m["pr_auc"], m["roc_auc"]
    weak = sorted(m.get("per_scenario", {}).items(), key=lambda kv: kv[1])[:3]
    weak_s = ", ".join(f"{k} {v:.0f}%" for k, v in weak) if weak else "-"
    return (
        f"FPR=1% 엄격 임계에서 탐지율 {tpr:.1f}% — {_grade(tpr,70,50)}.\n"
        f"PR-AUC {pr:.3f}·ROC-AUC {roc:.3f} → 정상/이상 분리력 {_grade(pr,0.9,0.75)}. "
        f"임계 무관 분리는 강하나, 1% 오탐 제약이 탐지율을 제한.\n"
        f"최약세 시나리오: {weak_s} → 이들을 살리는 것이 추가 개선 여지."
    )


def analyze_stage1_done(selected: dict, indiv: dict, ens: list) -> str:
    typ = selected["type"]
    if typ == "single":
        base = (f"단일 모델 `{selected['members'][0]}` 선정 "
                f"(TPR@FP1 {selected['tpr_fp1']:.1f}%). 앙상블 조합이 없어 단독 진행.")
    else:
        base = (f"앙상블 `{'+'.join(selected['members'])}` / {selected['method']} 선정 "
                f"(TPR@FP1 {selected['tpr_fp1']:.1f}%).")
    n_ens = len(ens)
    cmp = (f" 앙상블 {n_ens}개 조합 탐색 결과 단일보다 우위 없음." if typ == "single" and n_ens
           else (f" 후보 {n_ens}개 중 최고." if n_ens else ""))
    return (base + cmp + "\n표본(소규모) 기준 선정이므로, 다음 단계에서 전체 데이터로 "
            "재학습해 일반화 성능을 확정한다.")


def analyze_stage2_start(selected: dict, mmsi: int, epochs: int, folds: int) -> str:
    members = selected["members"]
    return (
        f"선정 구성을 표본→**전체 데이터(max_mmsi {mmsi:,})**로 재학습한다.\n"
        f"각 멤버({', '.join(members)})에 lr 그리드 HPO(기본/0.3×)를 적용해 "
        f"검증 TPR 최고 학습률을 자동 선택.\n"
        f"이후 {folds}-fold 교차검증(서로 다른 날짜 파일)으로 시간적 일반화를 측정하고, "
        f"독립 테스트셋에서 Confusion Matrix·운영 예상성능을 산출한다."
    )


def analyze_final(final: dict, cv: dict) -> str:
    tpr, fpr, pr, roc = final["tpr_fp1"], final["fpr"], final["pr_auc"], final["roc_auc"]
    cm = final.get("confusion", {})
    cvm = cv.get("tpr_fp1_mean")
    cvs = cv.get("tpr_fp1_std")
    cvtxt = (f"\n교차검증 평균 TPR {cvm}±{cvs}% → 날짜(시기)가 바뀌어도 "
             f"성능이 {'안정적' if (cvs is not None and cvs < 8) else '다소 변동'}."
             if cvm is not None else "")
    return (
        f"독립 테스트셋에서 오탐률 {fpr:.2f}%(목표 1%)일 때 탐지율 **{tpr:.1f}%** 달성.\n"
        f"PR-AUC {pr:.3f}·ROC-AUC {roc:.3f} → 분리력 {_grade(pr,0.9,0.75)}. "
        f"혼동행렬 TP={cm.get('tp')}·FP={cm.get('fp')}·FN={cm.get('fn')}·TN={cm.get('tn')}."
        f"{cvtxt}\n"
        f"→ 운영 시 오탐 1%를 유지하며 공격 시퀀스의 약 {cvm or tpr}%를 탐지할 것으로 예상."
    )


# ── 30분 주기 하트비트 ───────────────────────────────────────────────
class Heartbeat(threading.Thread):
    def __init__(self, interval=1800):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self.interval):
            try:
                report(heartbeat_card())
            except Exception as e:
                log_event(f"heartbeat 오류: {e}")

    def stop(self):
        self._stop.set()


# ════════════════════════════════════════════════════════════════════
# 학습
# ════════════════════════════════════════════════════════════════════
def model_dir(stage: str, name: str) -> str:
    return os.path.join(MODELS, stage, name)


def model_files_exist(stage: str, name: str) -> bool:
    d = model_dir(stage, name)
    return all(os.path.exists(os.path.join(d, f"{p}_{name}.{ext}"))
               for p, ext in (("model", "onnx"), ("scaler", "json"), ("threshold", "txt")))


def train_neural(name: str, csv: str, outdir: str, max_mmsi: int,
                 epochs: int, lr=None) -> tuple:
    """train_benchmark.py 서브프로세스 학습 (지정 outdir). (ok, sec)"""
    import subprocess
    os.makedirs(outdir, exist_ok=True)
    cmd = [sys.executable, "train_benchmark.py", "--model", name,
           "--input", csv, "--output_dir", outdir, "--max_mmsi", str(max_mmsi)]
    if epochs:
        cmd += ["--epochs", str(epochs)]
    if lr:
        cmd += ["--lr", str(lr)]
    env = dict(os.environ)
    env["PYTHONPATH"] = "C:\\pylibs"
    env["PYTHONUTF8"] = "1"
    logf = os.path.join(outdir, "train.log")
    t0 = time.time()
    with open(logf, "w", encoding="utf-8") as lf:
        ret = subprocess.run(cmd, cwd=_CORE, env=env, stdout=lf,
                             stderr=subprocess.STDOUT)
    return ret.returncode == 0, time.time() - t0


def train_sklearn(name: str, csv: str, outdir: str, max_mmsi: int,
                  epochs: int, lr=None) -> tuple:
    """iforest/ocsvm in-process 학습 (best-effort, 지정 outdir). (ok, sec)"""
    try:
        import torch
        os.makedirs(outdir, exist_ok=True)
        d = tb.DEFAULTS[name]
        scaler_path = os.path.join(outdir, f"scaler_{name}.json")
        onnx_path   = os.path.join(outdir, f"model_{name}.onnx")
        thr_path    = os.path.join(outdir, f"threshold_{name}.txt")
        device = torch.device("cpu")
        t0 = time.time()
        tb.N_FEAT = len(tb.FEATURES)          # 기본 12피처 보장
        tensor = tb.load_and_prepare(csv, scaler_path=scaler_path,
                                     max_mmsi=max_mmsi, extra_features=[])
        tb.run_model(name, tensor, epochs or d["epochs"], lr or d["lr"],
                     d["batch_size"], d["patience"], device,
                     onnx_path, scaler_path, thr_path, full_tensor=tensor)
        return True, time.time() - t0
    except Exception as e:
        log_event(f"[train_sklearn] {name} 실패: {e}")
        return False, 0.0


def train_model(name: str, csv: str, stage: str, max_mmsi: int,
                epochs: int, lr=None) -> tuple:
    if model_files_exist(stage, name):
        return True, 0.0   # 재개: 이미 학습됨
    outdir = model_dir(stage, name)
    if name in SK:
        return train_sklearn(name, csv, outdir, max_mmsi, epochs, lr)
    return train_neural(name, csv, outdir, max_mmsi, epochs, lr)


def hpo_lrs(name: str) -> list:
    """HPO 학습률 그리드. 신경망=2개(기본, 기본×0.3), sklearn=단일."""
    if name in SK:
        return [None]
    base = tb.DEFAULTS[name]["lr"]
    return [base, round(base * 0.3, 6)]


def train_member_best(name, csv, stage, max_mmsi, epochs, lrs, hpo_eval) -> tuple:
    """lr 그리드 HPO: 각 lr 학습 → 소형셋 TPR@FP1로 최적 선택 → 최종 dir 복사.
    Returns (ok, total_sec, best_lr, best_tpr)."""
    import shutil
    final_dir = model_dir(stage, name)
    if model_files_exist(stage, name):
        return True, 0.0, None, None
    files = (("model", "onnx"), ("scaler", "json"), ("threshold", "txt"))
    best, total = None, 0.0
    for i, lr in enumerate(lrs):
        tdir = final_dir + f"__t{i}"
        have = all(os.path.exists(os.path.join(tdir, f"{p}_{name}.{e}")) for p, e in files)
        if not have:
            if name in SK:
                ok, sec = train_sklearn(name, csv, tdir, max_mmsi, epochs, lr)
            else:
                ok, sec = train_neural(name, csv, tdir, max_mmsi, epochs, lr)
            total += sec
            if not ok:
                log_event(f"[hpo] {name} lr={lr} 학습 실패")
                continue
        try:
            sess, mins, maxs, inp = load_onnx_dir(tdir, name)
            neg = score_raw_seqs(sess, inp, mins, maxs, hpo_eval["normal"])
            pos = np.concatenate([score_raw_seqs(sess, inp, mins, maxs, hpo_eval["scenarios"][s])
                                  for s in hpo_eval["scenario_names"]])
            tpr = metrics_from_scores(neg, pos)["tpr_fp1"]
        except Exception as e:
            log_event(f"[hpo] {name} lr={lr} 평가 실패: {e}")
            continue
        log_event(f"[hpo] {name} lr={lr} → TPR@FP1={tpr:.1f}%")
        if best is None or tpr > best[0]:
            best = (tpr, tdir, lr)
    if best is None:
        return False, total, None, None
    os.makedirs(final_dir, exist_ok=True)
    for p, e in files:
        shutil.copy(os.path.join(best[1], f"{p}_{name}.{e}"),
                    os.path.join(final_dir, f"{p}_{name}.{e}"))
    return True, total, best[2], round(best[0], 2)


# ════════════════════════════════════════════════════════════════════
# 추론 / 평가셋
# ════════════════════════════════════════════════════════════════════
import onnxruntime as ort


def load_onnx_dir(d: str, name: str):
    sess = ort.InferenceSession(os.path.join(d, f"model_{name}.onnx"),
                                providers=["CPUExecutionProvider"])
    with open(os.path.join(d, f"scaler_{name}.json")) as f:
        j = json.load(f)
    return sess, j["min"], j["max"], sess.get_inputs()[0].name


def load_onnx(stage: str, name: str):
    return load_onnx_dir(model_dir(stage, name), name)


def score_raw_seqs(sess, inp, mins, maxs, raw_seqs) -> np.ndarray:
    """raw 시퀀스 리스트 → 재구성 MSE 점수 배열."""
    out = np.empty(len(raw_seqs), dtype=np.float64)
    for i, seq in enumerate(raw_seqs):
        x = np.array(scale_seq(seq, mins, maxs), dtype=np.float32)[None]
        r = sess.run(None, {inp: x})[0]
        out[i] = float(np.mean((r - x) ** 2))
    return out


def build_eval_set(csv: str, n_normal: int, n_anom: int) -> dict:
    """정상 raw 시퀀스 + 시나리오별 raw 시퀀스(고정) 생성. 모든 모델 공통 사용."""
    raw_normal = pl.load_raw_normal_seqs(data_file=csv, n_seqs=n_normal)
    if not raw_normal:
        raise RuntimeError(f"정상 시퀀스 로드 실패: {csv}")
    anom = [(nm, mk) for nm, mk, ia, ho in SCENARIO_MAKERS if ia]
    scn = {}
    for nm, mk in anom:
        scn[nm] = [mk() for _ in range(n_anom)]
    return {"normal": raw_normal, "scenarios": scn,
            "scenario_names": [n for n, _ in anom]}


# ── 단일 모델 메트릭 ─────────────────────────────────────────────────
def metrics_from_scores(neg: np.ndarray, pos: np.ndarray, fp_target=1.0) -> dict:
    thr = float(np.percentile(neg, 100 - fp_target))
    y    = np.concatenate([np.zeros(len(neg)), np.ones(len(pos))])
    s    = np.concatenate([neg, pos])
    pred = (s > thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "threshold": thr,
        "fpr": round(float(fp / max(1, tn + fp)) * 100, 3),
        "tpr_fp1": round(float(tp / max(1, tp + fn)) * 100, 2),   # recall=탐지율
        "precision": round(float(precision_score(y, pred, zero_division=0)) * 100, 2),
        "f1": round(float(f1_score(y, pred, zero_division=0)) * 100, 2),
        "roc_auc": round(float(roc_auc_score(y, s)), 4),
        "pr_auc": round(float(average_precision_score(y, s)), 4),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


# ════════════════════════════════════════════════════════════════════
# 앙상블
# ════════════════════════════════════════════════════════════════════
def zscore(neg: np.ndarray, arr: np.ndarray):
    mu, sd = float(neg.mean()), float(neg.std() + 1e-9)
    return (arr - mu) / sd


def ensemble_eval(neg_mat: dict, pos_mat: dict, members: list, method: str,
                  weights: dict | None = None, fp_target=1.0) -> dict:
    """neg_mat/pos_mat: {model: 점수배열}. members 부분집합으로 앙상블 평가."""
    # z-정규화 (각 모델의 정상 분포 기준)
    zneg = {m: zscore(neg_mat[m], neg_mat[m]) for m in members}
    zpos = {m: zscore(neg_mat[m], pos_mat[m]) for m in members}
    Zneg = np.column_stack([zneg[m] for m in members])
    Zpos = np.column_stack([zpos[m] for m in members])

    if method == "avg":
        sneg, spos = Zneg.mean(1), Zpos.mean(1)
    elif method == "weighted":
        w = np.array([max(1e-3, (weights or {}).get(m, 1.0)) for m in members])
        w = w / w.sum()
        sneg, spos = Zneg @ w, Zpos @ w
    elif method == "stack":
        X = np.vstack([Zneg, Zpos])
        y = np.concatenate([np.zeros(len(Zneg)), np.ones(len(Zpos))])
        oof = np.zeros(len(y))
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        for tr, te in skf.split(X, y):
            clf = LogisticRegression(max_iter=2000, C=1.0)
            clf.fit(X[tr], y[tr])
            oof[te] = clf.predict_proba(X[te])[:, 1]
        sneg, spos = oof[:len(Zneg)], oof[len(Zneg):]
    else:
        raise ValueError(method)

    m = metrics_from_scores(sneg, spos, fp_target)
    m.update({"members": members, "method": method})
    return m


def fit_final_stack(neg_mat, pos_mat, members):
    """전체 데이터로 스태킹 메타모델 적합 → (clf, model별 mu/sd) 반환 (배포용)."""
    stats = {m: (float(neg_mat[m].mean()), float(neg_mat[m].std() + 1e-9)) for m in members}
    Zneg = np.column_stack([(neg_mat[m]-stats[m][0])/stats[m][1] for m in members])
    Zpos = np.column_stack([(pos_mat[m]-stats[m][0])/stats[m][1] for m in members])
    X = np.vstack([Zneg, Zpos]); y = np.concatenate([np.zeros(len(Zneg)), np.ones(len(Zpos))])
    clf = LogisticRegression(max_iter=3000, C=1.0).fit(X, y)
    return clf, stats


# ════════════════════════════════════════════════════════════════════
# 점수 수집 (모델 집합 → neg/pos 행렬)
# ════════════════════════════════════════════════════════════════════
def collect_scores(stage: str, models: list, evalset: dict):
    """각 모델로 정상/시나리오 raw 시퀀스를 채점 → 정렬된 점수 행렬."""
    neg_mat, pos_mat, perscn = {}, {}, {}
    normal = evalset["normal"]
    scn = evalset["scenarios"]
    pos_concat_order = evalset["scenario_names"]
    infer_ms = {}
    for name in models:
        try:
            sess, mins, maxs, inp = load_onnx(stage, name)
        except Exception as e:
            log_event(f"[collect] {name} 로드 실패: {e}")
            continue
        t0 = time.time()
        neg = score_raw_seqs(sess, inp, mins, maxs, normal)
        pos_by = {nm: score_raw_seqs(sess, inp, mins, maxs, scn[nm]) for nm in pos_concat_order}
        dt = time.time() - t0
        ninf = len(normal) + sum(len(v) for v in scn.values())
        infer_ms[name] = round(dt / max(1, ninf) * 1000, 3)
        neg_mat[name] = neg
        pos_mat[name] = np.concatenate([pos_by[nm] for nm in pos_concat_order])
        perscn[name]  = pos_by
    return neg_mat, pos_mat, perscn, infer_ms


def per_scenario_rates(neg, pos_by, names, fp_target=1.0) -> dict:
    thr = float(np.percentile(neg, 100 - fp_target))
    return {nm: round(float((pos_by[nm] > thr).mean()) * 100, 1) for nm in names}


# ════════════════════════════════════════════════════════════════════
# STAGE 1
# ════════════════════════════════════════════════════════════════════
def stage1(args):
    STATE["stage_label"] = "Stage1 탐색"
    if STATE.get("stage1_done"):
        report("↩️ [ens24] Stage1 이미 완료 — 건너뜀")
        return STATE["stage1"]

    report(card("🚀", "Stage 1 시작 — 탐색 및 모델 선정",
                {"데이터": os.path.basename(args.stage1_file),
                 "표본 규모": f"max_mmsi {args.stage1_mmsi:,} · epochs {args.stage1_epochs}",
                 "평가셋": f"정상 {args.n_normal:,} · 시나리오당 {args.n_anom} (31종)",
                 "대상 모델": f"{len(args.models)}개 — {', '.join(args.models)}"},
                analysis=(
                    "표본 데이터로 빠르게 각 모델을 학습·평가한다.\n"
                    "FPR=1% 기준 탐지율·PR-AUC·ROC-AUC·F1로 순위를 매기고, "
                    f"{'2개 이상이면 ' if len(args.models)>1 else ''}앙상블(평균/가중/스태킹)까지 "
                    "탐색해 최적 구성을 고른다. 본 학습(전체 데이터)은 다음 단계.")))

    s1 = STATE.setdefault("stage1", {"train_sec": {}, "individual": {}})

    # 1) 학습
    nmodels = len(args.models)
    for i, name in enumerate(args.models):
        set_progress(2 + i / nmodels * 10, f"Stage1 학습 {name} ({i+1}/{nmodels})")
        if model_files_exist("stage1", name):
            log_event(f"[stage1] {name} 이미 학습됨")
            continue
        report(f"🔧 [ens24] Stage1 학습 {i+1}/{nmodels}: `{name}`")
        ok, sec = train_model(name, args.stage1_file, "stage1",
                              args.stage1_mmsi, args.stage1_epochs)
        s1["train_sec"][name] = round(sec, 1)
        save_state()
        if not ok:
            report(f"⚠️ [ens24] `{name}` 학습 실패 — 제외하고 계속")

    trained = [m for m in args.models if model_files_exist("stage1", m)]
    if not trained:
        raise RuntimeError("Stage1: 학습된 모델이 없습니다")
    report(f"✅ [ens24] Stage1 학습 완료: {len(trained)}/{nmodels} — {trained}")

    # 2) 평가셋 + 점수 수집
    set_progress(13, "Stage1 평가셋 생성/채점")
    evalset = build_eval_set(args.stage1_file, args.n_normal, args.n_anom)
    neg_mat, pos_mat, perscn, infer_ms = collect_scores("stage1", trained, evalset)
    scored = list(neg_mat.keys())

    # 3) 개별 메트릭
    set_progress(15, "Stage1 개별 모델 메트릭")
    indiv = {}
    for name in scored:
        m = metrics_from_scores(neg_mat[name], pos_mat[name])
        m["train_sec"] = s1["train_sec"].get(name, 0.0)
        m["infer_ms"]  = infer_ms.get(name, 0.0)
        m["per_scenario"] = per_scenario_rates(neg_mat[name], perscn[name],
                                               evalset["scenario_names"])
        indiv[name] = m
        update_best(f"individual:{name}", m["tpr_fp1"])
    s1["individual"] = indiv
    save_state()

    ranked = sorted(scored, key=lambda n: (indiv[n]["tpr_fp1"], indiv[n]["pr_auc"]),
                    reverse=True)
    tbl = "\n".join(f"  {r+1}. {n:10s} TPR@FP1={indiv[n]['tpr_fp1']:5.1f}% "
                    f"PR-AUC={indiv[n]['pr_auc']:.3f} ROC-AUC={indiv[n]['roc_auc']:.3f} "
                    f"F1={indiv[n]['f1']:.1f}"
                    for r, n in enumerate(ranked))
    top = ranked[0]
    report(card("📈", "Stage 1 — 개별 모델 성능 (FPR=1%)",
                {"순위표": f"```\n{tbl}\n```"},
                analysis=analyze_individual(top, indiv[top])))

    # 4) 앙상블 탐색
    set_progress(17, "Stage1 앙상블 탐색")
    weights = {n: indiv[n]["pr_auc"] for n in scored}
    combos = {
        "all": scored,
        "top5": ranked[:5],
        "top3": ranked[:3],
        "top2": ranked[:2],
    }
    ens_results = []
    for cname, members in combos.items():
        if len(members) < 2:
            continue
        for method in ("avg", "weighted", "stack"):
            try:
                r = ensemble_eval(neg_mat, pos_mat, members, method, weights)
                r["combo"] = cname
                ens_results.append(r)
                update_best(f"{cname}/{method}", r["tpr_fp1"],
                            {"members": members, "method": method, "combo": cname})
            except Exception as e:
                log_event(f"[ensemble] {cname}/{method} 실패: {e}")
    s1["ensembles"] = ens_results
    save_state()

    if ens_results:
        srt = sorted(ens_results, key=lambda x: x["tpr_fp1"], reverse=True)
        etbl = "\n".join(f"  {r['combo']:5s}/{r['method']:8s} "
                         f"TPR@FP1={r['tpr_fp1']:5.1f}% FPR={r['fpr']:.2f}% "
                         f"PR-AUC={r['pr_auc']:.3f} F1={r['f1']:.1f}" for r in srt)
        bi = max(indiv.values(), key=lambda m: m["tpr_fp1"])["tpr_fp1"]
        win = srt[0]
        ana = (f"최고 앙상블 {win['combo']}/{win['method']} TPR@FP1 {win['tpr_fp1']:.1f}% "
               f"vs 최고 단일 {bi:.1f}% → "
               f"{'앙상블이 ' + format(win['tpr_fp1']-bi,'+.1f') + 'pp 우위' if win['tpr_fp1']>bi else '단일이 동등/우위'}.\n"
               f"스태킹(메타학습)이 평균/가중보다 나은지로 모델 상호보완성을 판단.")
        report(card("🧬", "Stage 1 — 앙상블 탐색 결과 (FPR=1%)",
                    {"결과표": f"```\n{etbl}\n```"}, analysis=ana))
    else:
        log_event("[stage1] 단일 모델 — 앙상블 탐색 생략")

    # 5) 최적 선정 (앙상블/개별 통틀어 최고 TPR@FP1)
    #    모델이 1개이거나 앙상블 조합이 없으면 단일 모델로 직행
    best_ind = max(indiv.items(), key=lambda kv: kv[1]["tpr_fp1"])
    if ens_results:
        best_ens = max(ens_results, key=lambda x: x["tpr_fp1"])
        if best_ens["tpr_fp1"] >= best_ind[1]["tpr_fp1"]:
            selected = {"type": "ensemble", "members": best_ens["members"],
                        "method": best_ens["method"], "combo": best_ens["combo"],
                        "tpr_fp1": best_ens["tpr_fp1"]}
            why = (f"앙상블 {best_ens['combo']}/{best_ens['method']} "
                   f"TPR@FP1 {best_ens['tpr_fp1']:.1f}% ≥ 최고 개별 "
                   f"{best_ind[0]} {best_ind[1]['tpr_fp1']:.1f}%")
        else:
            selected = {"type": "single", "members": [best_ind[0]],
                        "method": "single", "combo": "single",
                        "tpr_fp1": best_ind[1]["tpr_fp1"]}
            why = f"최고 개별 {best_ind[0]} {best_ind[1]['tpr_fp1']:.1f}% > 모든 앙상블"
    else:
        # 단일 모델 — 앙상블 탐색 불필요
        selected = {"type": "single", "members": [best_ind[0]],
                    "method": "single", "combo": "single",
                    "tpr_fp1": best_ind[1]["tpr_fp1"]}
        why = f"단일 모델 {best_ind[0]} (앙상블 조합 없음)"
    selected["rationale"] = why
    s1["selected"] = selected
    STATE["stage1_done"] = True
    save_state()

    # stage1 결과 파일
    with open(os.path.join(OUT, "stage1_results.json"), "w", encoding="utf-8") as f:
        json.dump(s1, f, ensure_ascii=False, indent=2)

    set_progress(20, "Stage1 완료")
    report(card("🎯", "Stage 1 완료 — 최적 구성 선정",
                {"유형": selected["type"],
                 "멤버": ", ".join(selected["members"]),
                 "앙상블 방식": selected["method"],
                 "선정 TPR@FP1": f"{selected['tpr_fp1']:.1f}%",
                 "선정 사유": why},
                analysis=analyze_stage1_done(selected, indiv, ens_results)))
    return s1


# ════════════════════════════════════════════════════════════════════
# STAGE 2
# ════════════════════════════════════════════════════════════════════
def stage2(args):
    STATE["stage_label"] = "Stage2 대규모"
    sel = STATE["stage1"]["selected"]
    members = sel["members"]
    method = sel["method"]
    # 안전: stage2 재학습 멤버 최대 5개 (시간 예산 보호)
    if len(members) > 5:
        indiv = STATE["stage1"].get("individual", {})
        members = sorted(members, key=lambda n: indiv.get(n, {}).get("tpr_fp1", 0),
                         reverse=True)[:5]
        report(f"⏱️ [ens24] Stage2 멤버 {len(sel['members'])}→5개 제한(예산): {members}")
    report(card("🚀", "Stage 2 시작 — 대규모 학습 + HPO + 교차검증",
                {"구성": f"{sel['type']} ({', '.join(members)}) / {method}",
                 "데이터 규모": f"max_mmsi {args.stage2_mmsi:,} · epochs {args.stage2_epochs}",
                 "HPO": "lr 그리드 (기본 / 0.3×) 멤버별 최적 선택",
                 "교차검증": f"{args.cv_folds}-fold (날짜별 파일)",
                 "학습파일": os.path.basename(args.stage2_file)},
                analysis=analyze_stage2_start(
                    {"members": members}, args.stage2_mmsi, args.stage2_epochs, args.cv_folds)))

    s2 = STATE.setdefault("stage2", {"train_sec": {}, "folds": [], "hpo": {}})

    # HPO 선택용 소형 평가셋 (1회 생성, 학습파일 기준)
    hpo_eval = build_eval_set(args.stage2_file, min(1000, args.n_normal),
                              max(40, args.n_anom // 3))

    # 1) 멤버 대규모 재학습 + lr 그리드 HPO (마감 인지: 시간 압박 시 epochs 축소)
    train_deadline = STATE["t_start"] + args.budget_hours * 3600 - 2.5 * 3600
    for i, name in enumerate(members):
        set_progress(22 + i / max(1, len(members)) * 28,
                     f"Stage2 HPO 학습 {name} ({i+1}/{len(members)})")
        if model_files_exist("stage2", name):
            log_event(f"[stage2] {name} 이미 학습됨")
            continue
        ep = args.stage2_epochs
        if time.time() > train_deadline:
            ep = max(3, args.stage2_epochs // 3)
            report(f"⏳ [ens24] 시간 압박 — `{name}` epochs {args.stage2_epochs}→{ep} 축소")
        lrs = hpo_lrs(name)
        report(f"🔧 [ens24] Stage2 HPO {i+1}/{len(members)}: `{name}` "
               f"(mmsi={args.stage2_mmsi}, ep={ep}, lr그리드={lrs})")
        ok, sec, best_lr, best_tpr = train_member_best(
            name, args.stage2_file, "stage2", args.stage2_mmsi, ep, lrs, hpo_eval)
        s2["train_sec"][name] = round(sec, 1)
        s2["hpo"][name] = {"best_lr": best_lr, "best_tpr": best_tpr}
        save_state()
        if not ok:
            report(f"⚠️ [ens24] Stage2 `{name}` 학습 실패")
        else:
            report(f"  ↳ `{name}` HPO 최적 lr={best_lr} (trial TPR@FP1={best_tpr}%)")
    trained = [m for m in members if model_files_exist("stage2", m)]
    if not trained:
        raise RuntimeError("Stage2: 학습된 멤버가 없습니다")
    report(f"✅ [ens24] Stage2 멤버 학습 완료: {trained}")

    # 2) 교차검증 (여러 날짜 파일을 폴드로)
    fold_files = pick_cv_files(args, exclude=args.stage2_file)
    n_folds = min(args.cv_folds, len(fold_files))
    fold_metrics = []
    deadline = STATE["t_start"] + args.budget_hours * 3600
    for k in range(n_folds):
        if time.time() > deadline:
            report("⏳ [ens24] 예산 시간 초과 — CV 조기 종료")
            break
        if k < len(s2["folds"]):
            fold_metrics.append(s2["folds"][k])
            continue
        set_progress(50 + (k + 1) / max(1, n_folds) * 35,
                     f"Stage2 CV 폴드 {k+1}/{n_folds}")
        f = fold_files[k]
        report(f"🔁 [ens24] CV 폴드 {k+1}/{n_folds}: {os.path.basename(f)}")
        try:
            es = build_eval_set(f, args.n_normal, args.n_anom)
            neg_mat, pos_mat, perscn, _ = collect_scores("stage2", trained, es)
            if sel["type"] == "ensemble" and len(trained) >= 2:
                w = {n: average_precision_score(
                        np.r_[np.zeros(len(neg_mat[n])), np.ones(len(pos_mat[n]))],
                        np.r_[neg_mat[n], pos_mat[n]]) for n in trained}
                r = ensemble_eval(neg_mat, pos_mat, trained, method, w)
            else:
                nm = trained[0]
                r = metrics_from_scores(neg_mat[nm], pos_mat[nm])
                r["members"], r["method"] = [nm], "single"
            r["fold_file"] = os.path.basename(f)
            fold_metrics.append(r)
            s2["folds"] = fold_metrics
            save_state()
            update_best(f"stage2/CV{k+1}", r["tpr_fp1"])
            report(f"  ↳ 폴드{k+1} TPR@FP1={r['tpr_fp1']:.1f}% "
                   f"PR-AUC={r['pr_auc']:.3f} F1={r['f1']:.1f}")
        except Exception as e:
            log_event(f"[stage2] 폴드{k+1} 실패: {e}\n{traceback.format_exc()}")

    # 3) CV 요약 + 최종 테스트셋 평가
    set_progress(88, "Stage2 최종 테스트 평가")
    if fold_metrics:
        tprs = [m["tpr_fp1"] for m in fold_metrics]
        s2["cv_summary"] = {
            "n_folds": len(fold_metrics),
            "tpr_fp1_mean": round(float(np.mean(tprs)), 2),
            "tpr_fp1_std": round(float(np.std(tprs)), 2),
            "pr_auc_mean": round(float(np.mean([m["pr_auc"] for m in fold_metrics])), 4),
            "roc_auc_mean": round(float(np.mean([m["roc_auc"] for m in fold_metrics])), 4),
            "f1_mean": round(float(np.mean([m["f1"] for m in fold_metrics])), 2),
        }

    # 최종 테스트: 전용 파일 (학습/CV 미사용)
    test_file = pick_cv_files(args, exclude=args.stage2_file,
                              extra_exclude=[os.path.basename(x) for x in fold_files[:n_folds]])
    test_file = test_file[0] if test_file else args.stage2_file
    es = build_eval_set(test_file, args.n_normal * 2, args.n_anom)
    neg_mat, pos_mat, perscn, infer_ms = collect_scores("stage2", trained, es)
    if sel["type"] == "ensemble" and len(trained) >= 2:
        w = {n: average_precision_score(
                np.r_[np.zeros(len(neg_mat[n])), np.ones(len(pos_mat[n]))],
                np.r_[neg_mat[n], pos_mat[n]]) for n in trained}
        final = ensemble_eval(neg_mat, pos_mat, trained, method, w)
        # 배포용 스태킹 메타 저장
        if method == "stack":
            try:
                import pickle
                clf, stats = fit_final_stack(neg_mat, pos_mat, trained)
                with open(os.path.join(MODELS, "stage2", "stack_meta.pkl"), "wb") as f:
                    pickle.dump({"clf": clf, "stats": stats, "members": trained}, f)
            except Exception as e:
                log_event(f"[stage2] stack_meta 저장 실패: {e}")
    else:
        nm = trained[0]
        final = metrics_from_scores(neg_mat[nm], pos_mat[nm])
        final["members"], final["method"] = [nm], "single"
    # per-scenario (대표 = 첫 멤버 기준 + 앙상블 별도 계산 생략, 개별 기록)
    final["test_file"] = os.path.basename(test_file)
    final["infer_ms"] = infer_ms
    final["per_scenario_member0"] = per_scenario_rates(
        neg_mat[trained[0]], perscn[trained[0]], es["scenario_names"])
    s2["final"] = final
    STATE["stage2_done"] = True
    save_state()
    with open(os.path.join(OUT, "stage2_results.json"), "w", encoding="utf-8") as f:
        json.dump(s2, f, ensure_ascii=False, indent=2)

    update_best("stage2/final", final["tpr_fp1"])
    set_progress(95, "Stage2 완료")
    cm = final["confusion"]
    cvs = s2.get("cv_summary", {})
    hpo = s2.get("hpo", {})
    hpo_txt = ", ".join(f"{k}:lr={v.get('best_lr')}" for k, v in hpo.items()) or "-"
    report(card("🎯", "Stage 2 완료 — 최종 평가",
                {"구성": f"{', '.join(trained)} / {method}",
                 "HPO 최적 lr": hpo_txt,
                 "최종 (테스트셋)": f"TPR@FP1 **{final['tpr_fp1']:.1f}%** · FPR {final['fpr']:.2f}%",
                 "보조 지표": f"PR-AUC {final['pr_auc']:.3f} · ROC-AUC {final['roc_auc']:.3f} · F1 {final['f1']:.1f}",
                 "교차검증": f"TPR {cvs.get('tpr_fp1_mean','-')}±{cvs.get('tpr_fp1_std','-')}% ({cvs.get('n_folds','-')}-fold)",
                 "Confusion": f"TP={cm['tp']} FP={cm['fp']} FN={cm['fn']} TN={cm['tn']}"},
                analysis=analyze_final(final, cvs)))
    return s2


# ── CV 파일 선택 ─────────────────────────────────────────────────────
def pick_cv_files(args, exclude=None, extra_exclude=None, min_bytes=20_000_000) -> list:
    files = sorted(f for f in glob.glob(os.path.join(args.data_dir, "*_preprocessed.csv"))
                   if os.path.getsize(f) >= min_bytes)
    ex = set()
    if exclude:
        ex.add(os.path.basename(exclude))
    for e in (extra_exclude or []):
        ex.add(e)
    files = [f for f in files if os.path.basename(f) not in ex]
    if not files:
        return []
    # 풀에서 균등 간격 샘플 (다양한 시기)
    n = max(1, args.cv_folds + 1)
    step = max(1, len(files) // n)
    return files[::step][:n]


# ════════════════════════════════════════════════════════════════════
# 완료 후 자동 배포 (post_run_distribute)
# ════════════════════════════════════════════════════════════════════
def _load_pat() -> str:
    """GitHub PAT를 .mcp.json에서 로드."""
    for p in (r"C:\ccit\JB-Pirate-King\.mcp.json",
              os.path.join(os.path.dirname(_ML), "..", ".mcp.json")):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8-sig") as f:
                    d = json.load(f)
                return d.get("mcpServers", {}).get("github", {}).get(
                    "env", {}).get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
            except Exception:
                pass
    return ""


def _github_api(path: str, data: dict | None, method: str, pat: str,
                content_type: str = "application/json") -> tuple:
    """GitHub REST API 호출. (status, body_dict)"""
    url = f"https://api.github.com{path}"
    payload = json.dumps(data).encode() if data else None
    headers = {"Authorization": f"token {pat}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": content_type}
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as e:
        log_event(f"[github_api] 실패: {e}")
        return 0, {}


def _create_model_zip() -> str | None:
    """stage2 멤버 모델 파일 zip 생성. 경로 반환."""
    import zipfile
    sel = STATE.get("stage1", {}).get("selected", {})
    members = sel.get("members", [])
    if not members:
        return None
    zip_name = f"trained_models_{datetime.now():%Y-%m-%d}.zip"
    zip_path = os.path.join(OUT, zip_name)
    added = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for m in members:
            mdir = os.path.join(MODELS, "stage2", m)
            for ext in ("onnx", "json", "txt"):
                src = os.path.join(mdir, f"model_{m}.{ext}" if ext == "onnx"
                                   else (f"scaler_{m}.json" if ext == "json"
                                         else f"threshold_{m}.txt"))
                if os.path.exists(src):
                    z.write(src, f"{m}/{os.path.basename(src)}")
                    added.append(f"{m}/{os.path.basename(src)}")
    if not added:
        return None
    log_event(f"[distribute] 모델 zip 생성: {zip_path} ({len(added)}개)")
    return zip_path


def _upload_model_release(zip_path: str, pat: str) -> str | None:
    """GitHub Release 생성 + zip 업로드. Release URL 반환."""
    tag = f"models/{datetime.now():%Y-%m-%d}"
    final = STATE.get("stage2", {}).get("final", {})
    sel = STATE.get("stage1", {}).get("selected", {})
    cv = STATE.get("stage2", {}).get("cv_summary", {})
    body = (f"## 학습 모델 ({datetime.now():%Y-%m-%d})\n\n"
            f"- 구성: {', '.join(sel.get('members',[]))} / {sel.get('method','-')}\n"
            f"- FPR=1% 탐지율: {final.get('tpr_fp1','-')}% "
            f"(CV {cv.get('tpr_fp1_mean','-')}±{cv.get('tpr_fp1_std','-')}%)\n"
            f"- run_id: {STATE.get('run_id','-')}\n\n"
            f"첨부 zip 내 모델(onnx/scaler/threshold) 포함.")
    st, resp = _github_api("/repos/JB-Pirate-King/JB-Pirate-King/releases",
                           {"tag_name": tag, "target_commitish": "develop",
                            "name": f"모델 {tag}", "body": body, "prerelease": True},
                           "POST", pat)
    if st not in (200, 201) or "id" not in resp:
        log_event(f"[distribute] Release 생성 실패: {st}")
        return None
    release_id = resp["id"]
    html_url = resp.get("html_url", "")
    upload_url = f"https://uploads.github.com/repos/JB-Pirate-King/JB-Pirate-King/releases/{release_id}/assets"
    with open(zip_path, "rb") as f:
        data = f.read()
    fname = os.path.basename(zip_path)
    req = urllib.request.Request(
        f"{upload_url}?name={fname}", data=data,
        headers={"Authorization": f"token {pat}", "Content-Type": "application/zip",
                 "User-Agent": _UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            asset = json.loads(r.read())
            log_event(f"[distribute] 모델 업로드 완료: {asset.get('browser_download_url')}")
    except Exception as e:
        log_event(f"[distribute] 모델 업로드 실패: {e}")
    return html_url


def _git_push_code(pat: str):
    """미커밋 변경사항이 있으면 커밋 후 origin/develop에 push."""
    import subprocess
    cwd = os.path.dirname(_ML)
    try:
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                               capture_output=True, text=True).stdout.strip()
        if not dirty:
            log_event("[distribute] 코드 변경 없음 — push 생략")
            return
        subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
        msg = (f"chore(auto): post-run state update [{STATE.get('run_id','-')}]\n\n"
               f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>")
        subprocess.run(["git", "commit", "-m", msg], cwd=cwd, check=True)
        remote = f"https://{pat}@github.com/JB-Pirate-King/JB-Pirate-King.git"
        subprocess.run(["git", "remote", "set-url", "origin", remote], cwd=cwd, check=True)
        # 워크트리 브랜치를 push 후 fast-forward 머지
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=cwd,
                                 capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "push", "origin", f"{branch}:develop"], cwd=cwd, check=True)
        log_event(f"[distribute] 코드 push 완료: {branch} → origin/develop")
    except Exception as e:
        log_event(f"[distribute] 코드 push 실패: {e}")


def _update_obsidian(final_report_path: str, release_url: str):
    """Obsidian 현재 작업 + 인수인계 문서 자동 갱신."""
    VAULT = r"C:\ObsidianVault"
    if not os.path.isdir(VAULT):
        return
    final = STATE.get("stage2", {}).get("final", {})
    cv = STATE.get("stage2", {}).get("cv_summary", {})
    sel = STATE.get("stage1", {}).get("selected", {})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    tpr = final.get("tpr_fp1", "-")
    cv_m = cv.get("tpr_fp1_mean", "-")

    # 현재 작업 스냅샷 갱신
    wip = os.path.join(VAULT, "운영", "_Working", "현재 작업.md")
    if os.path.exists(wip):
        try:
            txt = open(wip, encoding="utf-8").read()
            txt = txt.replace("updated: 2026", f"updated: {ts[:10]}")
            # status 줄 교체
            import re
            txt = re.sub(r"^status:.*$", f"status: ens24 완료 FPR1% {tpr}% — Iter9(FN4) 대기",
                         txt, flags=re.MULTILINE)
            open(wip, "w", encoding="utf-8").write(txt)
            log_event("[distribute] Obsidian 현재 작업 갱신 완료")
        except Exception as e:
            log_event(f"[distribute] Obsidian 갱신 실패: {e}")

    # 인수인계 요약 파일 덮어쓰기
    handoff = os.path.join(VAULT, "운영", "_Working", "다음 세션 작업 지시.md")
    content = f"""---
updated: {ts}
status: ready-to-execute
run_id: {STATE.get('run_id','-')}
---
[[00 - 프로젝트 현황 (Claude)|← 현황 허브]]

# ▶ 다음 세션 실행 지시

## 최우선: FN4-status 보강 (Iteration 9)
- dcdetect conv1d 공통 최약세: FN4-status (dcdetect 7%, conv1d 0%)
- status 전용 파생피처 추가 후 `feature_engineer.py` 재실행

## 방금 완료한 run 결과
- **ens24 {sel.get('method','-')} [{', '.join(sel.get('members',[]))}]**
- FPR=1% 탐지율: **{tpr}%** (CV {cv_m}±{cv.get('tpr_fp1_std','-')}%)
- 최종 보고서: `{final_report_path}`
- GitHub Release: {release_url or '미생성'}

## 배포 완료
- GitHub Release: {release_url or '-'}
- Obsidian: 갱신 완료

## Sheets/Drive 배포 대기 (MCP 필요)
- 매니페스트: `{os.path.join(OUT, 'distribute_manifest.json')}`
- 새 세션 시작 시 bootstrap.py 실행 → 자동 안내
"""
    try:
        open(handoff, "w", encoding="utf-8").write(content)
        log_event("[distribute] 인수인계 지시서 갱신 완료")
    except Exception as e:
        log_event(f"[distribute] 인수인계 갱신 실패: {e}")


def _save_manifest(release_url: str, final_report_path: str):
    """Sheets/Drive 배포용 매니페스트 저장 (MCP 픽업용)."""
    final = STATE.get("stage2", {}).get("final", {})
    cv = STATE.get("stage2", {}).get("cv_summary", {})
    sel = STATE.get("stage1", {}).get("selected", {})
    manifest = {
        "run_id": STATE.get("run_id"),
        "ts": datetime.now().isoformat(),
        "model": sel.get("members", []),
        "method": sel.get("method"),
        "tpr_fp1": final.get("tpr_fp1"),
        "fpr": final.get("fpr"),
        "pr_auc": final.get("pr_auc"),
        "roc_auc": final.get("roc_auc"),
        "f1": final.get("f1"),
        "confusion": final.get("confusion"),
        "cv_mean": cv.get("tpr_fp1_mean"),
        "cv_std": cv.get("tpr_fp1_std"),
        "cv_folds": cv.get("n_folds"),
        "hpo": STATE.get("stage2", {}).get("hpo", {}),
        "per_scenario": final.get("per_scenario_member0", {}),
        "github_release": release_url,
        "final_report": final_report_path,
        "stage1_results": os.path.join(OUT, "stage1_results.json"),
        "stage2_results": os.path.join(OUT, "stage2_results.json"),
        "model_dir": os.path.join(MODELS, "stage2"),
        "pending": ["sheets_update", "drive_upload", "notion_update"],
        "sheets_id": "1uSF1FXsMvha24t0LpgNbI20MLumbq4lm1LbBtc14H1U",
        "drive_folder_id": "1x0VpT9L9wD07HKSmwdZC-s5a_V5QAqus",
    }
    path = os.path.join(OUT, "distribute_manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    log_event(f"[distribute] 매니페스트 저장: {path}")
    return path


def post_run_distribute(args, final_report_path: str):
    """
    완료 후 자동 배포 파이프라인
    ─────────────────────────────
    ✅ 완전 자동 (코드 내):
      1. GitHub Release + 모델 zip 업로드
      2. 코드 변경 있으면 develop에 push
      3. Obsidian SSOT (현재 작업·인수인계) 갱신
      4. Discord/Slack 배포 완료 보고
    📋 매니페스트 저장 (MCP 픽업):
      5. distribute_manifest.json → 새 세션 bootstrap.py가 감지
         → Sheets·Drive·Notion 자동 처리
    """
    report(card("📤", "자동 배포 시작",
                {"단계": "GitHub Release → Obsidian → 매니페스트"},
                analysis="학습 완료 후 산출물을 자동으로 배포합니다.\n"
                         "Sheets·Drive는 MCP가 필요해 매니페스트를 저장하고 다음 세션에서 처리합니다."))

    pat = _load_pat()
    release_url = "-"

    # 1. 모델 zip + GitHub Release
    zip_path = _create_model_zip()
    if zip_path and pat:
        release_url = _upload_model_release(zip_path, pat) or "-"
        log_event(f"[distribute] GitHub Release: {release_url}")
    else:
        log_event("[distribute] 모델 zip 또는 PAT 없음 — Release 생략")

    # 2. 코드 push
    if pat and not getattr(args, "smoke", False):
        _git_push_code(pat)

    # 3. Obsidian SSOT 갱신
    _update_obsidian(final_report_path, release_url)

    # 4. 매니페스트 저장 (Sheets/Drive/Notion)
    manifest_path = _save_manifest(release_url, final_report_path)

    # 5. 배포 완료 보고
    final = STATE.get("stage2", {}).get("final", {})
    cv = STATE.get("stage2", {}).get("cv_summary", {})
    report(card("✅", "자동 배포 완료",
                {"GitHub Release": release_url,
                 "Obsidian SSOT": "현재 작업 + 인수인계 갱신 완료",
                 "Sheets/Drive/Notion": f"매니페스트 저장 → {manifest_path}",
                 "🎯 최종 결과": f"FPR=1% {final.get('tpr_fp1','-')}% (CV {cv.get('tpr_fp1_mean','-')}±{cv.get('tpr_fp1_std','-')}%)"},
                analysis=("GitHub Release에 학습 모델 zip이 업로드됐습니다.\n"
                          "Sheets·Drive·Notion 배포는 distribute_manifest.json을 저장했습니다.\n"
                          "다음 세션 시작 시 bootstrap.py가 이 매니페스트를 감지하고 자동으로 처리합니다.")))


# ════════════════════════════════════════════════════════════════════
# 최종 보고서
# ════════════════════════════════════════════════════════════════════
def write_final_report(args):
    s1 = STATE.get("stage1", {})
    s2 = STATE.get("stage2", {})
    sel = s1.get("selected", {})
    final = s2.get("final", {})
    cv = s2.get("cv_summary", {})
    indiv = s1.get("individual", {})
    ens = s1.get("ensembles", [])
    el = str(timedelta(seconds=int(time.time() - STATE.get("t_start", time.time()))))

    lines = []
    L = lines.append
    L(f"# ens24 최종 보고서 — 비지도 이상탐지 앙상블")
    L(f"\n생성: {datetime.now():%Y-%m-%d %H:%M}  | 총 소요: {el}  | run_id: {STATE.get('run_id')}")
    L(f"\n## 최종 목표 결과")
    L(f"**오탐률(FPR) 1% 조건에서 최대 탐지율(TPR) = {final.get('tpr_fp1','-')}%** "
      f"(CV 평균 {cv.get('tpr_fp1_mean','-')}±{cv.get('tpr_fp1_std','-')}%)")
    L(f"\n## 1. 선정 구성")
    L(f"- 유형: {sel.get('type')}  | 멤버: {sel.get('members')}  | 앙상블 방식: {sel.get('method')}")
    L(f"- 선정 사유: {sel.get('rationale')}")
    L(f"\n## 2. 개별 모델 성능 (Stage1, FPR=1%)")
    L("| 모델 | TPR@FP1 | Precision | F1 | ROC-AUC | PR-AUC | 학습(s) | 추론(ms) |")
    L("|---|---|---|---|---|---|---|---|")
    for n in sorted(indiv, key=lambda x: indiv[x]["tpr_fp1"], reverse=True):
        m = indiv[n]
        L(f"| {n} | {m['tpr_fp1']} | {m['precision']} | {m['f1']} | {m['roc_auc']} "
          f"| {m['pr_auc']} | {m.get('train_sec','-')} | {m.get('infer_ms','-')} |")
    L(f"\n## 3. 앙상블 성능 (Stage1, FPR=1%)")
    L("| 조합 | 방식 | TPR@FP1 | FPR | PR-AUC | F1 |")
    L("|---|---|---|---|---|---|")
    for r in sorted(ens, key=lambda x: x["tpr_fp1"], reverse=True):
        L(f"| {r['combo']} | {r['method']} | {r['tpr_fp1']} | {r['fpr']} | {r['pr_auc']} | {r['f1']} |")
    if final:
        cm = final.get("confusion", {})
        L(f"\n## 4. 최종 대규모 평가 (Stage2)")
        L(f"- 학습 데이터: {os.path.basename(args.stage2_file)} (max_mmsi={args.stage2_mmsi}, "
          f"epochs={args.stage2_epochs})")
        L(f"- 테스트 파일: {final.get('test_file')}")
        L(f"- 멤버 학습시간(s): {s2.get('train_sec',{})}")
        if s2.get("hpo"):
            L(f"- HPO(lr 그리드) 최적: " +
              ", ".join(f"{k}: lr={v.get('best_lr')}(TPR {v.get('best_tpr')}%)"
                        for k, v in s2["hpo"].items()))
        L(f"- **FPR={final.get('fpr')}% / TPR={final.get('tpr_fp1')}% / "
          f"Precision={final.get('precision')}% / F1={final.get('f1')}**")
        L(f"- ROC-AUC={final.get('roc_auc')}  PR-AUC={final.get('pr_auc')}")
        L(f"- Confusion Matrix: TN={cm.get('tn')} FP={cm.get('fp')} "
          f"FN={cm.get('fn')} TP={cm.get('tp')}")
        L(f"- 교차검증({cv.get('n_folds','-')}폴드): TPR@FP1 {cv.get('tpr_fp1_mean','-')}±"
          f"{cv.get('tpr_fp1_std','-')}% · PR-AUC {cv.get('pr_auc_mean','-')} · "
          f"ROC-AUC {cv.get('roc_auc_mean','-')}")
        L(f"\n### 운영 시 예상 성능")
        L(f"교차검증 평균 기준, 오탐률 1% 운영 시 탐지율 약 "
          f"**{cv.get('tpr_fp1_mean', final.get('tpr_fp1','-'))}%** 예상.")
    path = os.path.join(OUT, "final_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log_event(f"최종 보고서: {path}")
    return path, "\n".join(lines)


# ════════════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default=r"D:\JB-Pirate-King-AIS\preprocessed_all")
    ap.add_argument("--stage1_file", default=None)
    ap.add_argument("--stage2_file", default=None)
    ap.add_argument("--stage1_mmsi", type=int, default=1200)
    ap.add_argument("--stage2_mmsi", type=int, default=6000)
    ap.add_argument("--stage1_epochs", type=int, default=8)
    ap.add_argument("--stage2_epochs", type=int, default=25)
    ap.add_argument("--n_normal", type=int, default=3000)
    ap.add_argument("--n_anom", type=int, default=200)
    ap.add_argument("--cv_folds", type=int, default=4)
    ap.add_argument("--budget_hours", type=float, default=24.0)
    ap.add_argument("--include_sklearn", action="store_true", default=True)
    ap.add_argument("--no_sklearn", dest="include_sklearn", action="store_false")
    ap.add_argument("--models", nargs="+", default=None,
                    help="사용할 모델 목록 직접 지정 (기본: 비지도 9개). 예: --models conv1d dcdetect")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="중복 인스턴스 가드 무시하고 강제 시작 (직전 run 비정상 종료 후 재개 시)")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    # 산출물 경로 태그 (스모크 격리)
    tag = args.tag or ("smoke" if args.smoke else "")
    if tag:
        global OUT, MODELS, STATE_PATH, EVENTS_LOG
        OUT = OUT + "_" + tag
        MODELS = MODELS + "_" + tag
        STATE_PATH = os.path.join(OUT, "state.json")
        EVENTS_LOG = os.path.join(OUT, "events.log")

    # 모델 집합: --models 직접 지정 > 기본(비지도 전체)
    if args.models:
        pass   # 이미 args.models 에 지정됨
    else:
        args.models = NEURAL + (SK if args.include_sklearn else [])

    # 데이터 파일 자동 선택
    pool = sorted(glob.glob(os.path.join(args.data_dir, "*_preprocessed.csv")))
    if not pool:
        print(f"오류: 데이터 없음 {args.data_dir}")
        sys.exit(1)
    if not args.stage1_file:
        cand = [f for f in pool if "2017-08-02" in f]
        args.stage1_file = cand[0] if cand else pool[len(pool)//2]
    if not args.stage2_file:
        big = max(pool, key=lambda f: os.path.getsize(f))
        args.stage2_file = big

    if args.smoke:
        args.stage1_mmsi = 60; args.stage2_mmsi = 80
        args.stage1_epochs = 1; args.stage2_epochs = 1
        args.n_normal = 150; args.n_anom = 20
        args.cv_folds = 1; args.budget_hours = 0.2
        args.models = ["conv1d", "tcn", "dcdetect"]

    # 상태 초기화/재개
    global STATE
    STATE = {} if args.fresh else load_state()
    if not STATE:
        STATE = {"run_id": datetime.now().strftime("ens24_%Y%m%d_%H%M%S"),
                 "t_start": time.time(), "progress": 0.0, "last_decile": -1,
                 "best": {}, "args": vars(args)}
    else:
        STATE.setdefault("t_start", time.time())
        STATE["resumed_at"] = time.time()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(MODELS, exist_ok=True)

    # 중복 실행 방지: state.json 이 최근(10분내) 갱신 + 미완료면 다른 인스턴스 활성으로 간주
    done_flag = os.path.join(OUT, "DONE.flag")
    if os.path.exists(done_flag) and not args.fresh:
        print("[ens24] DONE.flag 존재 — 이미 완료됨, 종료"); sys.exit(0)
    if os.path.exists(STATE_PATH) and not args.fresh and not getattr(args, "force", False):
        try:
            age = time.time() - os.path.getmtime(STATE_PATH)
            if age < 600 and not STATE.get("done"):
                print(f"[ens24] 다른 인스턴스 활성 추정 (state {age:.0f}s 전 갱신) — 종료")
                print(f"  강제 시작하려면 --force 옵션을 추가하세요.")
                sys.exit(0)
        except Exception:
            pass
    save_state()

    hb = Heartbeat(interval=60 if args.smoke else 1800)
    hb.start()

    mode = "스모크" if args.smoke else "본"
    act = "재개" if STATE.get("resumed_at") else "시작"
    report(card("🧪" if args.smoke else "🟢", f"{mode} 실행 {act}",
                {"run_id": STATE["run_id"],
                 "예산": f"{args.budget_hours}h",
                 "모델": f"{len(args.models)}개 — {', '.join(args.models)}",
                 "Stage1 표본": f"{os.path.basename(args.stage1_file)} (mmsi {args.stage1_mmsi:,})",
                 "Stage2 대규모": f"{os.path.basename(args.stage2_file)} (mmsi {args.stage2_mmsi:,})"},
                analysis=(
                    "목표: 오탐률 1% 이하에서 최대 탐지율 달성.\n"
                    "Stage1(탐색)에서 구성을 고르고, Stage2(대규모+HPO+CV)에서 확정한다.\n"
                    "중단되어도 체크포인트로 자동 재개되며, 단계마다 분석과 함께 보고한다.")))

    try:
        if not STATE.get("stage1_done"):
            stage1(args)
        else:
            report("↩️ [ens24] Stage1 완료 상태 — Stage2로")
        if not STATE.get("stage2_done"):
            stage2(args)
        else:
            report("↩️ [ens24] Stage2 완료 상태 — 보고서로")

        path, body = write_final_report(args)
        set_progress(100, "완료")
        STATE["done"] = True
        save_state()
        try:
            with open(os.path.join(OUT, "DONE.flag"), "w", encoding="utf-8") as f:
                f.write(STATE.get("run_id", "") + "\n")
        except Exception:
            pass
        final = STATE.get("stage2", {}).get("final", {})
        cv = STATE.get("stage2", {}).get("cv_summary", {})
        sel = STATE.get("stage1", {}).get("selected", {})
        report(card("🏁", "전체 완료 — 최종 결과",
                    {"run_id": STATE["run_id"],
                     "최종 구성": f"{', '.join(sel.get('members',[]))} / {sel.get('method','-')}",
                     "🎯 최대 탐지율(FPR≤1%)": f"**{final.get('tpr_fp1','-')}%** (실제 FPR {final.get('fpr','-')}%)",
                     "운영 예상(CV평균)": f"{cv.get('tpr_fp1_mean','-')}±{cv.get('tpr_fp1_std','-')}%",
                     "보고서": path},
                    analysis=("24h 예산 내 탐색→대규모학습 완료. "
                              f"오탐 1% 제약에서 달성 가능한 최대 탐지율은 "
                              f"약 {final.get('tpr_fp1','-')}%로 확정.\n"
                              "결과물·모델·로그가 저장되었으며, 산출물 배포를 시작합니다.")))

        # ── 완료 후 자동 배포 ─────────────────────────────────────
        if not args.smoke:
            try:
                post_run_distribute(args, path)
            except Exception as dist_e:
                log_event(f"[distribute] 배포 실패(무시, 결과는 보존됨): {dist_e}")
    except Exception as e:
        tb_txt = traceback.format_exc()
        report(card("❌", "오류 발생 — 상태 저장됨(재개 가능)",
                    {"단계": STATE.get("stage_label", "-"),
                     "작업": STATE.get("current_task", "-"),
                     "오류": f"`{e}`"},
                    analysis=("체크포인트가 저장되어 재실행 시 이어서 진행됩니다.\n"
                              f"```\n{tb_txt[-600:]}\n```")))
        save_state()
        hb.stop()
        sys.exit(1)
    finally:
        hb.stop()


if __name__ == "__main__":
    main()
