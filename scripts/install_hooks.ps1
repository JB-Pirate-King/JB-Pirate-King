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
echo "[pre-push] C++ 검사 중..."

# cmake 빌드 디렉토리가 있으면 실제 빌드로 검사
BUILD_DIRS="build build_debug ais_ids_pi/build"
BUILD_FOUND=0
for bd in $BUILD_DIRS; do
    if [ -f "$bd/CMakeCache.txt" ]; then
        echo "  빌드 디렉토리 발견: $bd — cmake --build 실행"
        cmake --build "$bd" --target ais_ids_pi -- -j4 2>&1
        if [ $? -ne 0 ]; then
            echo "  ❌ C++ 빌드 실패"
            FAILED=1
        else
            echo "  ✅ C++ 빌드 성공"
        fi
        BUILD_FOUND=1
        break
    fi
done

# 빌드 디렉토리 없으면 cppcheck fallback
if [ $BUILD_FOUND -eq 0 ]; then
    if command -v cppcheck >/dev/null 2>&1; then
        echo "  빌드 디렉토리 없음 — cppcheck 정적 분석으로 대체"
        cppcheck \
            --enable=warning,style,performance \
            --suppress=missingIncludeSystem \
            --suppress=missingInclude \
            --suppress=unknownMacro \
            --error-exitcode=1 \
            --std=c++17 \
            -I ais_ids_pi/include \
            ais_ids_pi/src/ais_ml.cpp \
            ais_ids_pi/src/ais_ids.cpp \
            ais_ids_pi/src/ais_ids_pi.cpp \
            2>&1
        if [ $? -ne 0 ]; then
            echo "  ❌ cppcheck 오류 발견"
            FAILED=1
        else
            echo "  ✅ cppcheck 통과"
        fi
    else
        echo "  ⚠ cppcheck 없음, cmake 빌드 디렉토리도 없음 — C++ 검사 건너뜀"
        echo "    (cppcheck 설치: https://cppcheck.sourceforge.io)"
    fi
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
Write-Host "  2. C++ 빌드 검사"
Write-Host "     - cmake 빌드 디렉토리(build/ 등)가 있으면 cmake --build"
Write-Host "     - 없으면 cppcheck 정적 분석으로 대체"
Write-Host ""
Write-Host "cppcheck 설치 (없는 경우):"
Write-Host "  Windows: https://cppcheck.sourceforge.io 또는 winget install cppcheck"
