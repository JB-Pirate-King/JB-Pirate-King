# ML 테스트 자동화 가이드

Claude Code 루틴으로 ML 모델의 학습 및 성능 테스트를 수동으로 실행합니다.

## 📋 목차
1. [빠른 시작](#빠른-시작)
2. [기본 사용법](#기본-사용법)
3. [고급 사용법](#고급-사용법)
4. [테스트 설명](#테스트-설명)
5. [결과 확인](#결과-확인)
6. [커스터마이징](#커스터마이징)

---

## 빠른 시작

### 가장 간단한 방법 (모든 테스트 실행)

```bash
cd ml
python run_ml_tests.py
```

### 고급 옵션 사용

```bash
cd ml
python run_ml_tests_advanced.py
```

---

## 기본 사용법

### 1. 기본 테스트 실행
```bash
python run_ml_tests.py
```

자동으로 다음 3개 테스트를 순차 실행합니다:
1. 데이터 전처리
2. 모델 벤치마크 학습
3. 이상 탐지 평가

### 2. 결과 확인
`ml/results/` 폴더에서 최신 결과 확인:
```bash
ls -lt ml/results/
```

---

## 고급 사용법

### 1. 사용 가능한 테스트 목록 확인
```bash
python run_ml_tests_advanced.py --list-tests
```

출력:
```
🧪 사용 가능한 테스트 목록
--------------------------------------------------
  preprocess           - 데이터 전처리 (필수)
  train_benchmark      - 모델 벤치마크 학습
  eval_anomaly         - 이상 탐지 모델 평가
  eval_rule_gen        - 규칙 생성 평가
  eval_rule_nmea       - NMEA 규칙 평가
```

### 2. 특정 테스트만 실행
```bash
# 전처리와 벤치마크만 실행
python run_ml_tests_advanced.py --tests preprocess train_benchmark

# 이상 탐지 평가만 실행 (자동으로 전처리도 포함됨)
python run_ml_tests_advanced.py --tests eval_anomaly
```

### 3. 상세 출력 보기
```bash
python run_ml_tests_advanced.py --verbose
```

각 테스트의 상세한 출력과 로그를 보여줍니다.

### 4. 타임아웃 설정 (초 단위)
```bash
# 기본값: 3600초 (1시간)
python run_ml_tests_advanced.py --timeout 7200  # 2시간
```

### 5. 복합 옵션
```bash
# 특정 테스트만, 상세 출력, 타임아웃 2시간
python run_ml_tests_advanced.py --tests train_benchmark eval_anomaly --verbose --timeout 7200
```

---

## 테스트 설명

### 1. 데이터 전처리 (preprocess.py)
- **목적**: 원본 데이터 정제 및 전처리
- **주요 작업**:
  - 데이터 로드
  - 결측값 처리
  - 정규화/표준화
  - 피처 엔지니어링
- **소요 시간**: 몇 초 ~ 몇 분
- **의존성**: 없음 (다른 테스트 전 필수 실행)

### 2. 모델 벤치마크 학습 (train_benchmark.py)
- **목적**: 여러 모델 아키텍처 성능 비교
- **학습 모델** (5종):
  - **PatchTST**: 패치 토큰화 + Transformer (NeurIPS 2023)
  - **iTransformer**: 입력 전치 + 다변량 어텐션 (ICLR 2024)
  - **TSMixer**: 시간/피처 축 MLP (2023)
  - **ModernTCN**: ConvNeXt 스타일 대형 커널 (ICLR 2024)
  - **Mamba**: 선택적 상태 공간 모델 (NeurIPS 2023)
- **소요 시간**: 
  - GPU: 10-30분
  - CPU: 1-3시간
- **의존성**: preprocess (자동 실행)

### 3. 이상 탐지 평가 (eval_anomaly.py)
- **목적**: 학습된 모델의 이상 탐지 성능 평가
- **평가 지표**:
  - ROC-AUC
  - Precision, Recall, F1-Score
  - Confusion Matrix
- **소요 시간**: 수 분 ~ 십 분
- **의존성**: train_benchmark (자동 실행)

### 4. 규칙 생성 평가 (eval_rule_gen.py)
- **목적**: 규칙 기반 이상 탐지 방식 평가
- **소요 시간**: 수 분

### 5. NMEA 규칙 평가 (eval_rule_nmea.py)
- **목적**: NMEA 프로토콜 기반 규칙 평가
- **소요 시간**: 수 분

---

## 결과 확인

### 결과 파일 위치
```
ml/results/ml_test_results_YYYYMMDD_HHMMSS.json
```

### 결과 파일 형식
```json
{
  "timestamp": "2024-05-15T14:30:45.123456",
  "results": {
    "preprocess": {
      "status": "성공",
      "timestamp": "2024-05-15T14:30:50.123456",
      "elapsed_seconds": 5.0
    },
    "train_benchmark": {
      "status": "성공",
      "timestamp": "2024-05-15T14:45:30.123456",
      "elapsed_seconds": 880.0
    },
    ...
  },
  "summary": {
    "total": 3,
    "passed": 3
  }
}
```

### 최신 결과 빠르게 확인
```bash
# 최신 결과 파일 열기
cat ml/results/$(ls -t ml/results/ | head -1)
```

---

## 커스터마이징

### 테스트 항목 추가

`run_ml_tests.py` 수정:

```python
# 4. 새로운 테스트 추가
log_message("\n[4/4] 나의 커스텀 테스트")
success, output = run_command(
    [sys.executable, "my_custom_test.py"],
    "나의 커스텀 테스트"
)
test_results["my_custom_test"] = {
    "status": "성공" if success else "실패",
    "timestamp": datetime.now().isoformat()
}
```

### 고급 버전에서 테스트 추가

`run_ml_tests_advanced.py`의 `AVAILABLE_TESTS` 수정:

```python
AVAILABLE_TESTS = {
    # ... 기존 테스트 ...
    "my_custom_test": {
        "script": "my_custom_test.py",
        "description": "나의 커스텀 테스트",
        "required": False,
    },
}
```

그 후:
```bash
python run_ml_tests_advanced.py --tests my_custom_test
```

### 테스트 순서 변경

`run_ml_tests.py`에서 함수 호출 순서 변경

### 로깅 수준 변경

`run_ml_tests.py`의 `log_message()` 함수 수정

---

## 트러블슈팅

### 문제: "모듈을 찾을 수 없습니다"
```bash
# 필요한 패키지 설치
pip install -r requirements.txt
```

### 문제: "메모리 부족" 오류
```bash
# 배치 크기 축소 (각 스크립트에서)
# 또는 불필요한 다른 프로그램 종료
```

### 문제: "타임아웃" 오류
```bash
# 타임아웃 증가
python run_ml_tests_advanced.py --timeout 14400  # 4시간
```

### 문제: GPU를 사용하고 싶음
- 각 학습 스크립트에서 `device` 설정 확인
- CUDA/cuDNN 설치 확인
- GPU 드라이버 업데이트

---

## 예제 시나리오

### 시나리오 1: 빠른 테스트 (5분)
```bash
# 전처리만 실행
python run_ml_tests_advanced.py --tests preprocess
```

### 시나리오 2: 전체 테스트 (1-3시간)
```bash
# 모든 테스트 실행
python run_ml_tests.py
```

### 시나리오 3: 특정 모델만 평가
```bash
# 모델 학습 후 평가만 수행
python run_ml_tests_advanced.py --tests eval_anomaly eval_rule_gen --verbose
```

### 시나리오 4: 야간 테스트 스케줄
```bash
# cron 작업으로 매일 자정에 실행 (Linux/Mac)
0 0 * * * cd /path/to/project/ml && python run_ml_tests_advanced.py >> test_logs.txt 2>&1
```

---

## Claude Code 통합

Claude Code에서 빠르게 실행:

```bash
# 프로젝트 루트에서
cd ml
python run_ml_tests.py

# 또는 고급 버전
python run_ml_tests_advanced.py --list-tests
python run_ml_tests_advanced.py --tests train_benchmark eval_anomaly --verbose
```

---

## 주의사항

1. **순차 실행**: 테스트는 순서대로 실행됩니다
2. **자동 의존성**: 필요한 전처리는 자동으로 실행됩니다
3. **시간 소요**: GPU 없이는 상당한 시간이 필요합니다
4. **파일 수정**: 테스트 중 프로젝트 파일 수정 금지
5. **디스크 공간**: 큰 모델과 데이터는 상당한 디스크 공간 필요
6. **메모리**: 여러 모델 동시 학습은 많은 메모리 필요

---

## 추가 정보

- `run_ml_tests.py`: 기본 버전 (간단, 빠름)
- `run_ml_tests_advanced.py`: 고급 버전 (유연한, 상세함)
- `ML_TEST_ROUTINE.md`: 기본 사용 설명서
- `ML_TESTING_GUIDE.md`: 이 파일 (상세 가이드)

---

**마지막 업데이트**: 2024-05-15
