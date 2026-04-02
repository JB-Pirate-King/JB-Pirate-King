export OCPN_TARGET=noble
export BUILD_GTK3=true
export WX_VER=32
export LOCAL_DEPLOY=true
# this removes old xml files from the build directory
rm *.xml
rm -rf build
mkdir build
cd build
# the actual configuration, build and installable package creation
cmake -DCMAKE_BUILD_TYPE=Debug ..
make -j$(($(nproc) / 2))
make package
chmod a+x cloudsmith-upload.sh
./cloudsmith-upload.sh

