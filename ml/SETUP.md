# ML 파이프라인 — 환경 셋업 & 트러블슈팅

> `ml/README.md`는 **방법론/구조**를, 이 문서는 `orchestrator.py`를 **실제 Windows 학습
> 머신에서 굴러가게 만드는 환경 설정**을 다룬다. 2026-05-29 실데이터 검증 기준.
> (orchestrator/core/integrations 코드는 팀원 작업, 이 문서는 그 위에서 검증한 운영 노하우)

---

## 0. TL;DR — 처음부터 돌리기

```powershell
# 1) 의존성 설치 (onnx 는 long-path 우회 필요 — 아래 3절)
python -m pip install -r ml/requirements.txt
$env:PIP_USER=0; python -m pip install --no-cache-dir --target=C:\pylibs onnx

# 2) 설정 파일 작성 (아래 5절) — ml/pipeline_config.json + ml/gsheets_creds.json

# 3) git remote 확인 (아래 6절)
git remote add upstream https://github.com/JB-Pirate-King/JB-Pirate-King

# 4) 실행 (PYTHONPATH 로 C:\pylibs 의 onnx 를 주입)
$env:PYTHONPATH="C:\pylibs"
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 `
  --data_file "D:/JB-Pirate-King-AIS/preprocessed_all/ais-2018-02-19_preprocessed.csv" `
  --base_dir "D:/" --skip_preprocess --auto_approve
```

---

## 1. 환경 (실측)

| 항목 | 문서상(README/CLAUDE) | **실제 머신** |
|---|---|---|
| Python | 3.14 | **3.11.9 (Microsoft Store 배포판)** |
| 위치 | — | `...\WindowsApps\python.exe` (다른 Python 없음, venv 없음) |
| GPU | Intel Arc | **CPU 학습** (torch 2.4.1+cpu) |
| 관리자 권한 | — | **없음** (long-path 레지스트리 변경 불가 → 3절 우회 사용) |

Store Python은 `pip install`이 기본적으로 `--user`를 강제한다. `--target`과 함께 쓰려면
`PIP_USER=0`을 먼저 설정해야 한다(아래 3절).

---

## 2. 의존성

`ml/requirements.txt` 참고. 핵심:

- **ML**: torch(+cpu), onnxruntime, onnx, numpy, pandas, scikit-learn, scipy, tqdm
- **integrations**: slack_bolt, slack_sdk, gspread, google-auth, google-auth-oauthlib, mlflow

> ⚠️ `slack_bolt`, `gspread`, `google-auth`, `mlflow`는 기본 환경에 **빠져 있었다**.
> orchestrator는 import 단계에서 이들이 없으면 죽으므로 반드시 설치.

---

## 3. onnx long-path 이슈 (★ 가장 중요)

### 증상
`torch.onnx.export()`가 다음으로 실패하고, 그 결과 **학습 전체가 실패**한다:
```
torch.onnx.errors.OnnxExporterError: Module onnx is not installed!
  ModuleNotFoundError: No module named 'onnx.defs'
```
torch 2.4의 export는 내부에서 `import onnx`를 호출한다. 즉 **onnx는 임계경로**다.

### 근본 원인
`pip install onnx`가 **WinError 206 (경로 260자 초과)**로 중간에 실패한다. onnx 휠에는
`onnx\backend\test\data\node\test_attention_4d_with_past_and_present_qk_matmul_bias_3d_mask_causal\...`
같은 초장문 경로가 있는데, Store Python의 base 경로가 이미 길어 합산이 MAX_PATH를 넘는다.
설치가 `onnx/defs/`를 놓친 채 끝나 import가 깨진다.
Windows long-path는 `HKLM\...\FileSystem\LongPathsEnabled=1`로 켜야 하나 **관리자 권한 필요**.

### 해결 (관리자 불필요) — 짧은 경로에 설치 + PYTHONPATH 주입
```powershell
$env:PIP_USER=0
python -m pip install --no-cache-dir --target=C:\pylibs onnx
# 검증
$env:PYTHONPATH="C:\pylibs"; python -c "import onnx, onnx.defs; print(onnx.__version__)"   # → 1.21.0
```
이후 파이프라인/오케스트레이터 실행 시 항상 `PYTHONPATH=C:\pylibs`를 설정한다.
orchestrator의 subprocess(run_cmd)는 부모 환경을 상속하므로, 부모 셸에 한 번만 설정하면 된다.

> 대안(권장, 1회): 관리자 PowerShell에서 `LongPathsEnabled=1` 설정 후 일반 `pip install onnx` →
> PYTHONPATH 우회 불필요. 관리자 권한이 있으면 이쪽이 깔끔하다.

---

## 4. 데이터 경로 (실측)

문서의 `D:\ais_data\...`는 이 머신에 **없다**. 실제 전처리 데이터:

```
D:\JB-Pirate-King-AIS\
├── preprocessed_all\   # ais-YYYY-MM-DD_preprocessed.csv  (일별, ~1GB, 1002개)
└── preprocessed\       # *_skip_log.csv (스킵 로그만)
D:\JB-Pirate-King-ML-Results\   # 과거 학습 산출물(onnx/scaler/threshold)
```

일별 CSV 헤더는 현재 `BASE_FEATURES` 12개와 정확히 일치(검증됨):
`mmsi,base_date_time,latitude,longitude,sog,cog,heading,status,vessel_type,dt,dist_km,
cog_hdg_diff,sog_change,cog_hdg_change,speed_consistency,lat_speed,lon_speed`

