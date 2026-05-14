# AIS IDS Server

OpenCPN에서 들어오는 AIS NMEA/AIVDM 신호를 TCP로 받아 이상 징후를 탐지하는 로컬 서버입니다.

이 프로젝트는 웹 대시보드가 아니라 로컬 GUI 창과 Docker 컨테이너로 실행합니다.

## 빠른 실행

권장 실행 방식:

```powershell
cd C:\ccit\JB-Pirate-King\s-c
c:\python313\python.exe .\ais_ids_gui.py
```

또는 `AIS-IDS-GUI.bat` 파일을 더블클릭해도 됩니다.

GUI에서 할 수 있는 일:

- Docker Desktop 자동 실행 시도
- `docker compose up --build -d` 실행
- 서버 중지, 재시작, 새로고침
- Docker/컨테이너/API/ML 상태 확인
- 모델 파일 누락 확인
- AIS 수신 카운터, 선박 목록, 경보 목록 확인
- Docker 로그와 경보 로그 확인
- `models`, `logs` 폴더 열기

터미널만 사용할 때:

```powershell
cd C:\ccit\JB-Pirate-King\s-c
c:\python313\python.exe .\start_ais_ids.py
```

진단만 할 때:

```powershell
c:\python313\python.exe .\start_ais_ids.py --skip-compose
```

## 현재 상태 해석

`http://localhost:5000/status`에서 아래처럼 나오면 서버는 정상 실행 중입니다.

```json
{
  "running": true,
  "ml_loaded": false,
  "ml_status": "missing file: /models/model.onnx",
  "stats": {
    "rx_sentences": 0,
    "rx_targets": 0,
    "ml_detections": 0
  },
  "alert_count": 0,
  "tracked_vessels": 0
}
```

의미:

- `running: true`: 서버는 실행 중입니다.
- `ml_loaded: false`: 모델 파일이 없어 ML 탐지만 꺼진 상태입니다.
- `rx_sentences: 0`: 아직 OpenCPN에서 AIS 문장이 들어오지 않았습니다.
- `tracked_vessels: 0`: 아직 추적 중인 선박이 없습니다.

## 모델 파일

ML 탐지를 켜려면 `models` 폴더에 모델 파일을 넣고 서버를 재시작해야 합니다.

단일 모델:

```text
models/
|-- model.onnx
|-- scaler.json
`-- threshold.txt
```

앙상블 모델:

```text
models/
|-- ensemble_config.json
|-- model_dcdetect.onnx
|-- model_tranad.onnx
|-- scaler_tranad.json
`-- threshold_weighted_ensemble.txt
```

`threshold_dcdetect.txt` 같은 개별 모델 threshold 파일은 같이 있어도 됩니다. 현재 앙상블 실행에서는 `threshold_weighted_ensemble.txt`를 사용합니다.

`scaler_dcdetect.json`이 없고 `scaler_tranad.json`만 있는 기존 산출물도 지원합니다. 이 경우 두 모델 모두 같은 스케일러를 사용합니다.

`ensemble_config.json` 예시:

```json
{
  "models": ["model_dcdetect.onnx", "model_tranad.onnx"],
    "scalers": ["scaler_dcdetect.json", "scaler_tranad.json"],
  "threshold": "threshold_weighted_ensemble.txt",
  "weights": [0.7, 0.3]
}
```

모델 파일이 없어도 서버는 실행됩니다. 이 경우 AIS 수신, REST API, 로그 기능은 동작하지만 ML 이상 탐지는 비활성화됩니다.

## OpenCPN 연결

OpenCPN에서 NMEA 출력을 이 서버로 보내면 됩니다.

1. OpenCPN 설정에서 연결 추가
2. 유형: 네트워크
3. 프로토콜: TCP
4. 주소: 이 서버가 실행 중인 PC의 IP
5. 포트: `10110`
6. NMEA 출력 활성화

같은 PC에서 테스트하면 주소는 보통 `127.0.0.1` 또는 `localhost`를 사용할 수 있습니다. 다른 PC에서 보낼 때는 Windows 방화벽에서 TCP `10110` 포트를 허용해야 할 수 있습니다.

## REST API

서버가 실행되면 아래 API를 사용할 수 있습니다.

| API | 설명 |
|---|---|
| `GET /status` | 서버, ML, 수신 통계 상태 |
| `GET /alerts?n=100` | 최근 경보 목록 |
| `GET /vessels` | 현재 추적 중인 선박 목록 |

예:

```powershell
curl http://localhost:5000/status
```

## 파일 구조

```text
s-c/
|-- ais_ids_gui.py        # 로컬 GUI 실행기
|-- AIS-IDS-GUI.bat       # GUI 더블클릭 실행 파일
|-- start_ais_ids.py      # 터미널 실행기
|-- docker-compose.yml
|-- Readme.md
|-- models/               # ONNX 모델 파일 위치
|-- logs/                 # 경보 로그 출력 위치
`-- server/
    |-- Dockerfile
    |-- requirements.txt
    |-- main.py           # TCP NMEA 서버 + REST API
    |-- ais_parser.py     # AIVDM/AIVDO Type 1/2/3 파서
    |-- feature.py        # 12개 특징 추출
    |-- ml_detector.py    # ONNX 모델 로딩 및 추론
    `-- alert_logger.py   # 경보 로그 기록
```

## Docker 명령

GUI나 Python 실행기가 내부적으로 실행하는 명령입니다.

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f ais-ids-server
docker compose restart ais-ids-server
docker compose down
```

## 문제 해결

Docker 오류:

```text
open //./pipe/docker_engine: The system cannot find the file specified.
```

이 오류는 Docker CLI는 있지만 Docker Engine이 실행되지 않았을 때 발생합니다. Docker Desktop을 실행하고 `Engine running` 상태가 된 뒤 다시 실행하세요. GUI 실행기는 Docker Desktop 실행을 자동으로 시도하지만, Windows 정책에 따라 관리자 권한이 필요할 수 있습니다.

컨테이너는 켜졌는데 ML이 비활성화됨:

```text
missing file: /models/model.onnx
```

`s-c\models` 폴더에 모델 파일을 넣고 GUI에서 `Restart`를 누르거나 아래 명령을 실행하세요.

```powershell
docker compose restart ais-ids-server
```

AIS 수신 카운터가 계속 0:

- OpenCPN의 NMEA 출력 대상 IP와 포트 `10110`을 확인하세요.
- 같은 PC가 아니라면 Windows 방화벽에서 TCP `10110`을 허용하세요.
- 서버 로그에서 연결 수락 메시지가 찍히는지 확인하세요.
