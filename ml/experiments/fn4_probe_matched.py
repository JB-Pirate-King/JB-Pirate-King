#!/usr/bin/env python3
"""FN4 status 피처 효과 — nhead 교란 제거 재실험 (confound-free).

검증 워크플로 지적: 기존 probe의 V1/V2(19피처)는 nhead=1(퇴화 단일헤드)로
nhead=2인 V0과 비교 불가. 본 실험은 **n_feat=20 고정 → nhead=4 동일**로 맞추고,
중립(비-status) 필러 피처를 status 피처로 '교체'하며 효과를 격리한다.

  C0  base6 + [dist_speed_err, cog_change]        (status 0개, 20피처, nhead4)
  C1  base6 + [status_fn4_flag, cog_change]        (status 1개 — 필러A 교체)
  C2  base6 + [status_fn4_flag, uncommon_status]   (status 2개 — 필러B 교체)

C0→C1→C2 는 nhead=4 고정에서 중립피처를 status피처로 바꿔감 → FN4·D1·전체 변화가
순수 'status 피처 효과'. (참고용으로 2개 시드 평균.)
"""
import json
import os
import random
import sys
import time

import numpy as np
import torch

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
import feature_engineer as fe  # noqa: E402

INPUT = r"D:\JB-Pirate-King-AIS\preprocessed_all\ais-2017-08-02_preprocessed.csv"
MAX_MMSI, EPOCHS, N_ANOM = 2000, 4, 300
EVAL_RATIO = fe.EVAL_NORMAL_RATIO
EVAL_SEED = 12345
TRAIN_SEEDS = [42, 123]      # 시드 강건성 (평균)
WEAK_FLOOR, WEAK_WEIGHT = 50.0, 1.5
BASE6 = ["accel", "heading_rate", "vec_sog_diff", "heading_change", "sog_vec_kn", "turn_rate"]

VARIANTS = {
    "C0_2neutral":  BASE6 + ["dist_speed_err", "cog_change"],
    "C1_1status":   BASE6 + ["status_fn4_flag", "cog_change"],
    "C2_2status":   BASE6 + ["status_fn4_flag", "uncommon_status"],
}
WATCH = ["FN4-status", "D1-LowSlow"]   # FN4 타겟 + nhead=1 의심됐던 D1


def seed_all(s):
    random.seed(s); np.random.seed(s); torch.manual_seed(s)


def run_once(extra, train_seqs, eval_seqs, tseed):
    n_feat = len(fe.BASE_FEATURES) + len(extra)
    seed_all(tseed)
    tensor, scaler = fe.prepare_tensor(train_seqs, extra)
    model, _ = fe.train_dcdetect(tensor, n_feat, EPOCHS)
    seed_all(EVAL_SEED)
    det, sc, _, thr = fe.evaluate(model, scaler, extra, N_ANOM, raw_seqs=eval_seqs)
    scen = {n: d for n, d, _ in sc}
    weak = {n for n, d, _ in sc if d < WEAK_FLOOR}
    return det, scen, fe._objective(sc, weak, WEAK_WEIGHT), thr, n_feat


def main():
    t0 = time.time()
    seed_all(TRAIN_SEEDS[0])
    train_seqs, eval_seqs = fe.load_raw_seqs(INPUT, MAX_MMSI, EVAL_RATIO)
    print(f"학습 {len(train_seqs):,} / 홀드아웃 {len(eval_seqs):,}\n")

    results = {}
    for name, extra in VARIANTS.items():
        runs = [run_once(extra, train_seqs, eval_seqs, s) for s in TRAIN_SEEDS]
        n_feat = runs[0][4]
        nhead = max(h for h in range(1, 9) if n_feat % h == 0 and 64 % h == 0)
        overall = float(np.mean([r[0] for r in runs]))
        obj = float(np.mean([r[2] for r in runs]))
        watch = {w: float(np.mean([r[1].get(w, float('nan')) for r in runs])) for w in WATCH}
        # 시드별 시나리오 평균
        allnames = runs[0][1].keys()
        scen_avg = {n: float(np.mean([r[1].get(n, float('nan')) for r in runs])) for n in allnames}
        results[name] = {"extra": extra, "n_feat": n_feat, "nhead": nhead,
                         "overall": overall, "objective": obj, "watch": watch,
                         "scen_avg": scen_avg,
                         "per_seed_fn4": [r[1].get("FN4-status") for r in runs]}
        print(f"[{name}] n_feat={n_feat} nhead={nhead}  overall={overall:5.1f}  "
              f"FN4={watch['FN4-status']:5.1f}  D1={watch['D1-LowSlow']:5.1f}  obj={obj:6.1f}")

    print("\n" + "=" * 72)
    print(f"  confound-free 비교 (n_feat=20·nhead=4 고정, 시드 {TRAIN_SEEDS} 평균)")
    print("=" * 72)
    c0 = results["C0_2neutral"]
    print(f"  {'변형':<14}{'status수':>8}{'전체%':>8}{'FN4%':>9}{'D1%':>8}{'목적점수':>10}")
    statn = {"C0_2neutral": 0, "C1_1status": 1, "C2_2status": 2}
    for name, r in results.items():
        dfn4 = r["watch"]["FN4-status"] - c0["watch"]["FN4-status"]
        print(f"  {name:<14}{statn[name]:>8}{r['overall']:>8.1f}"
              f"{r['watch']['FN4-status']:>6.1f}({dfn4:+4.1f}){r['watch']['D1-LowSlow']:>8.1f}"
              f"{r['objective']:>10.1f}")
    print(f"\n  per-seed FN4: " + "  ".join(f"{n}={r['per_seed_fn4']}" for n, r in results.items()))

    out = os.path.join(_HERE, "..", ".pipeline_tmp", f"fn4_matched_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(os.path.normpath(out), "w", encoding="utf-8") as f:
        json.dump({"variants": {k: v for k, v in results.items()},
                   "config": {"seeds": TRAIN_SEEDS, "epochs": EPOCHS, "max_mmsi": MAX_MMSI}},
                  f, ensure_ascii=False, indent=2)
    print(f"\n[저장] {os.path.normpath(out)}\n[총 소요] {(time.time()-t0)/60:.1f}분")


if __name__ == "__main__":
    main()
