#!/usr/bin/env python3
# coding: utf-8
"""
eval_rule_gen.py
────────────────
aivdm_gen.py 의 AttackPlugin 시뮬레이터를 직접 구동해서
룰 기반 탐지율(각 공격 플러그인)과 오탐율(정상 선박)을 측정합니다.

사용법:
    python eval_rule_gen.py                     # 전체 결과
    python eval_rule_gen.py --ticks 200         # 틱 수 조정 (기본 300)
    python eval_rule_gen.py --dt 10             # 초/틱 (기본 10)
    python eval_rule_gen.py --vessels 20        # 선박 수 override
    python eval_rule_gen.py --normal_n 5000     # 정상 선박 수 (기본 5000)
"""
from __future__ import annotations
import argparse, importlib.util, math, os, random, sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

# ──────────────────────────────────────────────────────────
#  aivdm_gen.py 동적 임포트
# ──────────────────────────────────────────────────────────
_GEN_PATH = Path(__file__).parent.parent / "aivdm_gen" / "aivdm_gen.py"
if not _GEN_PATH.exists():
    sys.exit(f"[오류] aivdm_gen.py 를 찾을 수 없습니다: {_GEN_PATH}")

spec = importlib.util.spec_from_file_location("aivdm_gen", _GEN_PATH)
_gen = importlib.util.module_from_spec(spec)
# tkinter GUI 임포트를 억제: 더미 tk 모듈 주입
import types, unittest.mock as _mock
_mk = _mock.MagicMock

_tk_dummy = types.ModuleType("tkinter")
# tkinter 가 사용하는 모든 이름을 MagicMock 으로 채움
for _attr in ("Tk","Toplevel","Frame","Canvas","Label","Button","Scale",
              "Checkbutton","Entry","Text","Listbox","Scrollbar","Menu",
              "BooleanVar","DoubleVar","IntVar","StringVar",
              "PanedWindow","LabelFrame","OptionMenu","Spinbox",
              "PhotoImage","N","S","E","W","NE","NW","SE","SW",
              "LEFT","RIGHT","TOP","BOTTOM","CENTER","BOTH","X","Y",
              "WORD","DISABLED","NORMAL","END","LAST","HORIZONTAL","VERTICAL",
              "SUNKEN","RAISED","FLAT","RIDGE","GROOVE","SOLID",
              "filedialog","messagebox","scrolledtext","ttk",
              "font","colorchooser"):
    setattr(_tk_dummy, _attr, _mk())

_tk_dummy.ttk = types.ModuleType("ttk")
for _attr in ("Frame","LabelFrame","Notebook","PanedWindow","Combobox",
              "Entry","Label","Button","Scale","Scrollbar","Treeview",
              "Separator","Progressbar","Style"):
    setattr(_tk_dummy.ttk, _attr, _mk())

_tk_dummy.scrolledtext = types.ModuleType("scrolledtext")
_tk_dummy.scrolledtext.ScrolledText = _mk()
_tk_dummy.filedialog = types.ModuleType("filedialog")
_tk_dummy.messagebox = types.ModuleType("messagebox")

sys.modules["tkinter"]             = _tk_dummy
sys.modules["tkinter.ttk"]         = _tk_dummy.ttk
sys.modules["tkinter.filedialog"]  = _tk_dummy.filedialog
sys.modules["tkinter.messagebox"]  = _tk_dummy.messagebox
sys.modules["tkinter.scrolledtext"]= _tk_dummy.scrolledtext

sys.modules["aivdm_gen"] = _gen   # dataclass __module__ 조회 전에 등록
spec.loader.exec_module(_gen)
AttackPlugin  = _gen.AttackPlugin
AttackRegistry= _gen.AttackRegistry
Vessel        = _gen.Vessel
_KN_TO_DPS    = _gen._KN_TO_DPS
_PROFILES     = _gen._PROFILES
_MID_POOL     = _gen._MID_POOL

# ──────────────────────────────────────────────────────────
#  Haversine
# ──────────────────────────────────────────────────────────
def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def _angle_diff(a, b) -> float:
    """두 각도 사이 최소 차 (0~180)"""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d

# ──────────────────────────────────────────────────────────
#  룰 정의  (eval_anomaly.py 의 RULES 와 동일)
# ──────────────────────────────────────────────────────────
def _rule_anchor_move(prev: dict, cur: dict) -> bool:
    """정박/계류(nav=1 or 5) 중 SOG ≥ 0.5"""
    if cur["nav"] in (1, 5):
        if cur["sog"] >= 0.5 and prev["sog"] >= 0.5:
            return True
    return False

def _rule_cog_hdg(prev: dict, cur: dict) -> bool:
    """COG/HDG 불일치 > 90°, SOG ≥ 2.0"""
    if cur["sog"] < 2.0:
        return False
    if cur["cog"] < 360.0 and cur["hdg"] < 511:
        diff = _angle_diff(cur["cog"], cur["hdg"])
        if diff > 90:
            return True
    return False

