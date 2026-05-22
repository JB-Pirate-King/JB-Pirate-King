# JB-Pirate-King ML Pipeline Auto-runner (비지도 전용)
$ML_DIR = "C:\ccit\JB-Pirate-King\ml"
$LOG    = "$ML_DIR\pipeline.log"
$TARGET = 186

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line -Encoding UTF8
}

function Notify($msg, $title) {
    try {
        & python -u "$ML_DIR\notify.py" $msg $title 2>$null
    } catch {}
}

Set-Location $ML_DIR
$env:PYTHONUNBUFFERED = "1"

Write-Log "=== Pipeline monitor started ==="
Notify "파이프라인 감시 시작. 전처리 완료 대기 중..." "JB-Pirate-King"

# Stage 1: Wait for preprocessing
Write-Log "Waiting for preprocessing... target: $TARGET files"
$lastCount = 0
while ($true) {
    $count = (Get-ChildItem $ML_DIR -Filter "*_preprocessed.csv" | Measure-Object).Count
    if ($count -ne $lastCount) {
        Write-Log "Preprocessing: $count / $TARGET"
        $lastCount = $count
    }
    if ($count -ge $TARGET) { break }
    $pyProcs = (Get-Process | Where-Object {$_.ProcessName -like "*python*"} | Measure-Object).Count
    if ($pyProcs -eq 0 -and $count -gt 50) {
        Write-Log "Python procs ended. Files ready: $count / $TARGET"
        break
    }
    Start-Sleep -Seconds 30
}

$preprocessCount = (Get-ChildItem $ML_DIR -Filter "*_preprocessed.csv" | Measure-Object).Count
Write-Log "Preprocessing done: $preprocessCount files ready"
Notify "전처리 완료! $preprocessCount 개 파일 준비됨. 비지도 학습을 시작합니다." "JB-Pirate-King | 1단계 완료"

# Stage 2: Unsupervised benchmark (지도학습 제외)
Write-Log "=== Unsupervised benchmark training started ==="
Notify "비지도 학습 시작 (9개 모델: USAD, TranAD, Conv1D, LSTM, TCN, AnomalyTransformer, DCDetector, IForest, OCSVM)" "JB-Pirate-King | 2단계 시작"

Set-Location $ML_DIR
$t0 = Get-Date
& python -u "train_benchmark.py" --model all --input "." 2>&1 | Tee-Object -FilePath "$ML_DIR\train_benchmark.log" -Append
$exitCode = $LASTEXITCODE
$elapsed = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)

if ($exitCode -eq 0) {
    Write-Log "Unsupervised training done (${elapsed} min)"
    Notify "비지도 학습 완료! 소요: ${elapsed}분. 결과물 D드라이브로 이동 시작." "JB-Pirate-King | 비지도 완료"
} else {
    Write-Log "Unsupervised training ERROR (exit=$exitCode)"
    Notify "오류 발생! 비지도 학습 실패 (exit=$exitCode). train_benchmark.log 확인 필요." "JB-Pirate-King | 오류"
}

# Stage 3: 결과물 D드라이브로 이동
Write-Log "=== 결과물 D드라이브 이동 시작 ==="
$D_OUT = "D:\JB-Pirate-King-ML-Results"
New-Item -ItemType Directory -Force -Path $D_OUT | Out-Null

# ONNX 모델, 스케일러, 임계값, 캐시 이동
$moveItems = @(
    "$ML_DIR\*.onnx",
    "$ML_DIR\*.pt",
    "$ML_DIR\scaler_*.json",
    "$ML_DIR\threshold_*.txt",
    "$ML_DIR\train_data_cache.pt"
)
foreach ($pattern in $moveItems) {
    Get-ChildItem $pattern -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName $D_OUT -Force
        Write-Log "  복사: $($_.Name) --> $D_OUT"
    }
}

# 전처리된 CSV도 D드라이브로 (이미 없으면 skip)
$D_CSV = "D:\JB-Pirate-King-AIS\preprocessed"
New-Item -ItemType Directory -Force -Path $D_CSV | Out-Null
Get-ChildItem "$ML_DIR\*_preprocessed.csv" -ErrorAction SilentlyContinue | ForEach-Object {
    Move-Item $_.FullName $D_CSV -Force
    Write-Log "  이동: $($_.Name) --> $D_CSV"
}

Write-Log "=== 이동 완료 ==="
Notify "결과물 D드라이브 이동 완료! D:\JB-Pirate-King-ML-Results 확인." "JB-Pirate-King | 이동 완료"

# C드라이브 불필요 파일 정리
Write-Log "=== C드라이브 정리 시작 ==="
Remove-Item "$ML_DIR\*.onnx" -ErrorAction SilentlyContinue -Force
Remove-Item "$ML_DIR\scaler_*.json" -ErrorAction SilentlyContinue -Force
Remove-Item "$ML_DIR\threshold_*.txt" -ErrorAction SilentlyContinue -Force
Remove-Item "$ML_DIR\train_data_cache.pt" -ErrorAction SilentlyContinue -Force
Remove-Item "$ML_DIR\train_benchmark.log" -ErrorAction SilentlyContinue -Force
Write-Log "C드라이브 학습 임시파일 정리 완료"
Notify "C드라이브 정리 완료. 모든 결과물은 D:\JB-Pirate-King-ML-Results 에 저장됨." "JB-Pirate-King | 정리 완료"

# 최종 요약
$totalMin = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
Write-Log "=== ALL DONE === Total time: ${totalMin} min"
Write-Log "결과물 위치: $D_OUT"
Get-ChildItem $D_OUT | ForEach-Object { Write-Log "  $($_.Name) ($([math]::Round($_.Length/1MB,2)) MB)" }
Notify "전체 완료! 비지도 학습 ${elapsed}분 + 정리 = 총 ${totalMin}분. 결과: D:\JB-Pirate-King-ML-Results" "JB-Pirate-King | 전체 완료"
