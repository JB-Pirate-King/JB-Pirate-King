# JB-Pirate-King Phase 2 Auto Orchestrator (ASCII-safe, UTF-8 BOM)
# Runs after run_pipeline_v3.ps1 completes automatically.
#
# TASK 1: Scaling analysis  -- small(1k MMSI) / 5yr Jan / 11yr Jan comparison
# TASK 2: Full-month ensemble -- download all months, preprocess,
#          train TranAD+DCdetector with FPR=1% threshold, run eval

$ML_DIR     = "C:\ccit\JB-Pirate-King\ml"
$D_RESULTS  = "D:\JB-Pirate-King-ML-Results"
$D_PREPROC  = "D:\JB-Pirate-King-AIS\preprocessed"
$D_AIS      = "D:\AIS"
$D_ALL_PRE  = "D:\JB-Pirate-King-AIS\preprocessed_all"
$D_ENSEMBLE = "$D_RESULTS\ensemble_full"
$MAX_RETRY  = 3
$P2_LOG     = "$D_RESULTS\phase2_auto.log"
$PHASE_START = Get-Date

$env:PYTHONUNBUFFERED = "1"
Set-Location $ML_DIR

New-Item -ItemType Directory -Force -Path $D_RESULTS  | Out-Null
New-Item -ItemType Directory -Force -Path $D_ALL_PRE  | Out-Null
New-Item -ItemType Directory -Force -Path $D_ENSEMBLE | Out-Null


function WLog {
    param([string]$msg)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Write-Host $line
    Add-Content -Path $P2_LOG -Value $line -Encoding UTF8
}

function Elapsed { param($from)
    $min = [math]::Round(((Get-Date) - $from).TotalMinutes, 1)
    return "${min}min"
}

function Notify { param([string]$msg, [string]$title = "JB-Pirate-King | Phase2")
    try { & python -u "$ML_DIR\notify.py" $msg $title 2>$null } catch {}
}

function RunWithRetry {
    param([string[]]$Cmd, [string]$LogPath, [string]$Desc, [int]$Retries = $MAX_RETRY)
    for ($i = 1; $i -le $Retries; $i++) {
        WLog "  [$Desc] attempt $i/$Retries"
        Add-Content -Path $LogPath -Value "`n===== $Desc attempt $i | $(Get-Date -Format 'HH:mm:ss') =====" -Encoding UTF8
        & $Cmd[0] @($Cmd[1..($Cmd.Count-1)]) 2>&1 | Tee-Object -FilePath $LogPath -Append
        if ($LASTEXITCODE -eq 0) { WLog "  [$Desc] OK"; return $true }
        WLog "  [$Desc] FAILED exit=$LASTEXITCODE (attempt $i/$Retries)"
        Notify "[$Desc] error exit=$LASTEXITCODE attempt $i/$Retries auto-retry..." "JB | Error"
        if ($i -lt $Retries) { Start-Sleep -Seconds 30 }
    }
    WLog "  [$Desc] all $Retries attempts failed -- continuing"
    Notify "[$Desc] max retries exceeded. Moving on." "JB | MaxRetry"
    return $false
}


# =============================================================================
# STEP 0: Wait for v3 pipeline completion
# =============================================================================
WLog "=== Phase 2 Auto Orchestrator START ==="
WLog "=== STEP 0: Waiting for v3 pipeline ==="
Notify "Phase 2 waiting for v3 pipeline to finish..." "JB | Phase2 Wait"

$waitStart = Get-Date
while ($true) {
    $logLines  = Get-Content "$D_RESULTS\pipeline_v3.log" -ErrorAction SilentlyContinue
    $allDone   = ($logLines -match "ALL DONE").Count -gt 0
    $onnxCount = @(Get-ChildItem "$D_RESULTS\model_*.onnx" -ErrorAction SilentlyContinue).Count
    $pyRunning = @(Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
                   Where-Object { $_.Name -like "*python*" -and
                                  $_.CommandLine -like "*train_benchmark*" }).Count -gt 0

    if ($allDone -or ($onnxCount -ge 7 -and -not $pyRunning)) {
        WLog "v3 complete detected! onnx=$onnxCount allDone=$allDone"
        break
    }

    $waitMin = [math]::Round(((Get-Date) - $waitStart).TotalMinutes, 0)
    if ($waitMin % 30 -eq 0 -and $waitMin -gt 0) {
        Notify "Phase2 waiting ${waitMin}min... onnx=$onnxCount py=$pyRunning" "JB | Phase2 Wait"
    }
    WLog "  waiting ${waitMin}min | onnx=$onnxCount py=$pyRunning"
    Start-Sleep -Seconds 300
}
WLog "STEP 0 done: waited $(Elapsed $waitStart)"
Notify "v3 complete! Phase 2 starting. onnx=$onnxCount" "JB | Phase2 Start"


