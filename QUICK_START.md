# ⚡ ML 테스트 루틴 - 빠른 시작

## 🚀 가장 빠른 시작 (30초)

```bash
cd ml
python run_ml_tests.py
```

끝! 이제 결과를 기다리세요. 📊

---

## 📋 자주 쓸 명령어 (복사해서 사용)

### 1️⃣ 모든 테스트 (약 35분)
```bash
python run_ml_tests.py
```

### 2️⃣ 특정 테스트만 (약 2초 - 5분)
```bash
# 전처리만
python run_ml_tests_advanced.py --tests preprocess

# 평가만
python run_ml_tests_advanced.py --tests eval_anomaly

# 전처리 + 벤치마크
python run_ml_tests_advanced.py --tests preprocess train_benchmark
```

### 3️⃣ 상세 로그 보기
```bash
python run_ml_tests_advanced.py --verbose
```

### 4️⃣ 뭐가 있는지 보기
```bash
python run_ml_tests_advanced.py --list-tests
```

### 5️⃣ 시간 제약이 있을 때 (타임아웃 2시간)
```bash
python run_ml_tests_advanced.py --timeout 7200
```

---

## 📊 결과 확인

### 화면 출력
```
✅ preprocess           [    2.3초] 성공
✅ train_benchmark      [   25.5분] 성공
✅ eval_anomaly         [    8.2분] 성공
----------------------------------------------------------------------
통과: 3/3
총 소요 시간: 33.7분

🎉 모든 테스트 성공!
```

### 파일로 저장됨
```
ml/results/ml_test_results_20260515_123045.json
```

---

## ⏱️ 소요 시간

| 명령어 | 소요 시간 |
|--------|---------|
| `--tests preprocess` | ~5초 |
| `--tests eval_anomaly` | ~8분 |
| `--tests preprocess train_benchmark` | ~25분 |
| `python run_ml_tests.py` | ~35분 |

---

## 🎯 목적별 추천 명령어

| 목적 | 명령어 |
|------|--------|
| 신속 테스트 | `python run_ml_tests_advanced.py --tests preprocess` |
| 빠른 평가 | `python run_ml_tests_advanced.py --tests eval_anomaly` |
| 완전 검증 | `python run_ml_tests.py` |
| 디버깅 | `python run_ml_tests_advanced.py --verbose` |
| 모델만 학습 | `python run_ml_tests_advanced.py --tests train_benchmark` |

---

## ❓ FAQ

**Q: 어떤 명령어를 써야 하나요?**
```
빠르게? → python run_ml_tests_advanced.py --tests preprocess
자세히? → python run_ml_tests_advanced.py --verbose
모두? → python run_ml_tests.py
```

**Q: 얼마나 걸리나요?**
```
전처리: 5초
평가: 8분
전체: 35분 이상
```

**Q: 오류가 나면?**
```
화면 출력에 에러 메시지 있음
ml/results/ 폴더에 JSON 파일에서도 확인 가능
```

**Q: 다시 실행해도 괜찮나요?**
```
네! 매번 새로운 파일이 생성됩니다.
이전 결과는 남아있습니다.
```

---

## 🔧 옵션 조합 예제

```bash
# 예제 1: 빠른 + 상세
python run_ml_tests_advanced.py --tests preprocess --verbose

# 예제 2: 여러 테스트 + 상세
python run_ml_tests_advanced.py --tests preprocess eval_anomaly --verbose

# 예제 3: 긴 타임아웃 + 상세
python run_ml_tests_advanced.py --timeout 7200 --verbose

# 예제 4: 전부 다
python run_ml_tests_advanced.py --tests preprocess train_benchmark eval_anomaly --verbose --timeout 14400
```

---

## 💾 결과 경로

```
ml/results/ml_test_results_YYYYMMDD_HHMMSS.json

예:
ml/results/ml_test_results_20260515_123045.json
ml/results/ml_test_results_20260515_143020.json
ml/results/ml_test_results_20260515_160115.json
```

---

**🎬 준비됐으면 실행하세요!**

```bash
cd ml && python run_ml_tests.py
```
