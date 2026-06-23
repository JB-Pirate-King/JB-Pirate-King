#!/usr/bin/env python3
"""
patch_plugin.py — scaler.json features 배열 기반으로 C++ 플러그인 코드 자동 패치

수정 대상 (AUTO: 마커 구간 교체):
  ais_ids_pi/include/ais_ml.h   — ML_FEATURE_COUNT + PushFeature 선언
  ais_ids_pi/src/ais_ml.cpp     — PushFeature 구현
  ais_ids_pi/src/ais_ids.cpp    — 파생 피처 계산 + PushFeature 호출

사용법:
  python ml/core/patch_plugin.py --scaler ais_ids_pi/data/scaler_dcdetect.json
  python ml/core/patch_plugin.py --scaler ... --dry_run
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ── 베이스 피처 (고정 12개, 순서 불변) — ml/core/constants.py 가 단일 출처 ──
try:
    from ml.core.constants import BASE_FEATURES
except ModuleNotFoundError:
    try:                                         # cwd=ml (CI)
        from core.constants import BASE_FEATURES
    except ModuleNotFoundError:                  # `python ml/core/patch_plugin.py` (스크립트 dir = sys.path[0])
        from constants import BASE_FEATURES

BASE_FEAT_DESC = {
    "sog":              "속력 (knots)",
    "cog":              "진행 방향 (도)",
    "heading":          "선수 방향 (도)",
    "status":           "항행 상태",
    "dt":               "이전 신호와 시간 차이 (초)",
    "dist_km":          "실제 이동 거리 (km)",
    "cog_hdg_diff":     "COG vs HDG 차이 (도, -1=미정의)",
    "sog_change":       "이전 대비 SOG 변화량 (knots)",
    "cog_hdg_change":   "이전 대비 cog_hdg_diff 변화량",
    "speed_consistency":"실제 이동거리 / SOG 기반 예상 거리",
    "lat_speed":        "위도 방향 변화율 (도/초)",
    "lon_speed":        "경도 방향 변화율 (도/초)",
}

# ── 추가 피처별 C++ 정의 (desc, compute_code, param_decl) ────────────
# compute_code: ais_ids.cpp 에 삽입할 계산식 (들여쓰기 8칸 포함)
# param_decl  : 파라미터 타입+이름 (e.g. "float accel")
EXTRA_FEAT_CPP: dict = {
    "accel": (
        "가속도 (knots/s)",
        "        float accel = (dt > 0.0f) ? sog_change / dt : 0.0f;",
        "float accel",
    ),
    "heading_change": (
        "Heading 변화량 (도)",
        """\
        float heading_change = 0.0f;
        if (cur.hdg < 511 && prev.hdg < 511) {
            float _hd = std::abs((float)cur.hdg - (float)prev.hdg);
            heading_change = (_hd > 180.0f) ? 360.0f - _hd : _hd;
        }""",
        "float heading_change",
    ),
    "heading_rate": (
        "Heading 변화율 (도/초)",
        """\
        float heading_rate = 0.0f;
        if (cur.hdg < 511 && prev.hdg < 511) {
            float _hr = std::abs((float)cur.hdg - (float)prev.hdg);
            if (_hr > 180.0f) _hr = 360.0f - _hr;
            heading_rate = (dt > 0.0f) ? _hr / dt : 0.0f;
        }""",
        "float heading_rate",
    ),
    "vec_sog_diff": (
        "벡터SOG 차이 (knots)",
        """\
        float vec_sog_diff = 0.0f;
        {
            // lat/lon speed (도/초) → 노트 변환 (1도≈111320m, 1노트=1852m/h)
            float _gps = std::sqrt(lat_speed * lat_speed + lon_speed * lon_speed)
                         * 111320.0f * 3600.0f / 1852.0f;
            vec_sog_diff = std::abs(_gps - (float)cur.sog);
        }""",
        "float vec_sog_diff",
    ),
    "cog_change": (
        "COG 변화량 (도)",
        """\
        float cog_change = 0.0f;
        {
            float _cd = std::abs((float)cur.cog - (float)prev.cog);
            cog_change = (_cd > 180.0f) ? 360.0f - _cd : _cd;
        }""",
        "float cog_change",
    ),
    "turn_rate": (
        "COG 변화율 (도/초)",
        """\
        float turn_rate = 0.0f;
        {
            float _tr = std::abs((float)cur.cog - (float)prev.cog);
            if (_tr > 180.0f) _tr = 360.0f - _tr;
            turn_rate = (dt > 0.0f) ? _tr / dt : 0.0f;
        }""",
        "float turn_rate",
    ),
    "dist_speed_err": (
        "거리/속도 불일치 (km)",
        "        float dist_speed_err = std::abs(dist_km - (float)(cur.sog * dt / 3600.0 * 1.852));",
        "float dist_speed_err",
    ),
    "lowspeed_crab": (
        "저속 횡류 (도)",
        "        float lowspeed_crab = (cur.sog < 3.0f && cog_hdg_diff >= 0.0f) ? cog_hdg_diff : 0.0f;",
        "float lowspeed_crab",
    ),
    "cog_sin_hdg": (
        "COG-HDG sin 변환",
        "        float cog_sin_hdg = (cog_hdg_diff >= 0.0f) ? std::abs(std::sin(cog_hdg_diff * (float)M_PI / 180.0f)) : 0.0f;",
        "float cog_sin_hdg",
    ),
    "status_fn4_flag": (
        "FN4 비정상 상태 플래그 (현재 timestep 근사)",
        """\
        float status_fn4_flag = 0.0f;
        {
            int _st = (int)cur.navStatus;
            if (_st == 2 || _st == 3 || _st == 7 || _st == 8 || _st == 11 || _st == 12)
                status_fn4_flag = 1.0f;
        }""",
        "float status_fn4_flag",
    ),
    "hdg_perp_score": (
        "항로 수직 분량 스코어",
        "        float hdg_perp_score = (cog_hdg_diff >= 0.0f) ? std::abs(std::sin(cog_hdg_diff * (float)M_PI / 180.0f)) : 0.0f;",
        "float hdg_perp_score",
    ),
    "status_motion_flag": (
        "상태-운동 불일치 플래그",
        "        float status_motion_flag = ((int)cur.navStatus == 0 && cur.sog < 0.5f) ? 1.0f : 0.0f;",
        "float status_motion_flag",
    ),
    # ── dcdetect_004 채택 피처 (WSL 빌드 검증 완료) ──────────────────
    "anchor_motion": (
        "정박/계류(status 1,5)인데 이동거리 큼 — 정박이동·status 위장 포착",
        "        float anchor_motion = (cur.navStatus == 1 || cur.navStatus == 5) ? dist_km : 0.0f;",
        "float anchor_motion",
    ),
    "kinematic_speed_gap": (
        "거리/시간 실제속도와 보고 sog 괴리 — 위치 못 맞추는 sog 조작 포착",
        "        float kinematic_speed_gap = std::abs(dist_km / std::max(dt, 1e-6f) - (float)cur.sog);",
        "float kinematic_speed_gap",
    ),
    "heading_micro_jitter": (
        "sog_change 작은데 heading 미세 변동 — 정상모방 속 기수 떨림 노출",
        "        float heading_micro_jitter = std::abs((float)cur.hdg - (float)prev.hdg) / (1.0f + std::abs(sog_change));",
        "float heading_micro_jitter",
    ),
    "status_expected_speed_dev": (
        "status 함의 기대속도(정박/계류 1,5,6→0, 그 외→약5kn)와 sog 편차 — dense 신호",
        """\
        float status_expected_speed_dev;
        {
            int _st = (int)cur.navStatus;
            float _exp = (_st == 1 || _st == 5 || _st == 6) ? 0.0f : 5.0f;
            status_expected_speed_dev = std::abs((float)cur.sog - _exp);
        }""",
        "float status_expected_speed_dev",
    ),
}


# ── 마커 간 구간 교체 헬퍼 ───────────────────────────────────────────
def replace_between(text: str, begin_marker: str, end_marker: str, new_content: str) -> str:
    pattern = re.compile(
        re.escape(begin_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    replacement = begin_marker + "\n" + new_content + "\n" + end_marker
    result, n = pattern.subn(replacement, text)
    if n == 0:
        raise ValueError(f"마커 쌍을 찾을 수 없음: {begin_marker!r} … {end_marker!r}")
    return result


# ── 코드 생성 함수들 ─────────────────────────────────────────────────

def gen_feat_block(all_feats: list) -> str:
    """ais_ml.h [AUTO:feat_block] 구간 — 피처 주석 + #define"""
    n = len(all_feats)
    lines = [f"// 피처 순서 ({n}개):"]
    for i, f in enumerate(all_feats):
        if f in BASE_FEAT_DESC:
            desc = BASE_FEAT_DESC[f]
        elif f in EXTRA_FEAT_CPP:
            desc = EXTRA_FEAT_CPP[f][0]
        else:
            desc = f
        lines.append(f"//  {i:>2}  {f:<22}  {desc}")
    lines.append(f"#define ML_FEATURE_COUNT {n}")
    return "\n".join(lines)


