#!/usr/bin/env python3
"""FN4 분리가능성 검증 (adversarial) — '비지도 탐지 불가' 주장 반증 시도.

주장: FN4(전 구간 비통상 status + 정상 운동)는 정상 트래픽의 '전 구간 비통상
status' 시퀀스(4.4%)와 분포가 중첩 → 비지도로 분리 불가.

반증 시도: 실제 정상 데이터 중 '전 구간 비통상 status'인 시퀀스를 뽑아,
FN4 합성 시퀀스와 status 를 제외한 모든 피처(운동학)에서 구별되는지 본다.
구별되면 → status 가 아닌 다른 신호로 탐지 가능(주장 약화).
구별 안 되면 → status 만이 차이 = 비지도 분리 불가(주장 확정).
"""
import os
import sys
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
import feature_engineer as fe          # noqa: E402
import eval_anomaly as ev              # noqa: E402

INPUT = r"D:\JB-Pirate-King-AIS\preprocessed_all\ais-2017-08-02_preprocessed.csv"
MAX_MMSI = 2000
UNCOMMON = {2, 3, 7, 8, 11, 12}
_B = {n: i for i, n in enumerate(fe.BASE_FEATURES)}

train_seqs, eval_seqs = fe.load_raw_seqs(INPUT, MAX_MMSI, fe.EVAL_NORMAL_RATIO)
si = _B["status"]

# 정상 중 '전 구간 비통상 status' (= FN4 형태) 시퀀스
legit_uncommon = [s for s in eval_seqs
                  if all(int(round(r[si])) in UNCOMMON for r in s)]
# 정상 전체(대조)
print(f"정상 전체 {len(eval_seqs):,} | 전구간-비통상 정상 {len(legit_uncommon):,}")

# FN4 합성 시퀀스 1000개 (평가와 동일 생성기)
import random
random.seed(777)
fn4 = [ev.make_fn_nav_status_seq() for _ in range(1000)]
# make_*_seq 는 _build_derived 로 BASE_FEATURES 정렬 행 리스트 반환
print(f"FN4 합성 {len(fn4):,}\n")

def feat_stats(seqs, fi):
    vals = np.array([r[fi] for s in seqs for r in s], dtype=float)
    return vals.mean(), vals.std(), np.percentile(vals, 5), np.percentile(vals, 95)

# status 제외한 운동학 피처 비교
print(f"{'feature':<16}{'│ legit-uncommon (mean±sd [p5,p95])':<42}{'│ FN4 (mean±sd [p5,p95])':<42}")
print("─" * 100)
compare_feats = ["sog", "cog", "heading", "dt", "dist_km", "cog_hdg_diff",
                 "sog_change", "cog_hdg_change", "speed_consistency",
                 "lat_speed", "lon_speed"]
for f in compare_feats:
    fi = _B[f]
    if legit_uncommon:
        lm, ls, l5, l95 = feat_stats(legit_uncommon, fi)
        lstr = f"{lm:8.3f}±{ls:7.3f} [{l5:7.2f},{l95:7.2f}]"
    else:
        lstr = "(none)"
    am, asd, a5, a95 = feat_stats(fn4, fi)
    astr = f"{am:8.3f}±{asd:7.3f} [{a5:7.2f},{a95:7.2f}]"
    print(f"{f:<16}│ {lstr:<40}│ {astr:<40}")

# 핵심: heading == cog 인 비율 (FN4 합성 artifact 의심)
def frac_hdg_eq_cog(seqs):
    n = tot = 0
    for s in seqs:
        for r in s:
            tot += 1
            if abs(r[_B["heading"]] - r[_B["cog"]]) < 1.0:
                n += 1
    return n / max(tot, 1)

print("\n── 합성 artifact 점검 (heading≈cog 비율) ──")
print(f"  legit-uncommon 정상 : {100*frac_hdg_eq_cog(legit_uncommon):.1f}%" if legit_uncommon else "  (none)")
print(f"  FN4 합성            : {100*frac_hdg_eq_cog(fn4):.1f}%")
print("\n해석: 운동학 피처 분포가 겹치고 heading≈cog 비율도 유사하면, status 외 구별신호")
print("      부재 → 비지도 분리 불가 확정. FN4 합성이 heading≈cog 100%면 그것은")
print("      '합성 artifact'일 뿐 실제 공격 탐지신호가 아님(과적합 위험).")