def _rule_overspeed(prev: dict, cur: dict) -> bool:
    """물리적 속도 초과 > 50 kn"""
    return cur["sog"] < 102.2 and cur["sog"] > 50.0

def _rule_sog_spike(prev: dict, cur: dict) -> bool:
    """순간 SOG 급변 > 15 kn, dt < 120s"""
    dt = cur["dt"]
    if dt <= 0 or dt >= 120.0:
        return False
    return abs(cur["sog"] - prev["sog"]) > 15.0

def _rule_speed_dist_mismatch(prev: dict, cur: dict) -> bool:
    """속도-거리 불일치: SOG와 실제 이동거리가 10배 이상 차이 (양방향)
    - SOG 높은데 실제 이동 없음 (rep >> act)
    - SOG 낮은데 실제 이동 큼 → 위치 점프 탐지 (act >> rep)
    """
    dt = cur["dt"]
    if dt <= 0:
        return False
    rep_dist_km = cur["sog"] * 1.852 / 3600.0 * dt   # SOG → km
    act_dist_km = cur.get("dist_km", 0.0)
    # 둘 다 매우 작으면 판단 불가 (정박 상태)
    if rep_dist_km < 0.001 and act_dist_km < 0.001:
        return False
    hi = max(rep_dist_km, act_dist_km)
    lo = min(rep_dist_km, act_dist_km)
    if lo < 0.0001:
        lo = 0.0001
    return hi / lo > 10.0

def _rule_signal_gap(prev: dict, cur: dict) -> bool:
    """신호 간격 이상 > 600s"""
    return cur["dt"] > 600.0

def _rule_zero_sog_moving(prev: dict, cur: dict) -> bool:
    """SOG ≈ 0 이지만 실제 이동 > 0.05 km, dt < 120s"""
    dt = cur["dt"]
    if dt <= 0 or dt >= 120.0:
        return False
    if cur["sog"] >= 0.3:
        return False
    return cur.get("dist_km", 0.0) > 0.05

RULES = [
    ("정박/계류 중 이동",       _rule_anchor_move),
    ("COG/HDG 불일치(>90°)",    _rule_cog_hdg),
    ("물리적 속도 초과(>50kn)", _rule_overspeed),
    ("순간 SOG 급변(>15kn)",    _rule_sog_spike),
    ("속도-거리 불일치(×10)",   _rule_speed_dist_mismatch),
    ("신호 간격 이상(>600s)",   _rule_signal_gap),
    ("SOG=0 실제이동",          _rule_zero_sog_moving),
]
RULE_NAMES = [r[0] for r in RULES]

# ──────────────────────────────────────────────────────────
#  선박 상태 스냅샷
# ──────────────────────────────────────────────────────────
def _snap(v: Vessel, elapsed: float, dt: float,
          prev_lat: float, prev_lon: float) -> dict:
    dist_km = _haversine_km(prev_lat, prev_lon, v.lat, v.lon)
    return dict(
        sog=v.sog, cog=v.cog % 360, hdg=v.hdg % 512,
        nav=v.nav, dt=dt, dist_km=dist_km,
        lat=v.lat, lon=v.lon,
    )

def _apply_rules(prev: dict, cur: dict) -> list[bool]:
    return [fn(prev, cur) for _, fn in RULES]

# ──────────────────────────────────────────────────────────
#  플러그인 시뮬레이션
# ──────────────────────────────────────────────────────────
def simulate_plugin(plugin: AttackPlugin, ticks: int, dt: float,
                    vessel_override: int | None) -> dict:
    """
    플러그인 한 번 실행 → 전체 Vessel × tick 에 대해 룰 적용.
    반환: {
        "vessels": int,
        "ticks":   int,
        "rule_hit": [bool] * len(RULES),   # 각 룰이 1회라도 발동했는지 (전체 선박 중 any)
        "any_hit":  bool,
    }
    """
    cfg = {
        "clat": 37.45, "clon": 126.70,
        "count": vessel_override or 10,
    }
    fleet = plugin.make(cfg)

    # 초기 스냅 (prev용)
    prev_snaps: dict[int, dict] = {}
    for v in fleet:
        prev_snaps[v.mmsi] = dict(
            sog=v.sog, cog=v.cog % 360, hdg=v.hdg % 512,
            nav=v.nav, dt=dt, dist_km=0.0,
            lat=v.lat, lon=v.lon,
        )

    rule_any = [False] * len(RULES)  # 전체 (vessel × tick) 중 발동 여부

    for tick in range(ticks):
        elapsed = (tick + 1) * dt
        # 위치 저장 (before update)
        prev_pos = {v.mmsi: (v.lat, v.lon) for v in fleet}

        plugin.update(fleet, elapsed, dt, cfg)

        for v in fleet:
            plat, plon = prev_pos[v.mmsi]
            cur = _snap(v, elapsed, dt, plat, plon)
            prev = prev_snaps[v.mmsi]
            hits = _apply_rules(prev, cur)
            for i, h in enumerate(hits):
                if h:
                    rule_any[i] = True
            prev_snaps[v.mmsi] = cur

    return {
        "vessels":  len(fleet),
        "ticks":    ticks,
        "rule_hit": rule_any,
        "any_hit":  any(rule_any),
    }