def gen_push_decl(extra: list) -> str:
    """ais_ml.h [AUTO:push_decl] 구간 — PushFeature 선언"""
    # 항목 자체에 trailing comma 없음 — ",\n".join() 이 쉼표를 붙임
    base_params = [
        "                     float sog, float cog, float heading",
        "                     float status, float dt, float dist_km",
        "                     float cog_hdg_diff, float sog_change",
        "                     float cog_hdg_change",
        "                     float speed_consistency",
        "                     float lat_speed, float lon_speed",
    ]
    extra_params = [
        "                     " + EXTRA_FEAT_CPP[f][2]
        for f in extra if f in EXTRA_FEAT_CPP
    ]
    params_str = ",\n".join(base_params + extra_params)
    return f"    void PushFeature(int mmsi,\n{params_str});"


def gen_push_impl(extra: list) -> str:
    """ais_ml.cpp [AUTO:push_impl] 구간 — PushFeature 구현"""
    base_sig_params = [
        "                          float sog, float cog, float heading",
        "                          float status, float dt, float dist_km",
        "                          float cog_hdg_diff, float sog_change",
        "                          float cog_hdg_change",
        "                          float speed_consistency",
        "                          float lat_speed, float lon_speed",
    ]
    extra_sig_params = [
        "                          " + EXTRA_FEAT_CPP[f][2]
        for f in extra if f in EXTRA_FEAT_CPP
    ]
    sig_str = ",\n".join(base_sig_params + extra_sig_params)

    base_arr = [
        "        sog, cog, heading, status",
        "        dt, dist_km",
        "        cog_hdg_diff, sog_change",
        "        cog_hdg_change",
        "        speed_consistency",
        "        lat_speed, lon_speed",
    ]
    extra_arr = [f"        {f}" for f in extra if f in EXTRA_FEAT_CPP]
    arr_str = ",\n".join(base_arr + extra_arr)

    return (
        f"void AIS_ML::PushFeature(int mmsi,\n{sig_str})\n"
        "{\n"
        f"    std::array<float, ML_FEATURE_COUNT> feat = {{\n{arr_str}\n    }};\n"
        "    auto &seq = m_sequences[mmsi];\n"
        "    seq.push_back(feat);\n"
        "    if ((int)seq.size() > m_seq_len)\n"
        "        seq.pop_front();\n"
        "}"
    )


