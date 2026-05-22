# JB-Pirate-King ML Pipeline v2
# - D드라이브 기반 (데이터/결과 모두 D:\)
# - 단계별 Discord 보고 (1시간+ 작업은 20%마다, 단계 완료시 항상)
# - 11개 비지도 알고리즘 (OCSVM 제외, DeepSVDD/TimesNet/DAGMM 신규)
# - 하드웨어 80% 활용 (SAMPLE_MMSI=6000, batch=2048, workers=10)

$ML_DIR     = "C:\ccit\JB-Pirate-King\ml"
$D_DATA     = "D:\JB-Pirate-King-AIS\preprocessed"
$D_OUT      = "D:\JB-Pirate-King-ML-Results"
$LOG        = "$D_OUT\pipeline.log"
$TOTAL_STEPS = 4   # 전처리 / 소규모테스트 / 전체학습 / 완료

New-Item -ItemType Directory -Force -Path $D_OUT | Out-Null

$STEP_START = Get-Date
$PIPELINE_START = Get-Date

function Write-Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line -Encoding UTF8
}

function Elapsed($from) {
    $min = [math]::Round(((Get-Date) - $from).TotalMinutes, 1)
    return "${min}분"
}

function EstRemain($from, $done, $total) {
    $elapsed = ((Get-Date) - $from).TotalMinutes
    if ($done -le 0) { return "추정불가" }
    $perItem = $elapsed / $done
    $remain  = [math]::Round($perItem * ($total - $done), 0)
    return "${remain}분"
}

function Notify($msg, $title) {
    try { & python -u "$ML_DIR\notify.py" $msg $title 2>$null } catch {}
}

function NotifyProgress($currentStep, $stepName, $stepPct, $totalPct, $elapsed, $estRemain) {
    $body = @"
[단계 $currentStep/$TOTAL_STEPS] $stepName
단계 진행률: $stepPct%  |  전체 진행률: $totalPct%
경과 시간: $elapsed  |  잔여 예상: $estRemain
"@
    Notify $body "JB-Pirate-King | $stepName"
}

$env:PYTHONUNBUFFERED = "1"
Set-Location $ML_DIR

Write-Log "=== Pipeline v2 시작 ==="
Notify "파이프라인 v2 시작! 총 $TOTAL_STEPS 단계. D드라이브 기반." "JB-Pirate-King | 시작"


# ──────────────────────────────────────────────
# STEP 1: 전처리 (D:\JB-Pirate-King-AIS\preprocessed 확인/실행)
# ──────────────────────────────────────────────
$STEP = 1
$STEP_START = Get-Date
Write-Log "=== STEP $STEP/$TOTAL_STEPS: 전처리 ==="

# AIS 원본 CSV 경로 (D:\AIS\연도\)
$AIS_YEARS = @("2015","2016","2017","2018","2019","2020","2021","2022","2023","2024","2025")
$TARGET_CSV = 341   # 2015~2025년 1월 전체