# ──────────────────────────────────────────────────────────
#  정상 선박 시뮬레이션 (오탐율 측정용)
# ──────────────────────────────────────────────────────────
def simulate_normal(n_vessels: int, ticks: int, dt: float) -> dict:
    """
    정상 선박 n_vessels 척을 ticks 틱 동안 시뮬레이션.
    반환: {
        "rule_fp_count": [int]*len(RULES),   # 각 룰이 발동된 (vessel,tick) 쌍 수
        "any_fp_count":  int,                # any 룰 발동된 vessel 수
        "total_vessels": int,
    }
    """
    clat, clon = 37.45, 126.70

    # per-vessel: 1회라도 발동했는지 여부 카운트
    rule_fp_vessels = [0] * len(RULES)
    any_fp_vessels = 0

    for _ in range(n_vessels):
        prof = random.choice(_PROFILES)
        mmsi = random.choice(_MID_POOL) * 1_000_000 + random.randint(100_000, 999_999)
        v = Vessel(mmsi, "NRM")
        v.lat = clat + random.uniform(-0.15, 0.15)
        v.lon = clon + random.uniform(-0.18, 0.18)
        v.sog = random.uniform(prof.sog_lo, prof.sog_hi)
        v.cog = random.uniform(0, 360)
        v.hdg = int((v.cog + random.uniform(-10, 10)) % 360)
        v.nav = random.choice(prof.nav_opts)

        prev = dict(sog=v.sog, cog=v.cog, hdg=v.hdg, nav=v.nav,
                    dt=dt, dist_km=0.0, lat=v.lat, lon=v.lon)

        vessel_rule_hit = [False] * len(RULES)
        for tick in range(ticks):
            plat, plon = v.lat, v.lon
            # 정상 이동: sog 소폭 변동, cog 소폭 변동
            v.sog = max(0.1, v.sog + random.uniform(-0.3, 0.3))
            v.sog = min(v.sog, prof.sog_hi + 0.5)
            v.cog = (v.cog + random.uniform(-5, 5)) % 360
            v.hdg = int((v.cog + random.uniform(-8, 8)) % 360)
            # 가끔 정박 전환 (정박 중 SOG=0 유지)
            if random.random() < 0.001:
                v.nav = 1 if v.nav == 0 else 0
            if v.nav == 1:
                v.sog = 0.0
            # _step 과 동일 이동
            s = v.sog * _KN_TO_DPS * dt
            v.lat += math.cos(math.radians(v.cog)) * s
            v.lon += math.sin(math.radians(v.cog)) * s * 1.2

            cur = _snap(v, (tick + 1) * dt, dt, plat, plon)
            hits = _apply_rules(prev, cur)
            for i, h in enumerate(hits):
                if h:
                    vessel_rule_hit[i] = True
            prev = cur

        for i, h in enumerate(vessel_rule_hit):
            if h:
                rule_fp_vessels[i] += 1
        if any(vessel_rule_hit):
            any_fp_vessels += 1

    return {
        "rule_fp_count": rule_fp_vessels,   # per-vessel: 1회라도 발동
        "any_fp_count":  any_fp_vessels,
        "total_vessels": n_vessels,
    }

