#!/usr/bin/env python3
"""약세 시나리오 ↔ 정상 분포 중첩 검증 (audit 주장 독립 확인).

audit 에이전트 주장: D1-LowSlow(20.9%)·anchor-move(5.37%) 형태가 정상 트래픽에
흔함 → distribution_overlap. 이를 홀드아웃 정상 데이터로 직접 측정한다.
각 시나리오 '형태'를 정의하고, 그 형태에 부합하는 정상 시퀀스 비율을 센다.
부합률이 높을수록 비지도 분리 불가(공격=정상 중첩).
"""
import os
import sys
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
import feature_engineer as fe  # noqa: E402

INPUT = r"D:\JB-Pirate-King-AIS\preprocessed_all\ais-2017-08-02_preprocessed.csv"
MAX_MMSI = 2000
_B = {n: i for i, n in enumerate(fe.BASE_FEATURES)}

_, eval_seqs = fe.load_raw_seqs(INPUT, MAX_MMSI, fe.EVAL_NORMAL_RATIO)
N = len(eval_seqs)
print(f"홀드아웃 정상 시퀀스 N = {N:,}\n")

def seq_arr(seq):
    return np.asarray(seq, dtype=float)

def frac(pred):
    c = sum(1 for s in eval_seqs if pred(seq_arr(s)))
    return c, 100.0 * c / N

# 시나리오 형태 정의 (생성기 파라미터 기반, 전 구간 또는 평균 기준)
checks = {
    # FN4: 전 구간 비통상 status {2,3,7,8,11,12}
    "FN4-status (전구간 비통상status)":
        lambda a: all(int(round(v)) in {2,3,7,8,11,12} for v in a[:, _B["status"]]),
    # anchor-move: status=1(정박) 인데 sog>1.5kn (정박 중 이동)
    "anchor-move (status=1 & 평균sog>1.5)":
        lambda a: (np.all(np.round(a[:, _B["status"]]) == 1)) and (a[:, _B["sog"]].mean() > 1.5),
    # anchor-move 완화: status∈{1,5} 이면서 평균 sog>1.5
    "  └ 완화(status∈{1,5} & 평균sog>1.5)":
        lambda a: np.all(np.isin(np.round(a[:, _B["status"]]), [1,5])) and (a[:, _B["sog"]].mean() > 1.5),
    # D1-LowSlow: 저속(sog<2) & 큰 cog-hdg 오프셋(>50)
    "D1-LowSlow (평균sog<2 & 평균cog_hdg_diff>50)":
        lambda a: (a[:, _B["sog"]].mean() < 2.0) and (a[a[:, _B["cog_hdg_diff"]] >= 0][:, _B["cog_hdg_diff"]].mean() > 50
                                                       if np.any(a[:, _B["cog_hdg_diff"]] >= 0) else False),
    # FN3-COG경계: cog_hdg_diff 가 수직(85~99) 부근
    "FN3-COG경계 (평균cog_hdg_diff 85~99)":
        lambda a: 85 <= a[a[:, _B["cog_hdg_diff"]] >= 0][:, _B["cog_hdg_diff"]].mean() <= 99
                  if np.any(a[:, _B["cog_hdg_diff"]] >= 0) else False,
}

print(f"{'시나리오 형태':<46}{'부합 정상수':>12}{'비율':>9}")
print("─" * 68)
for label, pred in checks.items():
    c, pct = frac(pred)
    flag = "  ← 높음(중첩)" if pct >= 1.0 else ""
    print(f"{label:<46}{c:>12,}{pct:>8.2f}%{flag}")

print("\n해석: 부합률이 ≳1%면 해당 '공격 형태'가 정상에 흔함 → 비지도 재구성으로")
print("      분리 불가(FP=1%에서 공격을 잡으려면 그만큼의 정상도 오탐). 즉 약세는")
print("      피처 부족이 아니라 시나리오-정상 분포 중첩 때문.")
