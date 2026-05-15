# JB-Pirate-King — AIS 이상 탐지 시스템

선박 AIS 신호에서 이상 행동을 탐지하는 시스템. OpenCPN 플러그인, 로컬 서버, ML 파이프라인으로 구성된다.

---

## 구성 요소

| 디렉토리 | 설명 |
|---|---|
| `ml/` | AIS 이상 탐지 ML 파이프라인 (학습 · 평가) |
| `ais_ids_pi/` | OpenCPN 플러그인 (C++, ONNX 추론) |
| `s-c/` | 로컬 서버 + GUI (Python, Docker) |
| `aivdm_gen/` | AIVDM 공격 시나리오 시뮬레이터 (15개 시나리오, GUI) |

---

## ML 파이프라인 (`ml/`)

비지도(오토인코더 계열) 9종 + 지도 학습 5종 모델을 지원한다.

```bash
# 비지도 학습
python ml/train_benchmark.py --model dcdetect

# 지도 학습
python ml/train_supervised.py --model moderntcn
python ml/train_supervised.py --model all --max_mmsi 300

# 평가
python ml/eval_anomaly.py --model sup_moderntcn
```

자세한 내용은 [`ml/README.md`](ml/README.md)를 참고한다.

---

## OpenCPN 플러그인 (`ais_ids_pi/`)

학습된 ONNX 모델을 플러그인 `data/` 폴더에 넣으면 실시간 추론이 활성화된다.

```
ais_ids_pi/data/
    model.onnx
    scaler.json
    threshold.txt
```

---

## 로컬 서버 (`s-c/`)

OpenCPN에서 TCP로 AIS NMEA 신호를 받아 이상을 탐지하는 서버. GUI 또는 CLI로 실행한다.

```powershell
cd s-c
python ais_ids_gui.py
```

자세한 내용은 [`s-c/Readme.md`](s-c/Readme.md)를 참고한다.

---

## 시나리오 시뮬레이터 (`aivdm_gen/`)

OpenCPN 또는 IDS 서버로 AIVDM NMEA 신호를 직접 주입하는 ML-Aware 공격 시뮬레이터.

```bash
python aivdm_gen/aivdm_gen.py
```

| 그룹 | 시나리오 | 설명 |
|---|---|---|
| A | A1~A4 | 규칙 기반 탐지 검증 (속도·정박·COG/HDG·위치 점프) |
| B | B5~B7 | 다중 선박 협조 패턴 (글자 선단, 집게, 파상 대형) |
| D | D1~D4 | ML 탐지 우회 1세대 (Low&Slow, 시간 위장, Gradual Drift, Mimicry) |
| E | E4~E5 | ML 탐지 우회 2세대 (Contextual Blend, Shadow Vessel) |
| F | F3, F6 | 구조적 공격 (궤적 봉합, AIS Gap으로 이력 리셋) |

전송 프로토콜은 TCP 서버·클라이언트·UDP를 GUI에서 선택할 수 있다.  
자세한 내용은 [`aivdm_gen/README.md`](aivdm_gen/README.md)를 참고한다.

---

## CI

GitHub Actions로 Push/PR 시 자동 검사가 실행된다 (`.github/workflows/ci.yml`).

- Python 문법 검사 + 5개 모델 smoke test
- C++ 핵심 파일 컴파일 (`g++ -fsyntax-only`)
- C++ 정적 분석 (`cppcheck`)
