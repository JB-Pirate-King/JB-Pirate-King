# automation/ — JB-Pirate-King 자동화 파이프라인

AIS 이상탐지 ML 시스템의 전체 자동화 레이어.
**MLflow**, **Google Sheets**, **Notion**, **Slack**, **Discord**, **GitHub Actions**를 통합하여
학습 → 평가 → 리포팅 → 알림 → 릴리즈를 자동화한다.

---

## 아키텍처 다이어그램

> FigJam에서 전체 파이프라인 아키텍처 확인:
> **[JB-Pirate-King AIS 이상탐지 자동화 파이프라인](https://www.figma.com/board/cyTCcQ6zhHlVIrcjSBufli)**

```
[Trigger Layer]
  GitHub Actions (Cron 02:00 KST / Push) → workflow_dispatch (수동)

[Data Layer]
  download_ais.py → preprocess.py → D:\ais_data\preprocessed\
                  ↘ Google Sheets (data_coverage 시트에 기록)

[ML Layer]
  pipeline.py (9 모델 학습) ↔ MLflow Tracking (Params/Metrics/Artifacts)
                            → eval_anomaly.py (32 시나리오 평가)
                            → MLflow Registry (Staging → Production)

[Report Layer]
  eval 결과 → Google Sheets (pipeline_results 시트)
           → Notion Database (실험 페이지 자동 생성)

[Notification Layer]
  완료/실패 → Slack (#ais-training-alerts)
           → Discord (Webhook)
  실패 시  → GitHub Issue 자동 생성

[Deploy Layer]
  MLflow Registry Promotion → GitHub Release (ONNX + Scaler + Threshold)
                           → ais_ids_pi/data/ (플러그인 배포)
```

---

## 디렉토리 구조

```
automation/
├── README.md           # 이 문서
├── __init__.py
├── requirements.txt    # Python 의존성
├── .env.example        # 환경변수 예시
│
├── config.py           # 환경변수 로딩 (모든 모듈이 임포트)
├── mlflow_tracker.py   # MLflow 실험 추적 통합
├── notify.py           # Slack + Discord 알림
├── sheets_tracker.py   # Google Sheets 결과 기록
├── notion_reporter.py  # Notion 자동 문서화
├── github_release.py   # GitHub 릴리즈 자동화
└── pipeline_runner.py  # 메인 오케스트레이터 (진입점)

.github/workflows/
├── ci.yml              # 기존: Push/PR 시 문법 검사 + smoke test
├── daily_pipeline.yml  # 새로 추가: 일일 학습 파이프라인 (self-hosted)
└── release.yml         # 새로 추가: 태그 기반 GitHub 릴리즈 자동화
```

---

## 빠른 시작

### 1. 의존성 설치

```bash
pip install -r automation/requirements.txt
```

### 2. 환경변수 설정

```bash
cp automation/.env.example automation/.env
# .env 파일을 열고 각 서비스의 토큰/키를 입력
```

### 3. MLflow 서버 실행 (로컬)

```bash
mlflow server --host 0.0.0.0 --port 5000
# 브라우저: http://localhost:5000
```

### 4. 파이프라인 실행

```bash
# 기본 실행 (conv1d tranad dcdetect, 10 에포크)
python automation/pipeline_runner.py --models conv1d tranad dcdetect --epochs 10

# 학습 없이 평가만
python automation/pipeline_runner.py --eval-only --models conv1d

# 학습 + 릴리즈 생성
python automation/pipeline_runner.py --models conv1d tranad --epochs 10 --release --tag v0.2.0

# 이미 학습된 모델 스킵
python automation/pipeline_runner.py --models conv1d tranad dcdetect --skip_trained
```

---

## 각 모듈 설명

### `config.py`
모든 환경변수를 한 곳에서 관리. `.env` 파일 또는 OS 환경변수에서 로딩.

### `mlflow_tracker.py`
`ml/pipeline.py` 실행 결과를 MLflow에 기록하는 래퍼.
- 실험(Experiment): `ais-anomaly-detection`
- 런(Run)마다: 파라미터(epochs, seq_len, n_features), 메트릭(DR, FPR), 아티팩트(ONNX, CSV)
- `patch_pipeline_with_mlflow(model, epochs)` — 이미 실행된 결과 CSV를 읽어 MLflow에 소급 기록

### `notify.py`
Slack + Discord 이중 알림.
| 이벤트 | Slack | Discord |
|--------|-------|---------|
| 학습 완료 | ✅ Block 메시지 | ✅ Embed |
| 파이프라인 실패 | ❌ 오류 코드 포함 | ❌ Embed |
| 릴리즈 생성 | 🚀 릴리즈 URL | 🚀 링크 |
| 데이터 업데이트 | 📦 커버리지 | 📦 커버리지 |

### `sheets_tracker.py`
Google Sheets에 두 시트를 관리:
- `pipeline_results` — 모델별 실험 결과 (timestamp, model, DR, FPR, MLflow run_id)
- `data_coverage` — 전처리 데이터 날짜 범위 추적

### `notion_reporter.py`
학습 완료 시 Notion Database에 실험 페이지 자동 생성.
페이지 속성: Model, Status, Train DR, Holdout DR, FP Rate, Epochs, Date, Release URL

### `github_release.py`
GitHub Release 자동 생성 + 모델 파일 첨부.
- `D:\ais_models\{model}\model_{model}.onnx` 를 찾아 첨부
- 비교 리포트(comparison_*.txt/csv)도 함께 첨부
- Release notes 자동 생성 (모델별 DR 포함)

### `pipeline_runner.py`
위 모든 모듈을 순서대로 호출하는 메인 진입점.
실행 순서: `pipeline.py` → MLflow 기록 → Sheets 기록 → Notion 생성 → 알림 → (선택) 릴리즈 생성

---

## GitHub Actions 설정

### Secrets 등록 (GitHub 레포 → Settings → Secrets)

| Secret 이름 | 값 |
|-------------|-----|
| `SLACK_BOT_TOKEN` | Slack Bot OAuth Token (xoxb-...) |
| `SLACK_CHANNEL` | `#ais-training-alerts` |
| `DISCORD_WEBHOOK_URL` | Discord 웹훅 URL |
| `NOTION_API_KEY` | Notion Internal Integration Token |
| `NOTION_DATABASE_ID` | 결과 DB 페이지 ID |
| `GSHEETS_SPREADSHEET_ID` | Google Sheets ID |
| `GH_PAT` | GitHub Personal Access Token (write:packages, repo) |
| `MLFLOW_TRACKING_URI` | `http://localhost:5000` (self-hosted runner 기준) |

### Self-Hosted Runner 등록 (학습용)

학습은 로컬 Windows 머신(D:\ 데이터 있는 곳)에서 실행되어야 하므로 self-hosted runner를 등록한다.

```powershell
# GitHub 레포 → Settings → Actions → Runners → New self-hosted runner → Windows
# 안내에 따라 runner 설치 후:
.\run.cmd
```

`daily_pipeline.yml`과 `release.yml`은 `runs-on: self-hosted`로 설정되어 있음.

### 워크플로 파일

| 파일 | 트리거 | Runner | 역할 |
|------|--------|--------|------|
| `ci.yml` | Push to main/develop, PR | ubuntu-latest | 문법 검사, smoke test |
| `daily_pipeline.yml` | 매일 02:00 KST, 수동 | self-hosted | 학습 + 평가 + 알림 |
| `release.yml` | `v*` 태그 Push, 수동 | self-hosted | 릴리즈 생성 + 파일 첨부 |

---

## MLflow 실험 구조

```
ais-anomaly-detection (Experiment)
├── conv1d_ep10 (Run)
│   ├── params: {model, epochs, seq_len, n_features}
│   ├── metrics: {detection_rate, fp_rate, holdout_detection_rate}
│   └── artifacts: {model_conv1d.onnx, scaler_conv1d.json, conv1d_TIMESTAMP.csv}
├── tranad_ep10 (Run)
│   └── ...
└── dcdetect_ep10 (Run)
    └── ...
```

---

## 서비스별 초기 설정 가이드

### Slack 앱 생성
1. https://api.slack.com/apps → Create New App
2. OAuth & Permissions → Bot Token Scopes: `chat:write`, `files:write`
3. Install to Workspace → Bot User OAuth Token 복사

### Discord 웹훅
1. 서버 → 채널 설정 → 연동 → 웹훅 → 새 웹훅 → URL 복사

### Notion 통합
1. https://www.notion.so/my-integrations → 새 통합 생성
2. Internal Integration Token 복사
3. 결과 Database 페이지 → ... → 연결 → 통합 추가

### Google Sheets 서비스 계정
1. Google Cloud Console → IAM → 서비스 계정 생성
2. JSON 키 다운로드 → `automation/credentials.json`으로 저장
3. Sheets 파일에 서비스 계정 이메일을 편집자로 공유
