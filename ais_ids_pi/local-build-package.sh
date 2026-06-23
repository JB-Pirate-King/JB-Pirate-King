export OCPN_TARGET=noble
export BUILD_GTK3=true
export WX_VER=32
export LOCAL_DEPLOY=true

# 빌드 전 C++ 를 배포 scaler(data/scaler.json) 피처에 맞춰 재패치.
# 미등록 피처가 있으면(--strict) 여기서 중단 → 입력 0 채움으로 망가진
# 플러그인이 빌드/릴리즈되는 것을 원천 차단. (C++-모델 불일치도 동시 해소)
if [ -f data/scaler.json ]; then
    python3 ../ml/core/patch_plugin.py --root .. --scaler data/scaler.json --strict \
        || { echo "❌ patch_plugin 실패(미등록 피처?) — 빌드 중단" >&2; exit 1; }
fi

# this removes old xml files from the build directory
rm *.xml
rm -rf build
mkdir build
cd build
# the actual configuration, build and installable package creation
cmake -DCMAKE_BUILD_TYPE=Debug ..
make -j$(($(nproc) / 2))
make package

# tar.gz 보정: metadata.xml 주입 + onnxruntime soname 링크 복원
# (OpenCPN Import + dlopen 이 그대로 동작하도록)
PKG_TARBALL=$(ls *.tar.gz 2>/dev/null | sort -V | tail -1)
PKG_META=$(ls *.xml 2>/dev/null | sort -V | tail -1)
if [[ -n "$PKG_TARBALL" ]]; then
    bash ../package-postprocess.sh "$PKG_TARBALL" "$PKG_META"
fi

chmod a+x cloudsmith-upload.sh
./cloudsmith-upload.sh

# 플러그인 data 파일을 OpenCPN 사용자 디렉터리에 설치
DATA_DEST="$HOME/.opencpn/plugins/ais_ids_pi/data"
mkdir -p "$DATA_DEST"
cp -r ../data/. "$DATA_DEST/"
echo "✅ data 파일 설치 완료: $DATA_DEST"

