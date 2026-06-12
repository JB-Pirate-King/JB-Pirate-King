---
notion_url: https://www.notion.so/368be080983080c58b39c99fe6589e8c
last_synced: 2026-06-12 19:39
tags: [notion-sync]
---

# JB-Pirate-King ML 파이프라인 

목표: 2016-2025 전체 AIS 데이터로 이상탐지 앙상블 모델 학습 및 평가
완료된 작업
| 항목 | 내용 |
| 데이터 수집 | download_ais_allmonths.py — 2016~2025 전 기간 랜덤 날짜 샘플링, 스트리밍(다운→전처리→삭제), 디스크 가드 80GB |
| 디스크 정리 | raw CSV 401개 삭제 (263GB 확보), tmp 파일 40GB 정리 → D: 여유 1,390GB+ |
| 전처리 | 679/~1,800개 완료 (진행 중), workers=3 (학습 우선) |
| 자동화 | download_watchdog.py — 크래시 자동재시작(최대 10회), 20분 하트비트, 완료 후 preprocess_fail 일괄재시도 |
| Discord 수정 | urllib → requests 우선 사용으로 403 Forbidden 해결, 알림 정상화 |
다운로드 파이프라인 (워치독 PID 22128)

```python

679/~1,800개 전처리 완료 | workers=3
2018년 대형 파일(770-800MB) 간헐적 preprocess_fail → 워치독이 완료 후 재시도
```

학습 파이프라인 (PID 4220)

```python

Pass 1: 20% (120/609) — 154min 경과 | ETA ~04:30 AM
모델: tranad (BOM 제거됨)
GPU: AMD Radeon RX 9060 XT (DirectML)
테스트셋: 67개 파일, 2023-01-27 ~ 2025-01-31 (데이터 누수 없음)
```

주요 이슈 및 해결
| 이슈 | 원인 | 조치 |
| Discord 알림 03시부터 단절 | urllib HTTP 403 | requests 라이브러리 우선 사용 |
| 학습 프로세스 2시간 동결 | 다운로드 10워커와 D: 디스크 I/O 경합 (23MB/s 쓰기, 262KB/s 읽기) | workers 10→3 축소, 학습 재시작 |
| 구 학습 PID BOM 오염 | best_ensemble.txt의 \ufeff 접두사로 lstm 모델명 인식 불가 | --model tranad 명시 재시작 |
| stdout 버퍼링 | sys.stdout.reconfigure write_through 미적용 | 소스 수정 완료 (차기 실행부터 적용) |
다음 단계 (자동화됨)

```python
Pass 1 완료 (~04:30) → Pass 2 (~10시간) → [cache.pt](http://cache.pt/) 생성
→ TrANAD GPU 학습 (50 epoch) → eval_all.py (테스트 67개)
→ Discord 최종 보고 (ROC AUC, FPR 1% 동작점, Bootstrap CI)
```
