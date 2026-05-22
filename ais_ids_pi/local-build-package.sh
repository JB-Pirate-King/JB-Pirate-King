export OCPN_TARGET=noble
export BUILD_GTK3=true
export WX_VER=32
export LOCAL_DEPLOY=true

# NTFS 경로에서 WSL로 실행 시 libonnxruntime.so symlink가 text 파일로 저장됨 → native EXT4에 복사
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ONNX_NATIVE="/tmp/ais_ids_onnxruntime"
if [ ! -f "${ONNX_NATIVE}/lib/libonnxruntime.so.1.20.1" ]; then
    echo "⏳ ONNX Runtime를 WSL native 경로로 복사 중..."
    rm -rf "${ONNX_NATIVE}"
    mkdir -p "${ONNX_NATIVE}/lib" "${ONNX_NATIVE}/include"
    cp -r "${SCRIPT_DIR}/onnxruntime/include/." "${ONNX_NATIVE}/include/"
    cp "${SCRIPT_DIR}/onnxruntime/lib/libonnxruntime.so.1.20.1" "${ONNX_NATIVE}/lib/"
    cp "${SCRIPT_DIR}/onnxruntime/lib/libonnxruntime_providers_shared.so" "${ONNX_NATIVE}/lib/"
    ln -sf libonnxruntime.so.1.20.1 "${ONNX_NATIVE}/lib/libonnxruntime.so.1"
    ln -sf libonnxruntime.so.1.20.1 "${ONNX_NATIVE}/lib/libonnxruntime.so"
    echo "✅ ONNX Runtime 준비: ${ONNX_NATIVE}"
fi

# this removes old xml files from the build directory
rm -f *.xml
rm -rf build
mkdir build
cd build
# the actual configuration, build and installable package creation
cmake -DCMAKE_BUILD_TYPE=Debug \
      -DONNXRUNTIME_DIR="${ONNX_NATIVE}" \
      ..
make -j$(($(nproc) / 2))
make package
chmod a+x cloudsmith-upload.sh
./cloudsmith-upload.sh

# 플러그인 data 파일을 OpenCPN 사용자 디렉터리에 설치
DATA_DEST="$HOME/.opencpn/plugins/ais_ids_pi/data"
mkdir -p "$DATA_DEST"
cp -r ../data/. "$DATA_DEST/"
echo "✅ data 파일 설치 완료: $DATA_DEST"

