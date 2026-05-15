# JB-Pirate-King — AIS 이상 탐지 시스템

선박 AIS 신호에서 이상 행동을 탐지하는 시스템. OpenCPN 플러그인, 로컬 서버, ML 파이프라인으로 구성된다.

---

## 구성 요소

| 디렉토리 | 설명 |
|---|---|
| `ml/` | AIS 이상 탐지 ML 파이프라인 (학습 · 평가) |
| `ais_ids_pi/` | OpenCPN 플러그인 (C++, ONNX 추론) |
| `s-c/` | 로컬 서버 + GUI (Python, Docker) |
| `aivdm_gen/` | AIVDM 테스트 신호 생성기 |

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

## CI

GitHub Actions로 Push/PR 시 자동 검사가 실행된다 (`.github/workflows/ci.yml`).

- Python 문법 검사 + 5개 모델 smoke test
- C++ 핵심 파일 컴파일 (`g++ -fsyntax-only`)
- C++ 정적 분석 (`cppcheck`)