# =============================================================================
# TASK 1: Data scaling comparison (small / 5yr / 11yr)
# =============================================================================
$T_START = Get-Date
WLog "=== TASK 1: Data scaling comparison ==="
Notify "TASK 1 start: small(1k) vs 5yr Jan vs 11yr Jan detection rate comparison" "JB | T1 Start"

$t1_ok = RunWithRetry `
    -Cmd @("python", "-u", "$ML_DIR\scaling_compare.py") `
    -LogPath "$D_RESULTS\task1_scaling.log" `
    -Desc "TASK1-scaling"

if ($t1_ok) {
    $r = (Get-Content "$D_RESULTS\scaling_compare_result.txt" -ErrorAction SilentlyContinue |
          Select-Object -First 20) -join "`n"
    WLog "TASK 1 done: $(Elapsed $T_START)"
    Notify "TASK 1 done! ($(Elapsed $T_START))`n`n$r" "JB | T1 Done"
} else {
    WLog "TASK 1 FAILED -- continuing to TASK 2"
    Notify "TASK 1 failed. Continuing to TASK 2." "JB | T1 Fail"
}


# =============================================================================
# TASK 2: Full-month data -- download / preprocess / ensemble train / eval
# =============================================================================
$T_START = Get-Date
WLog "=== TASK 2: Full-month ensemble ==="
Notify "TASK 2 start: all-month download + TranAD/DCdetector ensemble FPR=1%" "JB | T2 Start"


# ── 2-A: Download all months ──────────────────────────────────────────────────
WLog "  [2-A] Download all months"
$nonJanCount = @(Get-ChildItem "$D_AIS\*\ais-*-0[2-9]-*.csv" -ErrorAction SilentlyContinue |
                  Where-Object { $_.Length -gt 100KB }).Count
$nonJanCount += @(Get-ChildItem "$D_AIS\*\ais-*-1[0-2]-*.csv" -ErrorAction SilentlyContinue |
                   Where-Object { $_.Length -gt 100KB }).Count
WLog "  existing non-Jan CSV: $nonJanCount"

if ($nonJanCount -ge 3000) {
    WLog "  [2-A SKIP] non-Jan files already exist: $nonJanCount"
    Notify "All-month download already complete ($nonJanCount files) -- skip" "JB | 2A Skip"
} else {
    Notify "All-month download start! non-Jan existing=$nonJanCount workers=4" "JB | 2A Start"
    RunWithRetry `
        -Cmd @("python", "-u", "$ML_DIR\download_ais_allmonths.py", "--workers", "4") `
        -LogPath "$D_RESULTS\download_allmonths.log" `
        -Desc "2A-download" | Out-Null
    $nonJanCount = @(Get-ChildItem "$D_AIS\*\ais-*-0[2-9]-*.csv","$D_AIS\*\ais-*-1[0-2]-*.csv" `
                     -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 100KB }).Count
    WLog "  [2-A done] non-Jan CSV: $nonJanCount"
    Notify "All-month download done! non-Jan=$nonJanCount files | $(Elapsed $T_START)" "JB | 2A Done"
}


# ── 2-B: Preprocess all months ────────────────────────────────────────────────
WLog "  [2-B] Preprocess all months -> $D_ALL_PRE"
$totalCsv  = @(Get-ChildItem "$D_AIS\*\*.csv" -ErrorAction SilentlyContinue |
                Where-Object { $_.Length -gt 100KB }).Count
$preDone   = @(Get-ChildItem "$D_ALL_PRE\*_preprocessed.csv" -ErrorAction SilentlyContinue |
                Where-Object { $_.Length -gt 0 }).Count
WLog "  preprocess done=$preDone / target CSV=$totalCsv"

