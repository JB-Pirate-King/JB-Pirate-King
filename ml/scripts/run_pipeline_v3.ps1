# JB-Pirate-King ML Pipeline v3 (ASCII-safe, UTF-8 BOM)
$ML_DIR          = "C:\ccit\JB-Pirate-King\ml"
$D_AIS           = "D:\AIS"
$D_DATA          = "D:\JB-Pirate-King-AIS\preprocessed"
$D_OUT           = "D:\JB-Pirate-King-ML-Results"
$LOGFILE         = "D:\JB-Pirate-King-ML-Results\pipeline_v3.log"
$TOTAL_STEPS     = 3
$TARGET_CSV      = 341
$SELECTED        = "lstm,timesnet,usad,dcdetect,iforest,deepsvdd,dagmm"

New-Item -ItemType Directory -Force -Path $D_OUT  | Out-Null
New-Item -ItemType Directory -Force -Path $D_DATA | Out-Null

$PIPELINE_START = Get-Date
$STEP_START     = Get-Date

function WLog {
    param([string]$msg)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Write-Host $line
    Add-Content -Path "D:\JB-Pirate-King-ML-Results\pipeline_v3.log" -Value $line -Encoding UTF8
}

function Elapsed {
    param($from)
    $min = [math]::Round(((Get-Date) - $from).TotalMinutes, 1)
    return "${min}min"
}

function Notify {
    param([string]$msg, [string]$title)
    try {
        $py = "C:\ccit\JB-Pirate-King\ml\notify.py"
        Start-Process python -ArgumentList @("-u", $py, $msg, $title) `
            -WindowStyle Hidden -ErrorAction SilentlyContinue
    } catch {}
}

$env:PYTHONUNBUFFERED = "1"
Set-Location "C:\ccit\JB-Pirate-King\ml"

WLog "=== Pipeline v3 start: $SELECTED ==="
Notify "Pipeline v3 start! Models: $SELECTED" "JB-Pirate-King | Start"


# --- STEP 0 : Wait for download ---
WLog "=== STEP 0: Waiting for AIS download ==="
$dlWait = Get-Date
$lastDl = 0

while ($true) {
    $dlCount = (Get-ChildItem "D:\AIS" -Recurse -Filter "*.csv" -ErrorAction SilentlyContinue |
                Where-Object { $_.Length -gt 100MB } | Measure-Object).Count

    if ($dlCount -ne $lastDl) {
        WLog "  Download: $dlCount / 341"
        $lastDl = $dlCount
    }
    if ($dlCount -ge 341) { WLog "  Download complete: $dlCount"; break }

    $dlPy = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -like "*download_ais*" }
    if (-not $dlPy -and $dlCount -ge 200) {
        WLog "  No download process ($dlCount/341) -- proceeding"
        break
    }
    Start-Sleep -Seconds 60
}
WLog "STEP 0 done: $(Elapsed $dlWait) | $lastDl files"
Notify "Download done! $lastDl/341 files. Preprocessing next." "JB-Pirate-King | Download"


# --- STEP 1 : Preprocess ---
$STEP = 1; $STEP_START = Get-Date
WLog "=== STEP $STEP/$($TOTAL_STEPS): Preprocess ==="
$preCount = (Get-ChildItem "D:\JB-Pirate-King-AIS\preprocessed\*_preprocessed.csv" -ErrorAction SilentlyContinue | Measure-Object).Count
WLog "  Preprocessed: $preCount / 341"

if ($preCount -lt 341) {
    Notify "Preprocess start! $preCount/341. workers=10." "JB-Pirate-King | Preprocess"
    $dirs = @(2015..2025 | ForEach-Object { "D:\AIS\$_" } | Where-Object { Test-Path $_ })
    $preArgs = @("-u","C:\ccit\JB-Pirate-King\ml\parallel_preprocess.py","--output","D:\JB-Pirate-King-AIS\preprocessed","--workers","10","--input") + $dirs
    & python @preArgs 2>&1 | Tee-Object -FilePath "D:\JB-Pirate-King-ML-Results\preprocess_v3.log" -Append
    $preCount = (Get-ChildItem "D:\JB-Pirate-King-AIS\preprocessed\*_preprocessed.csv" -ErrorAction SilentlyContinue | Measure-Object).Count
    Notify "Preprocess done! $preCount/341. $(Elapsed $STEP_START)" "JB-Pirate-King | Pre Done"
} else {
    WLog "  Already done ($preCount files) -- skip"
}
WLog "STEP $STEP done: $(Elapsed $STEP_START)"
Notify "STEP $STEP/$TOTAL_STEPS Preprocess done. $(Elapsed $STEP_START)" "JB-Pirate-King | Step $STEP"


# --- STEP 2 : Full training ---
$STEP = 2; $STEP_START = Get-Date
WLog "=== STEP $STEP/$($TOTAL_STEPS): Full training ($SELECTED) ==="
Notify "Full training start! 7 models, MMSI=6000, batch=2048. $SELECTED" "JB-Pirate-King | Training"

Get-ChildItem "D:\JB-Pirate-King-ML-Results\model_*.onnx","D:\JB-Pirate-King-ML-Results\model_*.pt" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
$cache = "D:\JB-Pirate-King-ML-Results\train_data_cache.pt"
if (Test-Path $cache) { Remove-Item $cache -Force; WLog "  Old cache removed" }

& python -u "C:\ccit\JB-Pirate-King\ml\train_benchmark.py" --model $SELECTED --input "D:\JB-Pirate-King-AIS\preprocessed" 2>&1 |
    Tee-Object -FilePath "D:\JB-Pirate-King-ML-Results\train_full_v3.log" -Append

$exitFull = $LASTEXITCODE
$elFull   = Elapsed $STEP_START
$onnxN    = (Get-ChildItem "D:\JB-Pirate-King-ML-Results\model_*.onnx" -ErrorAction SilentlyContinue | Measure-Object).Count
if ($exitFull -eq 0) {
    WLog "Training done: $elFull | ONNX: $onnxN"
    Notify "Training done! ONNX $onnxN saved. $elFull" "JB-Pirate-King | Training Done"
} else {
    WLog "Training ERROR exit=$exitFull"
    Notify "Training ERROR! exit=$exitFull. Check train_full_v3.log" "JB-Pirate-King | Error"
}
WLog "STEP $STEP done: $elFull"


# --- STEP 3 : Final eval ---
$STEP = 3; $STEP_START = Get-Date
WLog "=== STEP $STEP/$($TOTAL_STEPS): Final eval ==="
Notify "Final eval start!" "JB-Pirate-King | Eval"

& python -u "C:\ccit\JB-Pirate-King\ml\eval_all.py" --model-dir "D:\JB-Pirate-King-ML-Results" --data-dir "D:\JB-Pirate-King-AIS\preprocessed" 2>&1 |
    Tee-Object -FilePath "D:\JB-Pirate-King-ML-Results\eval_final_v3.log" -Append

$best  = (Get-Content "D:\JB-Pirate-King-ML-Results\best_ensemble.txt" -ErrorAction SilentlyContinue) -join " "
$mlist = (Get-ChildItem "D:\JB-Pirate-King-ML-Results\model_*.onnx" -ErrorAction SilentlyContinue | ForEach-Object { $_.BaseName -replace "model_","" }) -join ", "
$tot   = Elapsed $PIPELINE_START

WLog "=== ALL DONE | Total: $tot | Best: $best ==="
Notify "Pipeline v3 COMPLETE! Total: $tot Best: $best Models: $mlist Results: D:\JB-Pirate-King-ML-Results" "JB-Pirate-King | DONE"