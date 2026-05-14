# pre-push hook 설치 스크립트
# 실행: .\scripts\install_hooks.ps1

$hooksDir = ".git\hooks"
$hookFile = "$hooksDir\pre-push"

$hookContent = @'
#!/bin/sh
# pre-push hook: Python 문법 검사 후 실패 시 push 차단

echo "[pre-push] Python 문법 검사 중..."

FAILED=0
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

if [ $FAILED -ne 0 ]; then
    echo ""
    echo "[pre-push] ❌ 문법 오류가 있습니다. push가 취소됐습니다."
    echo "           오류를 수정한 후 다시 push 하세요."
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
Write-Host "   이제 git push 전에 자동으로 Python 문법 검사가 실행됩니다."