def gen_extra_feats(extra: list) -> str:
    """ais_ids.cpp [AUTO:extra_feats] 구간 — 파생 피처 계산식"""
    if not extra:
        return "        // 추가 파생 피처 없음 (베이스 12개만 사용)"
    parts = ["        // 추가 파생 피처 계산 (patch_plugin.py 자동 생성)"]
    for f in extra:
        if f in EXTRA_FEAT_CPP:
            parts.append(EXTRA_FEAT_CPP[f][1])
        else:
            parts.append(f"        // [UNKNOWN FEATURE: {f}]")
    return "\n".join(parts)


def gen_push_calls(extra: list) -> str:
    """ais_ids.cpp [AUTO:push_calls] 구간 — PushFeature 두 번 호출"""
    base_args = [
        "                (float)cur.sog, (float)cur.cog",
        "                (float)cur.hdg, (float)cur.navStatus",
        "                dt, dist_km",
        "                cog_hdg_diff, sog_change",
        "                cog_hdg_change",
        "                speed_consistency",
        "                lat_speed, lon_speed",
    ]
    extra_args = [f"                {f}" for f in extra if f in EXTRA_FEAT_CPP]
    args_str = ",\n".join(base_args + extra_args)

    return (
        "        // seq10 / seq5 모두 동일 피처 push (각자 deque 길이만 다름)\n"
        "        if (ais_ml->IsLoaded())\n"
        f"            ais_ml->PushFeature(target.mmsi,\n{args_str});\n"
        "\n"
        "        if (ais_ml_short->IsLoaded())\n"
        f"            ais_ml_short->PushFeature(target.mmsi,\n{args_str});"
    )


# ── 메인 패치 로직 ───────────────────────────────────────────────────