# ──────────────────────────────────────────────────────────
#  메인
# ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="aivdm_gen 룰 탐지/오탐율 평가")
    parser.add_argument("--ticks",     type=int,   default=300, help="틱 수 (기본 300)")
    parser.add_argument("--dt",        type=float, default=10,  help="초/틱 (기본 10)")
    parser.add_argument("--vessels",   type=int,   default=None,help="선박 수 override")
    parser.add_argument("--normal_n",  type=int,   default=5000,help="정상 선박 수 (기본 5000)")
    parser.add_argument("--trials",    type=int,   default=10,  help="플러그인 반복 시행 수 (기본 10)")
    args = parser.parse_args()

    TICKS     = args.ticks
    DT        = args.dt
    VESSELS   = args.vessels
    NORMAL_N  = args.normal_n
    TRIALS    = args.trials

    print()
    print("=" * 70)
    print("  aivdm_gen 플러그인 → 룰 기반 탐지율 / 오탐율 평가")
    print(f"  틱={TICKS}  dt={DT}s  시행={TRIALS}  정상선박={NORMAL_N}")
    print("=" * 70)

    # ── 정상 오탐율 ───────────────────────────────────────
    print(f"\n[1] 정상 선박 {NORMAL_N}척 시뮬레이션 중 (오탐율)...", flush=True)
    fp_result = simulate_normal(NORMAL_N, TICKS, DT)
    fp_counts = fp_result["rule_fp_count"]
    total_v   = fp_result["total_vessels"]
    any_fp    = fp_result["any_fp_count"]

    print()
    print("  ┌─ 오탐율 ──────────────────────────────────────────────────")
    print(f"  │  정상 선박: {total_v:,}척  (틱={TICKS}, dt={DT}s)")
    print(f"  │")
    col_w = max(len(n) for n in RULE_NAMES)
    for i, name in enumerate(RULE_NAMES):
        pct = fp_counts[i] / total_v * 100
        print(f"  │  {name:<{col_w}}  발동 {fp_counts[i]:5d}회  ({pct:.2f}% 선박 중 any)")
    any_pct = any_fp / total_v * 100
    print(f"  │  {'(합산 any)':<{col_w}}  {any_fp:5d}척  ({any_pct:.2f}%)")
    print("  └──────────────────────────────────────────────────────────")

    # ── 공격 플러그인 탐지율 ──────────────────────────────
    plugins = AttackRegistry.all()
    print(f"\n[2] 공격 플러그인 {len(plugins)}개 평가 중...", flush=True)

    # 결과 저장: {key: {"label", "trials", "rule_hit_sum": [], "any_hit_sum"}}
    results = {}
    for plugin in plugins:
        key   = plugin.meta.key
        label = plugin.meta.label
        rule_hit_sum = [0] * len(RULES)
        any_hit_sum  = 0

        for trial in range(TRIALS):
            try:
                r = simulate_plugin(plugin, TICKS, DT, VESSELS)
            except Exception as e:
                print(f"  [!] {label} 시행{trial+1} 오류: {e}")
                continue
            for i, h in enumerate(r["rule_hit"]):
                if h:
                    rule_hit_sum[i] += 1
            if r["any_hit"]:
                any_hit_sum += 1

        results[key] = {
            "label":        label,
            "rule_hit_sum": rule_hit_sum,
            "any_hit_sum":  any_hit_sum,
            "category":     plugin.meta.category,
        }
        any_pct = any_hit_sum / TRIALS * 100
        print(f"  {label:<40s}  탐지 {any_pct:5.1f}%  ({any_hit_sum}/{TRIALS})", flush=True)

    # ── 상세 결과 테이블 ──────────────────────────────────
    print()
    print("=" * 70)
    print("  [결과] 룰별 탐지율 상세")
    print("=" * 70)

    # 헤더
    rule_short = ["정박이동", "COG/HDG", "과속(>50)", "SOG급변", "속도거리불일치", "신호간격", "SOG0이동"]
    rw = 10
    hdr = f"  {'플러그인':<40s}"
    for rs in rule_short:
        hdr += f"  {rs:>{rw}}"
    hdr += f"  {'any':>{rw}}"
    print(hdr)
    print("  " + "─" * (40 + (len(RULES) + 1) * (rw + 2)))

    for plugin in plugins:
        key = plugin.meta.key
        r   = results.get(key)
        if not r:
            continue
        row = f"  {r['label']:<40s}"
        for i in range(len(RULES)):
            pct = r["rule_hit_sum"][i] / TRIALS * 100
            row += f"  {pct:>{rw}.0f}%"
        any_pct = r["any_hit_sum"] / TRIALS * 100
        row += f"  {any_pct:>{rw}.0f}%"
        print(row)

    # ── 요약 테이블 (카테고리별) ──────────────────────────
    print()
    print("=" * 70)
    print("  [요약] 카테고리별 any 탐지율")
    print("=" * 70)
    from collections import defaultdict
    cat_results = defaultdict(list)
    for plugin in plugins:
        key = plugin.meta.key
        r   = results.get(key)
        if not r:
            continue
        cat_results[r["category"]].append((r["label"], r["any_hit_sum"] / TRIALS * 100))

    for cat in sorted(cat_results.keys()):
        print(f"\n  카테고리 {cat}:")
        for label, pct in cat_results[cat]:
            bar = "#" * int(pct / 5)
            print(f"    {label:<40s}  {pct:5.1f}%  {bar}")

    print()
    print("  오탐율 요약 (정상 선박 중 해당 룰이 1회라도 발동된 선박 비율):")
    for i, name in enumerate(RULE_NAMES):
        pct = fp_counts[i] / total_v * 100
        print(f"    {name:<30s}  {pct:.2f}%")
    print(f"    {'(any 룰 발동)':<30s}  {any_fp/total_v*100:.2f}%")
    print()


if __name__ == "__main__":
    main()
