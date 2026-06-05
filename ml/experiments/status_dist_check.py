#!/usr/bin/env python3
"""정상 AIS 데이터의 status 코드 분포 검증.

FN4-status 공격은 status ∈ {2,3,7,8,11,12} 를 '비통상=이상'으로 가정한다.
그러나 실제 정상 트래픽에 이 코드들이 흔하다면, 비지도 재구성 모델은
이를 이상으로 분리할 수 없다(정상=공격 분포 중첩). 이를 데이터로 검증.
"""
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
import feature_engineer as fe  # noqa: E402

INPUT = r"D:\JB-Pirate-King-AIS\preprocessed_all\ais-2017-08-02_preprocessed.csv"
MAX_MMSI = 2000
UNCOMMON = {2, 3, 7, 8, 11, 12}     # FN4 공격이 사용하는 코드
COMMON = {0, 1, 5}                  # 코드가 '통상'으로 가정하는 코드

# 캐시된 홀드아웃 정상 시퀀스 로드 (학습/평가와 동일 분포)
train_seqs, eval_seqs = fe.load_raw_seqs(INPUT, MAX_MMSI, fe.EVAL_NORMAL_RATIO)
si = fe.BASE_FEATURES.index("status")

# 1) 행(타임스텝) 단위 status 분포 — 홀드아웃 정상
row_counter = Counter()
seq_has_uncommon = 0
seq_all_uncommon = 0
for seq in eval_seqs:
    codes = [int(round(row[si])) for row in seq]
    for c in codes:
        row_counter[c] += 1
    if any(c in UNCOMMON for c in codes):
        seq_has_uncommon += 1
    if all(c in UNCOMMON for c in codes):
        seq_all_uncommon += 1

total_rows = sum(row_counter.values())
n_seq = len(eval_seqs)
print(f"\n=== 홀드아웃 정상 시퀀스 status 분포 (n_seq={n_seq:,}, n_rows={total_rows:,}) ===")
print(f"{'status':>6} {'count':>12} {'pct':>8}  비고")
labels = {0: "underway", 1: "anchored", 2: "not-under-cmd", 3: "restricted",
          4: "constrained", 5: "moored", 6: "aground", 7: "fishing",
          8: "sailing", 11: "towing", 12: "pushing", 15: "undefined"}
for code, cnt in sorted(row_counter.items()):
    tag = "← FN4 공격코드" if code in UNCOMMON else ("(통상)" if code in COMMON else "")
    print(f"{code:>6} {cnt:>12,} {100*cnt/total_rows:>7.3f}%  {labels.get(code,'?'):<14} {tag}")

uncommon_rows = sum(c for k, c in row_counter.items() if k in UNCOMMON)
print(f"\n── 핵심 지표 ──")
print(f"비통상(2,3,7,8,11,12) 행 비율 : {100*uncommon_rows/total_rows:.3f}%  ({uncommon_rows:,}/{total_rows:,})")
print(f"비통상 코드를 1개 이상 포함한 정상 시퀀스 : {100*seq_has_uncommon/n_seq:.3f}%  ({seq_has_uncommon:,}/{n_seq:,})")
print(f"전 구간 비통상인 정상 시퀀스(=FN4 형태)   : {100*seq_all_uncommon/n_seq:.3f}%  ({seq_all_uncommon:,}/{n_seq:,})")
print("\n해석: '전 구간 비통상' 정상 시퀀스가 존재/흔하면, FN4(전 구간 비통상 status)는")
print("      정상 분포와 중첩 → 비지도 재구성 모델이 원리적으로 분리 불가.")