def patch_file(path: str, begin: str, end: str, new_content: str, dry_run: bool, label: str):
    text = Path(path).read_text(encoding="utf-8")
    patched = replace_between(text, begin, end, new_content)
    if dry_run:
        print(f"\n{'='*60}")
        print(f"  [DRY RUN] {label}  →  {path}")
        print(f"{'='*60}")
        # 변경된 구간만 출력
        start_idx = patched.find(begin)
        end_idx   = patched.find(end, start_idx) + len(end)
        print(patched[start_idx:end_idx])
    else:
        Path(path).write_text(patched, encoding="utf-8")
        print(f"  ✓  {label}")


def run(scaler_path: str, dry_run: bool, root: str = ".", strict: bool = False):
    # 피처 목록 로드
    with open(scaler_path, encoding="utf-8") as f:
        data = json.load(f)
    all_feats: list = data["features"]
    extra = [f for f in all_feats if f not in BASE_FEATURES]

    print(f"\n[patch_plugin] 피처 {len(all_feats)}개  (베이스 {len(BASE_FEATURES)} + 추가 {len(extra)})")
    if extra:
        print(f"  추가 피처: {extra}")
    else:
        print("  추가 피처 없음 — 베이스 12개 원복")

    # ── 미등록 피처 검사: EXTRA_FEAT_CPP 에 C++ 정의가 없으면 입력이 0 으로
    #    채워져 추론이 망가진다(조용한 손상). 시끄럽게 경고하고, strict 면 중단. ──
    unknown = [f for f in extra if f not in EXTRA_FEAT_CPP]
    if unknown:
        msg = (
            f"미등록 피처 {unknown} — EXTRA_FEAT_CPP 에 C++ 정의 없음.\n"
            "  → 플러그인이 해당 입력을 0 으로 채워 추론이 망가짐(배포 불가).\n"
            "  → ml/core/patch_plugin.py 의 EXTRA_FEAT_CPP 에 (desc, C++계산식, param_decl) 추가 필요."
        )
        if strict:
            raise ValueError("[patch_plugin] " + msg)
        print("\n" + "!"*64, file=sys.stderr)
        print("⚠️  [patch_plugin] " + msg, file=sys.stderr)
        print("!"*64 + "\n", file=sys.stderr)

    h_path   = str(Path(root) / "ais_ids_pi/include/ais_ml.h")
    cpp_path = str(Path(root) / "ais_ids_pi/src/ais_ml.cpp")
    ids_path = str(Path(root) / "ais_ids_pi/src/ais_ids.cpp")

    patch_file(h_path,   "// [AUTO:feat_block_begin]",  "// [AUTO:feat_block_end]",
               gen_feat_block(all_feats), dry_run, "ais_ml.h  — feat_block")
    patch_file(h_path,   "    // [AUTO:push_decl_begin]", "    // [AUTO:push_decl_end]",
               gen_push_decl(extra), dry_run, "ais_ml.h  — push_decl")
    patch_file(cpp_path, "// [AUTO:push_impl_begin]",   "// [AUTO:push_impl_end]",
               gen_push_impl(extra), dry_run, "ais_ml.cpp — push_impl")
    patch_file(ids_path, "        // [AUTO:extra_feats_begin]", "        // [AUTO:extra_feats_end]",
               gen_extra_feats(extra), dry_run, "ais_ids.cpp — extra_feats")
    patch_file(ids_path, "        // [AUTO:push_calls_begin]",  "        // [AUTO:push_calls_end]",
               gen_push_calls(extra), dry_run, "ais_ids.cpp — push_calls")

    if not dry_run:
        print(f"\n[patch_plugin] 완료 — {len(all_feats)}피처로 C++ 패치됨")


def main():
    ap = argparse.ArgumentParser(description="C++ 플러그인 피처 자동 패치")
    ap.add_argument("--scaler", required=True,
                    help="scaler_dcdetect.json 경로 (features 배열 기준)")
    ap.add_argument("--dry_run", action="store_true",
                    help="파일을 수정하지 않고 변경 내용만 출력")
    ap.add_argument("--root", default=".",
                    help="레포 루트 경로 (기본: 현재 디렉터리)")
    ap.add_argument("--strict", action="store_true",
                    help="미등록 피처(EXTRA_FEAT_CPP 부재)가 있으면 패치하지 않고 중단")
    args = ap.parse_args()

    try:
        run(args.scaler, args.dry_run, args.root, args.strict)
    except Exception as e:
        print(f"[patch_plugin] 오류: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