if ($preDone -ge $totalCsv -and $totalCsv -gt 341) {
    WLog "  [2-B SKIP] preprocess already done: $preDone"
    Notify "All-month preprocess already done ($preDone files) -- skip" "JB | 2B Skip"
} else {
    $aisYearDirs = @(2015..2025 | ForEach-Object { "$D_AIS\$_" } | Where-Object { Test-Path $_ })
    Notify "All-month preprocess start! target=$totalCsv workers=10" "JB | 2B Start"
    RunWithRetry `
        -Cmd (@("python", "-u", "$ML_DIR\parallel_preprocess.py",
                "--output", $D_ALL_PRE, "--workers", "10", "--input") + $aisYearDirs) `
        -LogPath "$D_RESULTS\preprocess_allmonths.log" `
        -Desc "2B-preprocess" | Out-Null
    $preDone = @(Get-ChildItem "$D_ALL_PRE\*_preprocessed.csv" -ErrorAction SilentlyContinue |
                  Where-Object { $_.Length -gt 0 }).Count
    WLog "  [2-B done] preprocessed: $preDone"
    Notify "All-month preprocess done! $preDone files | $(Elapsed $T_START)" "JB | 2B Done"
}


# ── 2-C: Train TranAD + DCdetector ensemble (FPR=1%) ─────────────────────────
WLog "  [2-C] Train TranAD+DCdetector ensemble (threshold-pct=99 -> FPR~1%)"
$cacheAll    = "$D_ENSEMBLE\train_data_cache.pt"
$tranadOnnx  = Test-Path "$D_ENSEMBLE\model_tranad.onnx"
$dcdetOnnx   = Test-Path "$D_ENSEMBLE\model_dcdetect.onnx"

if ($tranadOnnx -and $dcdetOnnx) {
    WLog "  [2-C SKIP] ensemble ONNX already exist"
    Notify "Ensemble ONNX already exist -- skip training" "JB | 2C Skip"
} else {
    if (Test-Path $cacheAll) { Remove-Item $cacheAll -Force; WLog "  old cache removed" }
    Notify "Ensemble train start! TranAD+DCdetector FPR=1% input=$D_ALL_PRE" "JB | 2C Start"
    $t2c_ok = RunWithRetry `
        -Cmd @("python", "-u", "$ML_DIR\train_benchmark.py",
               "--model",         "tranad,dcdetect",
               "--input",         $D_ALL_PRE,
               "--output",        $D_ENSEMBLE,
               "--cache",         $cacheAll,
               "--threshold-pct", "99") `
        -LogPath "$D_RESULTS\train_ensemble_full.log" `
        -Desc "2C-train"

    $onnxN = @(Get-ChildItem "$D_ENSEMBLE\model_*.onnx" -ErrorAction SilentlyContinue).Count
    if ($t2c_ok) {
        WLog "  [2-C done] onnx=$onnxN"
        Notify "Ensemble train done! onnx=$onnxN | $(Elapsed $T_START)" "JB | 2C Done"
    } else {
        WLog "  [2-C FAILED] onnx=$onnxN"
    }
}


# ── 2-D: Eval / simulation ────────────────────────────────────────────────────
WLog "  [2-D] Ensemble eval/simulation"
$t2d_ok = RunWithRetry `
    -Cmd @("python", "-u", "$ML_DIR\eval_all.py",
           "--model-dir", $D_ENSEMBLE,
           "--data-dir",  $D_ALL_PRE) `
    -LogPath "$D_RESULTS\eval_ensemble_full.log" `
    -Desc "2D-eval"

if ($t2d_ok) {
    $evalResult = (Get-Content "$D_ENSEMBLE\eval_summary.txt" -ErrorAction SilentlyContinue) -join "`n"
    WLog "  [2-D done]"
    Notify "Ensemble eval done!`n`n$evalResult`n`nElapsed: $(Elapsed $T_START)" "JB | 2D Done"
} else {
    WLog "  [2-D FAILED]"
}


# =============================================================================
# Final report
# =============================================================================
$totalEl = Elapsed $PHASE_START
WLog "=== Phase 2 ALL DONE | Total: $totalEl ==="

$scaleResult = (Get-Content "$D_RESULTS\scaling_compare_result.txt" -ErrorAction SilentlyContinue |
                Select-Object -First 15) -join "`n"
$ensResult   = (Get-Content "$D_ENSEMBLE\eval_summary.txt" -ErrorAction SilentlyContinue) -join "`n"

Notify @"
Phase 2 ALL DONE! Total: $totalEl

[TASK1] Scaling comparison:
$scaleResult

[TASK2] Ensemble (TranAD+DCdetector, FPR=1%):
$ensResult

Results: $D_RESULTS
"@ "JB-Pirate-King | Phase 2 COMPLETE"
