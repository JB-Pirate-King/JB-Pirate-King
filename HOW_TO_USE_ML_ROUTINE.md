# 🚀 Claude Code ML 테스트 루틴 - 완벽 가이드

## 📌 개요

Claude Code로 만든 **수동 실행형** ML 테스트 자동화 루틴입니다.
필요할 때마다 한 번에 모든 테스트를 또는 특정 테스트만 선택해서 실행할 수 있습니다.

---

## 🎯 실행 방법 (3가지)

### 방법 1️⃣: 기본 실행 (모든 테스트, 간단)
```bash
cd ml
python run_ml_tests.py
```
- ✅ 모든 테스트를 순차 실행
- ✅ 간단한 출력
- ✅ 빠른 피드백

---

### 방법 2️⃣: 고급 실행 (선택 가능, 유연)
```bash
cd ml
python run_ml_tests_advanced.py
```
- ✅ 모든 옵션 사용 가능
- ✅ 특정 테스트만 선택
- ✅ 상세 로그 확인
- ✅ 타임아웃 커스터마이징

---

### 방법 3️⃣: 목록 확인 (뭐가 있는지 보기)
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

---

## 📊 최종 요약 출력 이해하기

### ✅ 모든 테스트 성공 (Best Case)

```
======================================================================
[2026-05-15 12:30:45] [INFO] ML 테스트 자동화 완료
======================================================================

📊 테스트 결과 요약
----------------------------------------------------------------------
✅ preprocess           [    2.3초] 성공
✅ train_benchmark      [   25.5분] 성공
✅ eval_anomaly         [    8.2분] 성공
----------------------------------------------------------------------
통과: 3/3
총 소요 시간: 33.7분

🎉 모든 테스트 성공!
✨ 결과 저장: ml/results/ml_test_results_20260515_123045.json
```

**의미:**
- 모든 테스트가 성공적으로 완료됨
- 각 테스트별 소요 시간 표시
- 결과가 JSON 파일로 저장됨

---

### ⚠️ 부분 실패 (Partial Failure)

```
📊 테스트 결과 요약
----------------------------------------------------------------------
✅ preprocess           [    2.3초] 성공
✅ train_benchmark      [   25.5분] 성공
❌ eval_anomaly         [    0.5초] 실패
----------------------------------------------------------------------
통과: 2/3
총 소요 시간: 25.8분

⚠️ 1개 테스트 실패
📝 자세한 내용은 결과 JSON 파일을 확인하세요.
```

**의미:**
- 일부 테스트 실패
- 진행된 테스트 결과는 저장됨
- JSON 파일에서 에러 메시지 확인 가능

---

### ❌ 필수 테스트 실패 (Critical Failure)

```
📊 테스트 결과 요약
----------------------------------------------------------------------
❌ preprocess           [    1.2초] 실패
----------------------------------------------------------------------
통과: 0/1
총 소요 시간: 1.2초

⚠️ 1개 테스트 실패
⛔ 필수 테스트(preprocess)가 실패했습니다!
   다른 테스트를 계속 실행할 수 없습니다.
```

**의미:**
- 필수 테스트(데이터 전처리)가 실패
- 다음 테스트는 실행되지 않음 (자동 중단)
- 데이터 파일 확인 필요

---

## 🎛️ 고급 옵션

### 옵션 1: 특정 테스트만 실행
```bash
# 전처리와 벤치마크만
python run_ml_tests_advanced.py --tests preprocess train_benchmark

# 평가만
python run_ml_tests_advanced.py --tests eval_anomaly

# 여러 개
python run_ml_tests_advanced.py --tests preprocess eval_anomaly eval_rule_gen
```

**활용:**
- 🚀 빠른 테스트 (전처리만: 몇 초)
- 🎯 신속 검증 (평가만: 몇 분)
- 🔧 부분 수정 후 검증

---

### 옵션 2: 상세 출력
```bash
python run_ml_tests_advanced.py --verbose
```

**보여주는 것:**
- 각 테스트의 상세 로그
- 실행된 명령어
- 표준 출력 내용

**활용:**
- 🐛 디버깅
- 📝 로그 저장
- 🔍 상세 분석

---

### 옵션 3: 타임아웃 설정
```bash
# 기본값: 3600초 (1시간)
python run_ml_tests_advanced.py --timeout 7200  # 2시간

# 매우 큰 데이터셋
python run_ml_tests_advanced.py --timeout 14400  # 4시간
```

**활용:**
- ⏱️ 시간 제약 있는 환경
- 📊 큰 데이터셋 처리
- 🖥️ 느린 머신

---

### 옵션 조합
```bash
# 가장 유연한 방식: 선택 + 상세 + 타임아웃
python run_ml_tests_advanced.py \
  --tests preprocess train_benchmark \
  --verbose \
  --timeout 7200
```

---

## 📁 결과 확인

### 저장 위치
```
ml/results/ml_test_results_YYYYMMDD_HHMMSS.json
```

