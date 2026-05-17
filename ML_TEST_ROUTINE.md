# ML 테스트 자동화 루틴

이 루틴은 ML 모델의 학습 및 성능 테스트를 수동으로 실행합니다.

## 빠른 시작

### Claude Code에서 실행
```bash
cd ml
python run_ml_tests.py
```

또는 Python 직접 실행:
```bash
python ml/run_ml_tests.py
```

## 포함된 테스트

1. **데이터 전처리** (`preprocess.py`)
   - 원본 데이터 전처리
   - 정규화 및 피처 엔지니어링

2. **모델 벤치마크** (`train_benchmark.py`)
   - 5개 모델 학습 (PatchTST, iTransformer, TSMixer, ModernTCN, Mamba)
   - 학습 성능 메트릭 측정

3. **이상 탐지 평가** (`eval_anomaly.py`)
   - 학습된 모델의 이상 탐지 성능 평가
   - ROC-AUC, F1-Score 등 메트릭 계산

## 결과 저장 위치

테스트 결과는 다음 위치에 저장됩니다:
```
ml/results/ml_test_results_YYYYMMDD_HHMMSS.json
```

각 실행 결과의 타임스탐프로 구분되므로 이전 결과와 충돌하지 않습니다.

## 커스터마이징

다른 테스트를 추가하려면 `ml/run_ml_tests.py`의 `run_ml_tests()` 함수를 수정하세요.

### 예: 규칙 기반 평가 추가
```python
# 규칙 생성 평가 추가
log_message("\n[4/4] 규칙 생성 평가")
success, output = run_command(
    [sys.executable, "eval_rule_gen.py"],
    "규칙 생성 평가"
)
test_results["eval_rule_gen"] = {
    "status": "성공" if success else "실패",
    "timestamp": datetime.now().isoformat()
}
```

### 예: NMEA 규칙 평가 추가
```python
success, output = run_command(
    [sys.executable, "eval_rule_nmea.py"],
    "NMEA 규칙 평가"
)
test_results["eval_rule_nmea"] = {
    "status": "성공" if success else "실패",
    "timestamp": datetime.now().isoformat()
}
```

## 설정 옵션

`run_ml_tests.py` 수정으로 다음을 커스터마이징할 수 있습니다:
- 타임아웃 시간 (기본값: 3600초 = 1시간)
- 테스트 순서
- 포함될 테스트 항목
- 결과 저장 형식

## 실행 시간

대략적인 예상 실행 시간:
- 데이터 전처리: 수 초 ~ 수 분
- 모델 벤치마크: GPU 있을 시 수 분, CPU 환경에서 수십 분
- 이상 탐지 평가: 수 분

총 소요 시간: CPU 환경에서 수십 분 ~ 수 시간

## 주의사항

- 테스트는 순차적으로 실행됩니다
- 큰 데이터셋의 경우 상당한 메모리와 시간이 필요합니다
- GPU가 있으면 학습 속도가 훨씬 빨라집니다
- 테스트 중 프로젝트 폴더의 파일을 수정하면 결과에 영향을 줄 수 있습니다