$preCount = (Get-ChildItem "$D_DATA\*_preprocessed.csv" -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Log "현재 전처리 완료: $preCount / $TARGET_CSV"

if ($preCount -lt $TARGET_CSV) {
    Notify "전처리 시작! 현재 $preCount/$TARGET_CSV 파일. D드라이브 병렬 처리(10 workers)" "JB-Pirate-King | 전처리 시작"

    # parallel_preprocess.py를 D드라이브 대상으로 실행
    $AIS_DIRS = ($AIS_YEARS | ForEach-Object { "D:\AIS\$_" } | Where-Object { Test-Path $_ }) -join " "
    & python -u "$ML_DIR\parallel_preprocess.py" --output "$D_DATA" --workers 10 $AIS_DIRS.Split(" ") 2>&1 |
        Tee-Object -FilePath "$D_OUT\preprocess.log" -Append

    $preCount = (Get-ChildItem "$D_DATA\*_preprocessed.csv" -ErrorAction SilentlyContinue | Measure-Object).Count
    Notify "전처리 완료! $preCount/$TARGET_CSV 파일  |  소요: $(Elapsed $STEP_START)" "JB-Pirate-King | 전처리 완료"
} else {
    Write-Log "전처리 이미 완료 ($preCount 파일) -- 스킵"
}
Write-Log "STEP $STEP 완료: $(Elapsed $STEP_START)"


# ──────────────────────────────────────────────
# STEP 2: 소규모 테스트 (SAMPLE_MMSI=500, epochs=10)
# ──────────────────────────────────────────────
$STEP = 2
$STEP_START = Get-Date
Write-Log "=== STEP $STEP/$TOTAL_STEPS: 소규모 알고리즘 테스트 (MMSI=500, 10epochs) ==="
Notify "소규모 테스트 시작! 11개 알고리즘 x 10 epochs. 탐지율 비교 후 최적 조합 선정." "JB-Pirate-King | 소규모 테스트"

$TEST_OUT = "$D_OUT\test_run"
New-Item -ItemType Directory -Force -Path $TEST_OUT | Out-Null

& python -u "$ML_DIR\train_benchmark.py" --model all --input "$D_DATA" `
    --epochs 10 --batch_size 512 2>&1 | Tee-Object -FilePath "$D_OUT\test_benchmark.log" -Append

$exitTest = $LASTEXITCODE
if ($exitTest -eq 0) {
    Write-Log "소규모 테스트 완료: $(Elapsed $STEP_START)"

    # eval_all.py로 탐지율 비교
    Notify "소규모 학습 완료! 탐지율 비교 실행 중..." "JB-Pirate-King | 탐지율 비교"
    & python -u "$ML_DIR\eval_all.py" --model-dir "$D_OUT" --data-dir "$D_DATA" `
        2>&1 | Tee-Object -FilePath "$D_OUT\eval_all.log" -Append

    $evalResult = Get-Content "$D_OUT\eval_all.log" -ErrorAction SilentlyContinue | Select-String "최적 조합|Best ensemble" | Select-Object -Last 3
    Notify "탐지율 비교 완료!`n$evalResult`n소요: $(Elapsed $STEP_START)" "JB-Pirate-King | 탐지율 완료"
} else {
    Write-Log "소규모 테스트 오류 (exit=$exitTest)"
    Notify "소규모 테스트 오류 (exit=$exitTest). 로그 확인 필요." "JB-Pirate-King | 오류"
}
Write-Log "STEP $STEP 완료: $(Elapsed $STEP_START)"


# ──────────────────────────────────────────────
# STEP 3: 전체 데이터 본 학습
# ──────────────────────────────────────────────
$STEP = 3
$STEP_START = Get-Date
Write-Log "=== STEP $STEP/$TOTAL_STEPS: 전체 학습 (SAMPLE_MMSI=6000, batch=2048) ==="
Notify "전체 학습 시작! SAMPLE_MMSI=6000, batch=2048. 11개 모델 순차 학습." "JB-Pirate-King | 전체 학습 시작"

# 소규모 테스트 결과물 초기화 (재학습 유도)
Get-ChildItem "$D_OUT\model_*.onnx","$D_OUT\model_*.pt" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

& python -u "$ML_DIR\train_benchmark.py" --model all --input "$D_DATA" 2>&1 |
    Tee-Object -FilePath "$D_OUT\train_full.log" -Append

$exitFull = $LASTEXITCODE
$elapsedFull = Elapsed $STEP_START

if ($exitFull -eq 0) {
    Write-Log "전체 학습 완료: $elapsedFull"
    $onnxFiles = Get-ChildItem "$D_OUT\*.onnx" | Measure-Object
    Notify "전체 학습 완료! ONNX 모델 $($onnxFiles.Count)개 저장`n소요: $elapsedFull" "JB-Pirate-King | 학습 완료"
} else {
    Write-Log "전체 학습 오류 (exit=$exitFull)"
    Notify "전체 학습 오류 발생 (exit=$exitFull). 로그 확인 필요." "JB-Pirate-King | 오류"
}
Write-Log "STEP $STEP 완료: $elapsedFull"


# ──────────────────────────────────────────────
# STEP 4: 최종 eval + 정리
# ──────────────────────────────────────────────
$STEP = 4
Write-Log "=== STEP $STEP/$TOTAL_STEPS: 최종 평가 & 정리 ==="

& python -u "$ML_DIR\eval_all.py" --model-dir "$D_OUT" --data-dir "$D_DATA" `
    2>&1 | Tee-Object -FilePath "$D_OUT\eval_final.log" -Append

$totalElapsed = Elapsed $PIPELINE_START
$onnxList = (Get-ChildItem "$D_OUT\*.onnx" | ForEach-Object { $_.Name }) -join ", "

Write-Log "=== 전체 파이프라인 완료 ==="
Write-Log "총 소요: $totalElapsed"
Write-Log "ONNX 모델: $onnxList"
Notify @"
전체 파이프라인 완료!
총 소요: $totalElapsed
모델: $onnxList
결과 위치: $D_OUT
"@ "JB-Pirate-King | 전체 완료"
