# pre-push hook 설치 스크립트
# 실행: .\scripts\install_hooks.ps1

$hooksDir = ".git\hooks"
$hookFile = "$hooksDir\pre-push"

$hookContent = @'
#!/bin/sh
# pre-push hook: Python 문법 + C++ 빌드/정적분석 검사 후 실패 시 push 차단

FAILED=0

# ── 1. Python 문법 검사 ───────────────────────────────────────────
echo "[pre-push] Python 문법 검사 중..."
for f in ml/preprocess.py ml/train_benchmark.py ml/train_supervised.py ml/eval_anomaly.py; do
    if [ -f "$f" ]; then
        python -m py_compile "$f" 2>&1
        if [ $? -ne 0 ]; then
            echo "  ❌ 문법 오류: $f"
            FAILED=1
        else
            echo "  ✅ $f"
        fi
    fi
done

# ── 2. C++ 빌드 검사 ─────────────────────────────────────────────
echo ""
echo "[pre-push] C++ 빌드 검사 중..."

BUILD_SCRIPT="ais_ids_pi/local-build-package.sh"

if [ ! -f "$BUILD_SCRIPT" ]; then
    echo "  ⚠ $BUILD_SCRIPT 없음 — C++ 빌드 검사 건너뜀"
else
    # local-build-package.sh 환경변수 그대로 사용
    export OCPN_TARGET=noble
    export BUILD_GTK3=true
    export WX_VER=32
    export LOCAL_DEPLOY=true

    cd ais_ids_pi

    # 빌드 디렉토리 재사용 (증분 빌드, rm -rf 안 함)
    mkdir -p build
    cd build

    echo "  cmake 구성 중..."
    cmake -DCMAKE_BUILD_TYPE=Debug .. > /tmp/cmake_out.txt 2>&1
    if [ $? -ne 0 ]; then
        echo "  ❌ cmake 구성 실패:"
        cat /tmp/cmake_out.txt
        FAILED=1
    else
        echo "  ✅ cmake 구성 완료"
        echo "  make 빌드 중..."
        make -j$(($(nproc) / 2)) > /tmp/make_out.txt 2>&1
        if [ $? -ne 0 ]; then
            echo "  ❌ 빌드 실패:"
            tail -30 /tmp/make_out.txt
            FAILED=1
        else
            echo "  ✅ 빌드 성공"
        fi
    fi

    cd ../..
fi

# ── 결과 ─────────────────────────────────────────────────────────
echo ""
if [ $FAILED -ne 0 ]; then
    echo "[pre-push] ❌ 검사 실패. push가 취소됐습니다."
    exit 1
fi

echo "[pre-push] ✅ 모든 검사 통과 — push 진행합니다."
exit 0
'@

if (-not (Test-Path $hooksDir)) {
    Write-Host "❌ .git/hooks 디렉토리를 찾을 수 없습니다. 저장소 루트에서 실행하세요."
    exit 1
}

Set-Content -Path $hookFile -Value $hookContent -Encoding UTF8 -NoNewline

# Git은 hooks 파일이 실행 가능해야 함 (Git Bash / WSL 환경)
if (Get-Command git -ErrorAction SilentlyContinue) {
    git update-index --chmod=+x $hookFile 2>$null
}

# bash로 실행 권한 부여 시도
if (Get-Command bash -ErrorAction SilentlyContinue) {
    bash -c "chmod +x .git/hooks/pre-push"
}

Write-Host "✅ pre-push hook 설치 완료: $hookFile"
Write-Host ""
Write-Host "검사 항목:"
Write-Host "  1. Python 문법 검사 (ml/*.py)"
Write-Host "  2. C++ 빌드 검사 (ais_ids_pi/local-build-package.sh 기반)"
Write-Host "     - cmake 구성 + make (패키지/업로드 제외, 증분 빌드)"
