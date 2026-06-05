#!/usr/bin/env python3
"""
FN4-status 고착 원인 진단 실험  (FE Iteration 9 사전 진단)

가설:
  재구성 기반 DCdetector에서 FN4-status(비통상 항행상태 공격)는 status 채널
  하나만 이상이다. status 이진 플래그 1개의 재구성오차는 ~18개 채널 평균에
  희석되어, Greedy의 단독 목적점수 이득이 채택임계(min_gain=3.0)를 못 넘는다.
  → status 후보 피처들이 정의돼 있어도 영원히 채택되지 않아 FN4가 ~7%에 고착.
  Greedy는 피처 '상호작용(블록)'을 못 찾는 국소최적 맹점을 가진다.

검증:
  동일 데이터/시드로 아래 변형을 학습·평가하여 FN4-status 탐지율(FP=1%)과
  전체평균·목적점수·타 시나리오 회귀를 비교한다.
    V0_base6        : 현재 채택 6개 (대조군)
    V1_+fn4flag     : +status_fn4_flag (단독)  → 단독으로는 부족한가?
    V2_+uncommon    : +uncommon_status (단독)
    V3_statusblock  : +status_fn4_flag +uncommon_status +status_change +status_motion (블록)

실행 (repo 루트에서):
  $env:PYTHONPATH="C:\\pylibs"; python ml/experiments/fn4_status_probe.py
출력:
  콘솔 비교표 + ml/.pipeline_tmp/fn4_probe_<ts>.json
"""
import json
import os
import random
import sys
import time

import numpy as np
import torch

# ── ml/core 를 import 경로에 추가 ─────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import feature_engineer as fe  # noqa: E402

# ── 실험 설정 ──────────────────────────────────────────────────────
INPUT = r"D:\JB-Pirate-King-AIS\preprocessed_all\ais-2017-08-02_preprocessed.csv"
MAX_MMSI   = 2000     # 기존 시퀀스 캐시(s2000) 재사용 → 재파싱 없음
EPOCHS     = 4
N_ANOM     = 300
EVAL_RATIO = fe.EVAL_NORMAL_RATIO
TRAIN_SEED = fe.SEED
EVAL_SEED  = 12345    # 평가용 합성 이상/정상 샘플 고정 (변형 간 동일 비교)
WEAK_FLOOR = 50.0
WEAK_WEIGHT = 1.5

BASE6 = ["accel", "heading_rate", "vec_sog_diff", "heading_change",
         "sog_vec_kn", "turn_rate"]   # = 현재 INITIAL_EXTRA

VARIANTS = {
    "V0_base6":       BASE6,
    "V1_+fn4flag":    BASE6 + ["status_fn4_flag"],
    "V2_+uncommon":   BASE6 + ["uncommon_status"],
    "V3_statusblock": BASE6 + ["status_fn4_flag", "uncommon_status",
                               "status_change", "status_motion"],
}

FOCUS = "FN4-status"


def seed_all(s):
    random.seed(s); np.random.seed(s); torch.manual_seed(s)


def run_variant(name, extra, train_seqs, eval_seqs):
    n_feat = len(fe.BASE_FEATURES) + len(extra)
    nhead = max(h for h in range(1, 9) if n_feat % h == 0 and 64 % h == 0)
    print(f"\n{'='*70}\n[{name}]  extra={extra}\n  n_feat={n_feat}  nhead={nhead}\n{'='*70}")

    seed_all(TRAIN_SEED)
    tensor, scaler = fe.prepare_tensor(train_seqs, extra)
    model, _ = fe.train_dcdetect(tensor, n_feat, EPOCHS)

    seed_all(EVAL_SEED)   # 동일 합성 이상/정상 샘플로 평가
    det, sc, extra_res, thr = fe.evaluate(
        model, scaler, extra, N_ANOM, raw_seqs=eval_seqs, extra_fp=(5.0, 10.0))

    scen = {n: d for n, d, _ in sc}
    weak_names = {n for n, d, _ in sc if d < WEAK_FLOOR}
    obj = fe._objective(sc, weak_names, WEAK_WEIGHT)
    fp5 = extra_res.get(5.0, {})
    fp10 = extra_res.get(10.0, {})
    print(f"  → 전체평균 {det:5.1f}%  | {FOCUS} {scen.get(FOCUS, float('nan')):5.1f}%"
          f"  | 목적점수 {obj:6.1f}  | thr={thr:.6f}")
    return {
        "extra": extra, "n_feat": n_feat, "nhead": nhead,
        "overall_fp1": det, "focus_fp1": scen.get(FOCUS),
        "focus_fp5": fp5.get(FOCUS), "focus_fp10": fp10.get(FOCUS),
        "objective": obj, "threshold": thr,
        "scenarios_fp1": scen, "weak_names": sorted(weak_names),
    }


def main():
    t0 = time.time()
    print(f"[데이터] {INPUT}  max_mmsi={MAX_MMSI}")
    seed_all(TRAIN_SEED)
    train_seqs, eval_seqs = fe.load_raw_seqs(INPUT, MAX_MMSI, EVAL_RATIO)
    print(f"  학습 {len(train_seqs):,} / 홀드아웃 {len(eval_seqs):,}")

    results = {}
    for name, extra in VARIANTS.items():
        results[name] = run_variant(name, extra, train_seqs, eval_seqs)

    # ── 비교표 ────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("  비교 요약  (FP=1% 기준)")
    print("=" * 78)
    base = results["V0_base6"]
    hdr = f"  {'변형':<16}{'전체%':>8}{FOCUS+'%':>14}{'FP5%':>8}{'FP10%':>8}{'목적점수':>10}"
    print(hdr)
    for name, r in results.items():
        d_focus = (r["focus_fp1"] or 0) - (base["focus_fp1"] or 0)
        print(f"  {name:<16}{r['overall_fp1']:>8.1f}"
              f"{(r['focus_fp1'] or 0):>9.1f}({d_focus:+5.1f})"
              f"{(r['focus_fp5'] or 0):>8.1f}{(r['focus_fp10'] or 0):>8.1f}"
              f"{r['objective']:>10.1f}")

    # ── 회귀 점검: V3 블록이 5pp 이상 떨어뜨린 시나리오 ───────────────
    print("\n  [회귀 점검] V3_statusblock 가 V0 대비 5pp 이상 하락시킨 시나리오:")
    b = base["scenarios_fp1"]; v = results["V3_statusblock"]["scenarios_fp1"]
    regr = [(n, b[n], v[n]) for n in b if (b[n] - v.get(n, 0)) >= 5.0]
    if regr:
        for n, bd, vd in sorted(regr, key=lambda x: x[1] - x[2], reverse=True):
            print(f"    {n:<18} {bd:5.1f}% → {vd:5.1f}%  ({vd-bd:+.1f})")
    else:
        print("    없음 (회귀 없음)")

    # ── 저장 ──────────────────────────────────────────────────────
    out_dir = os.path.normpath(os.path.join(_HERE, "..", ".pipeline_tmp"))
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"fn4_probe_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"config": {
            "input": INPUT, "max_mmsi": MAX_MMSI, "epochs": EPOCHS,
            "n_anom": N_ANOM, "train_seed": TRAIN_SEED, "eval_seed": EVAL_SEED,
            "weak_floor": WEAK_FLOOR, "weak_weight": WEAK_WEIGHT,
        }, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n[저장] {out_path}")
    print(f"[총 소요] {(time.time()-t0)/60:.1f}분")


if __name__ == "__main__":
    main()
