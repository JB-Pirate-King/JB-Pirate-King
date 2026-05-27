#!/usr/bin/env bash
# build_plugin_wsl.sh — WSL 내에서 OpenCPN 플러그인 빌드
#
# 사용법:
#   wsl -d Ubuntu-24.04 bash /mnt/c/.../ml/build_plugin_wsl.sh <repo_wsl_path>
#
# 성공 시 마지막 줄에 생성된 tar.gz 의 WSL 경로를 출력한다.
# Python 에서는 이 줄을 파싱해 Windows 경로로 변환한다.

set -euo pipefail

REPO_WSL="${1:?사용법: $0 <repo_wsl_path>}"
PLUGIN_SRC="$REPO_WSL/ais_ids_pi"
DIST_DIR="$PLUGIN_SRC"           # tar.gz 복사 목적지 (레포 내)

BUILD_BASE="$HOME/wsl_build"
BUILD_SRC="$BUILD_BASE/ais_ids_pi"

# ── 1. 소스 복사 ─────────────────────────────────────────────────────
echo "[WSL Build] ── 소스 복사 ──────────────────────────────────────"
echo "[WSL Build] repo : $REPO_WSL"
echo "[WSL Build] build: $BUILD_SRC"
mkdir -p "$BUILD_BASE"
rm -rf "$BUILD_SRC"
cp -rp "$PLUGIN_SRC" "$BUILD_BASE/"

# ── 2. ONNX 심볼릭 링크 복원 ─────────────────────────────────────────
# NTFS 에서는 symlink 가 텍스트 파일로 저장되므로, WSL native fs 로 복사 후
# 직접 ln -sf 로 재생성해야 cmake 가 올바른 SO 를 링크할 수 있다.
echo "[WSL Build] ── ONNX 심볼릭 링크 복원 ─────────────────────────"
ONNX_LIB="$BUILD_SRC/onnxruntime/lib"
pushd "$ONNX_LIB" > /dev/null

# 실제 SO 파일: libonnxruntime.so.X.Y.Z 형태 (세 자리 버전)
REAL_SO=""
for f in libonnxruntime.so.[0-9]*.[0-9]*.[0-9]*; do
    [[ -f "$f" ]] && REAL_SO="$f" && break
done
if [[ -z "$REAL_SO" ]]; then
    echo "ERROR: libonnxruntime.so.X.Y.Z 파일을 찾을 수 없음" >&2
    exit 1
fi

# major version 추출 (예: libonnxruntime.so.1.20.1 → 1)
MAJOR=$(echo "$REAL_SO" | grep -oP '(?<=libonnxruntime\.so\.)\d+')
SO_MAJ="libonnxruntime.so.$MAJOR"

echo "  실제 SO : $REAL_SO"
rm -f libonnxruntime.so "$SO_MAJ"
ln -sf "$REAL_SO" "$SO_MAJ"
ln -sf "$SO_MAJ"  libonnxruntime.so
echo "  링크 생성: libonnxruntime.so → $SO_MAJ → $REAL_SO"
popd > /dev/null

# ── 3. cmake / make / make package ───────────────────────────────────
echo "[WSL Build] ── cmake / make / package ─────────────────────────"
BUILD_DIR="$BUILD_SRC/build"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

cmake -DCMAKE_BUILD_TYPE=Release ..

NPROC=$(nproc)
JOBS=$(( NPROC > 2 ? NPROC / 2 : 1 ))
echo "  병렬 빌드: -j${JOBS}  (논리 코어 ${NPROC}개)"
make -j"$JOBS"
make package

# ── 4. tar.gz → 레포 복사 ────────────────────────────────────────────
echo "[WSL Build] ── tar.gz 복사 → 레포 ─────────────────────────────"
TARBALL=$(ls "$BUILD_DIR"/*.tar.gz 2>/dev/null | sort -V | tail -1)
if [[ -z "$TARBALL" ]]; then
    echo "ERROR: tar.gz 파일 없음 (make package 실패?)" >&2
    exit 1
fi

TARBALL_NAME=$(basename "$TARBALL")
cp "$TARBALL" "$DIST_DIR/$TARBALL_NAME"
echo "[WSL Build] 완료: $DIST_DIR/$TARBALL_NAME"

# 마지막 줄 = 파일 경로 (Python 에서 캡처)
echo "$DIST_DIR/$TARBALL_NAME"