**예:**
```
ml_test_results_20260515_123045.json
ml_test_results_20260515_143020.json
ml_test_results_20260515_160115.json
```

각 실행마다 **타임스탐프가 다르므로** 이전 결과가 덮어씌워지지 않습니다.

---

### JSON 결과 파일 구조
```json
{
  "timestamp": "2026-05-15T12:30:45.123456",
  "results": {
    "preprocess": {
      "status": "성공",
      "timestamp": "2026-05-15T12:30:10.123456",
      "elapsed_seconds": 2.3
    },
    "train_benchmark": {
      "status": "성공",
      "timestamp": "2026-05-15T12:55:40.123456",
      "elapsed_seconds": 1530.0
    },
    "eval_anomaly": {
      "status": "성공",
      "timestamp": "2026-05-15T13:04:12.123456",
      "elapsed_seconds": 492.0
    }
  },
  "summary": {
    "total": 3,
    "passed": 3
  }
}
```

---

## ⏱️ 소요 시간 예상

| 테스트 | 처리 | 시간 |
|--------|------|------|
| preprocess | 전처리 | 몇 초 ~ 수 분 |
| train_benchmark | 모델 학습 | GPU: 10-30분 / CPU: 1-3시간 |
| eval_anomaly | 성능 평가 | 수 분 ~ 십 분 |
| eval_rule_gen | 규칙 생성 | 몇 분 |
| eval_rule_nmea | NMEA 평가 | 몇 분 |

**전체 소요 시간:**
- 🚀 빠른 실행 (전처리만): ~5분
- ⚡ 중간 실행 (전처리 + 평가): ~15분  
- 🐢 전체 실행 (모든 테스트): 35분 ~ 3시간+

---

## 💡 사용 시나리오

### 시나리오 1: 코드 변경 후 신속 검증
```bash
# 데이터 전처리만 확인
python run_ml_tests_advanced.py --tests preprocess
```
✅ 30초 내 결과 확인

---

### 시나리오 2: 새로운 모델 테스트
```bash
# 전처리 + 벤치마크 학습만
python run_ml_tests_advanced.py --tests preprocess train_benchmark --timeout 7200
```
✅ 약 25분 내 모델 성능 확인

---

### 시나리오 3: 최종 검증 전 전체 테스트
```bash
# 모든 테스트 실행
python run_ml_tests.py
```
✅ 완벽한 검증

---

### 시나리오 4: 성능 재평가
```bash
# 기존 모델로 평가만 다시
python run_ml_tests_advanced.py --tests eval_anomaly --verbose
```
✅ 상세 결과 확인

---

## 🛠️ 커스터마이징

### 테스트 추가하기

1. `run_ml_tests.py` 수정:
```python
# 새로운 테스트 추가
success, output = run_command(
    [sys.executable, "my_test.py"],
    "나의 커스텀 테스트"
)
test_results["my_test"] = {
    "status": "성공" if success else "실패",
    "timestamp": datetime.now().isoformat()
}
```

2. `run_ml_tests_advanced.py`의 `AVAILABLE_TESTS` 수정:
```python
AVAILABLE_TESTS = {
    # ... 기존 ...
    "my_test": {
        "script": "my_test.py",
        "description": "나의 커스텀 테스트",
        "required": False,
    },
}
```

---

## ⚠️ 주의사항

1. **순차 실행**: 테스트는 동시에 실행되지 않고 순서대로 실행됩니다
2. **필수 의존성**: `preprocess`가 필요한 경우 자동으로 먼저 실행됩니다
3. **시간 소요**: GPU 없으면 상당한 시간이 필요합니다
4. **파일 수정 금지**: 테스트 중 프로젝트 파일을 수정하지 마세요
5. **메모리 요구**: 큰 모델은 충분한 메모리가 필요합니다

---

## 🐛 트러블슈팅

### 에러: "모듈을 찾을 수 없습니다"
```bash
# 필요한 패키지 설치
pip install torch pandas scikit-learn tqdm
```

### 에러: "입력 파일 없음"
```bash
# preprocess.py 상단의 INPUT_DIR / INPUT_GLOB 설정 확인
# 또는 데이터 파일 위치 확인
```

### 에러: "타임아웃"
```bash
# 타임아웃 증가
python run_ml_tests_advanced.py --timeout 14400
```

### GPU를 사용하고 싶은데 CPU로만 실행됨
- CUDA/cuDNN 설치 확인
- 각 스크립트의 `device` 설정 확인
- GPU 드라이버 업데이트

---

## 📞 요약

| 상황 | 명령어 | 시간 |
|-----|--------|------|
| 뭐가 있는지 보기 | `--list-tests` | 1초 |
| 빠른 검증 | `--tests preprocess` | ~5분 |
| 신속 테스트 | `--tests preprocess eval_anomaly` | ~15분 |
| 전체 테스트 | `python run_ml_tests.py` | 35분+ |
| 상세 디버깅 | `--verbose` | 추가 |

---

**Happy Testing! 🎉**
