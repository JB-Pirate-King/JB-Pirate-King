#!/usr/bin/env bash
# package-postprocess.sh — CPack 산출 tar.gz 를 OpenCPN "Import Plugin" 가능하게 보정.
#
# make package(CPack TGZ) 의 두 가지 누락을 채운다:
#   ① metadata.xml 을 tar 루트(topdir)에 주입
#        → 없으면 OpenCPN Import 가 "Error extracting metadata from tarball" 로 거부.
#   ② onnxruntime soname 링크 체인(.so → .so.MAJOR → .so.X.Y.Z) 복원
#        → CPack 이 중간 .so.MAJOR 심볼릭 링크를 빠뜨려 dlopen 이
#          "libonnxruntime.so.1: cannot open shared object file" 로 실패하는 것 방지.
#
# 사용법: package-postprocess.sh <tarball.tar.gz> [metadata.xml]
#   metadata.xml 생략 시 ① 건너뜀(②만 수행).

set -euo pipefail

TARBALL="${1:?사용법: $0 <tarball.tar.gz> [metadata.xml]}"
META_XML="${2:-}"

[[ -f "$TARBALL" ]] || { echo "ERROR: tarball 없음: $TARBALL" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[postprocess] ── tar.gz 보정: $(basename "$TARBALL") ──"
tar xzf "$TARBALL" -C "$WORK"
TOPDIR="$(ls "$WORK")"   # 예: ais_ids_pi-1.0.358.1-ubuntu-x86_64-24.04

# ── ① metadata.xml 주입 ──────────────────────────────────────────────
if [[ -n "$META_XML" && -f "$META_XML" ]]; then
    cp "$META_XML" "$WORK/$TOPDIR/metadata.xml"
    echo "  + metadata.xml  ($(basename "$META_XML") → $TOPDIR/metadata.xml)"
else
    echo "  ! metadata.xml 미지정/없음 — Import 가 거부될 수 있음" >&2
fi

# ── ② onnxruntime soname 링크 체인 복원 ──────────────────────────────
LIBDIR="$WORK/$TOPDIR/usr/local/lib/opencpn"
if [[ -d "$LIBDIR" ]]; then
    (
        cd "$LIBDIR"
        REAL="$(ls libonnxruntime.so.[0-9]*.[0-9]*.[0-9]* 2>/dev/null | head -1 || true)"
        if [[ -n "$REAL" ]]; then
            MAJ="$(echo "$REAL" | grep -oP '(?<=libonnxruntime\.so\.)\d+')"
            ln -sf "$REAL" "libonnxruntime.so.$MAJ"
            ln -sf "libonnxruntime.so.$MAJ" libonnxruntime.so
            echo "  + soname 링크: libonnxruntime.so → libonnxruntime.so.$MAJ → $REAL"
        else
            echo "  · onnxruntime 실 SO 없음 — 링크 보정 건너뜀"
        fi
    )
fi

# ── 재압축 (원본 tar.gz 덮어씀) ──────────────────────────────────────
( cd "$WORK" && tar czf "$TARBALL" "$TOPDIR" )
echo "[postprocess] 완료: $TARBALL"