- 빠른 실험: 중간 크기 일별 파일 1개를 `--data_file`로 지정 + `--max_mmsi`로 상한.
  예) `ais-2018-02-19_preprocessed.csv` (27.6 MB).
- 전체/3년치 병합 데이터셋은 별도 빌드 필요(`build_3yr_dataset.py`).

---

## 5. 설정 파일 (시크릿 — 모두 gitignore 됨)

### `ml/pipeline_config.json`
```json
{
  "slack":         { "bot_token": "xoxb-...", "app_token": "xapp-...", "channel": "C0XXXXXXX" },
  "google_sheets": { "credentials_file": "ml/gsheets_creds.json",
                     "sheet_id": "1uSF1FXsMvha24t0LpgNbI20MLumbq4lm1LbBtc14H1U" },
  "mlflow":        { "tracking_uri": "sqlite:///mlflow.db",
                     "experiment": "ais-anomaly-detection" }
}
```
- `mlflow` 키는 **선택** — 없으면 `sqlite:///mlflow.db` 기본값. (`MLFLOW_TRACKING_URI` 환경변수로도 override)
- `ml/*.json`은 통째로 gitignore(예외: `fe_state.json`). 시크릿이 커밋될 일 없음.

### `ml/gsheets_creds.json`
Google 서비스계정 키(JSON) 그대로 저장. 해당 서비스계정 이메일을 **대상 스프레드시트에 공유(편집자)**
해야 한다. (검증된 계정: `mcp-sheets@macro-kiln-497309-r0.iam.gserviceaccount.com`)

---

## 6. 외부 연동 상태 (2026-05-29 검증)

| 연동 | 상태 | 비고 |
|---|---|---|
| **MLflow** | ✅ 동작 | 로컬 sqlite. `mlflow ui --backend-store-uri sqlite:///mlflow.db` → http://localhost:5000 |
| **Google Sheets** | ✅ 동작 | 서비스계정 접근 확인("JB-Pirate-King" 시트) |
| **git** | ✅ 동작 | `git_manager`는 remote **`upstream`**으로 push → `git remote add upstream <repo>` 필요 |
| **Slack** | ⚠️ 봇 초대 필요 | 토큰 유효(team `ais-pipeline`, bot `@demo_app2`). `chat.postMessage`가 `channel_not_found` |

### Slack 봇 초대 (수동, 1회)
1. 대상 채널에서 `/invite @demo_app2`
2. 봇 스코프에 `chat:write` 필요(채널 나열하려면 `channels:read`도)
3. **대화형 승인(버튼)**을 쓰려면 App-Level Token(`xapp-`, `connections:write`)을 발급해 `app_token`에 넣는다.
   - `app_token`이 없으면 Socket Mode가 자동 비활성화되고 **메시지 전송만** 동작 → `--auto_approve`로 운용.

---

## 7. 실행

```powershell
$env:PYTHONPATH="C:\pylibs"   # onnx 주입 (3절)

# (A) 코어 단순 학습+평가 — 연동/깃 없이 ML만 (가장 빠른 검증)
python ml/core/pipeline.py --train --eval --models dcdetect --epochs 1 --max_mmsi 40 `
  --data_file "D:/JB-Pirate-King-AIS/preprocessed_all/ais-2018-02-19_preprocessed.csv" --base_dir "D:/"

# (B) 풀 오케스트레이터 (무인) — Slack/Sheets/MLflow/git 자동
python -m ml.orchestrator --model dcdetect --epochs 5 --max_mmsi 3000 `
  --data_file "D:/JB-Pirate-King-AIS/preprocessed_all/ais-2018-02-19_preprocessed.csv" `
  --base_dir "D:/" --skip_preprocess --auto_approve
```

`--skip_preprocess --skip_train --skip_eval`로 FE 단계만 단독 실행 가능.

---

## 8. 검증된 결과 (스모크)

코어 단순 경로 (dcdetect, epoch 1, 40 MMSI, `ais-2018-02-19`):
```
dcdetect  오탐율 15.6%  FP1%학습평균 48.7%  FP5% 55.2%  FP10% 66.8%  홀드아웃(일반화) 70.0%  학습 0.1분
```
학습 → 32개 시나리오 평가 → ONNX export 전 구간 정상. MLflow 기록(run_id 발급) 및 Sheets 접근 확인.

---

## 9. 트러블슈팅 빠른 표

| 증상 | 원인 | 조치 |
|---|---|---|
| `Module onnx is not installed` / `onnx.defs` 없음 | onnx long-path 설치 실패 | 3절 — `--target=C:\pylibs` + `PYTHONPATH` |
| `No module named 'slack_bolt'/'gspread'/'mlflow'` | 의존성 미설치 | `pip install -r ml/requirements.txt` |
| `FileNotFoundError: ml/pipeline_config.json` | 설정 파일 없음 | 5절 |
| Slack `channel_not_found`/`not_in_channel` | 봇 미초대 | 6절 — `/invite @demo_app2` |
| Sheets `PermissionError`/403 | 서비스계정 미공유 | 시트를 서비스계정 이메일에 공유 |
| `WinError 206` (pip) | 경로 260자 초과 | 3절 우회 또는 LongPathsEnabled |
| git push 실패(`upstream` 없음) | remote 미설정 | `git remote add upstream <repo>` |
