# aivdm_gen — AIS 공격 시나리오 시뮬레이터

OpenCPN 또는 AIS IDS 서버로 AIVDM NMEA 신호를 전송하는 ML-Aware 공격 시뮬레이터.  
15개 이상 시나리오를 GUI에서 선택해 실시간으로 주입할 수 있다.

---

## 실행

```bash
python aivdm_gen/aivdm_gen.py
```

Python 3.10+ 표준 라이브러리만 사용 (별도 설치 불필요).

---

## GUI 구성

```
┌──────────────────────────────────────────┐
│  시나리오 선택   네트워크 설정   파라미터  │  ← 왼쪽 패널 (설정)
│                                          │
│  [시작]  [중지]  [실시간 제어]            │
├──────────────────────────────────────────┤
│  전송 로그                                │  ← 오른쪽 패널 (로그)
└──────────────────────────────────────────┘
```

**실시간 제어 창** (`[RT 제어]` 버튼)을 열면 시뮬레이션 중에 SOG 배율, COG 오프셋, 위치 산포, NavStatus 오버라이드, 위치 점프를 즉시 조작할 수 있다.

---

## 네트워크 설정

| 프로토콜 | 설명 | 언제 사용 |
|---|---|---|
| `TCP 서버` | 이 PC의 포트를 열고 OpenCPN이 접속 | OpenCPN에서 아웃바운드로 연결할 때 |
| `TCP 클라이언트` | 지정 IP:포트로 직접 연결 | IDS 서버·OpenCPN이 먼저 수신 대기할 때 |
| `UDP` | 단방향 UDP 전송 | 수신 확인 없이 빠른 주입이 필요할 때 |

기본값: TCP 서버, 포트 `10110`

---

## 시나리오 목록

### A — 규칙 기반 탐지 검증 (IDS 정상 동작 확인용)

| ID | 이름 | 탐지 목표 | 회피 전략 |
|---|---|---|---|
| A1 | 속도 이상 | SOG 상한 초과 (ship-type maxSOG) | 없음 |
| A2 | 정박 이동 이상 | navStatus=1 + SOG≥0.5 불일치 | 없음 |
| A3 | COG/HDG 불일치 | COG-HDG diff > 100° | 없음 |
| A4 | 위치 점프 | Haversine 거리 > 5 km/min | 없음 |

### B — 다중 선박 협조 패턴

| ID | 이름 | 탐지 목표 | 회피 전략 |
|---|---|---|---|
| B5 | JBU 글자 선단 | 다수 선박 협조 이동 문자 형태 구성 | 각 선박 개별 거동은 저속 정상 범위 내 |
| B6 | 집게 협공 | 다수 선박 중심점 수렴 패턴 | 없음 |
| B7 | 파상 대형 | 사인파 횡진 + 전진 대형 | COG 변화가 자연스러운 파도 회피처럼 보임 |

### D — ML 탐지 우회 (1세대)

| ID | 이름 | 탐지 목표 | 회피 전략 |
|---|---|---|---|
| D1 | Low & Slow | 모든 규칙 임계값 동시 하회 | Δsog<9.9, dist<5 km/min, COG-HDG<99° 동시 유지 |
| D2 | Temporal Camouflage | 정상 N개 사이 이상 1개 삽입 | 윈도우 피처 평균에 희석 |
| D3 | Gradual Drift | GPS 노이즈 수준 이동 누적 | 각 스텝 ~44 m (GPS 오차 범위), 누적 시 수십 km |
| D4 | Feature Mimicry | 정상 SOG 프로파일 복사 + 실제 위치는 다른 방향 | 보고 SOG는 정상 분포 내, 실제 궤적은 hidden 속도 |

### E — ML 탐지 우회 (2세대, 고급 위장)

| ID | 이름 | 탐지 목표 | 회피 전략 |
|---|---|---|---|
| E4 | Contextual Blend | ship-type 맥락 위장으로 ML 클러스터 오분류 유도 | shipType=30+navStatus=7+조업 패턴 → 어선 클러스터로 평가 |
| E5 | Shadow Vessel | MMSI 지역 정합·항로 준수로 규칙+ML 동시 우회 | 한국 MID(440/441)+연안화물 프로파일+실제 항로각 |

### F — 구조적 공격 (IDS 이력·피처 무력화)

| ID | 이름 | 탐지 목표 | 회피 전략 |
|---|---|---|---|
| F3 | Trajectory Stitching | trajectory continuity 피처 무력화 | C1 연속 Hermite spline으로 궤적 봉합 → 곡률·jerk 자연스러움 |
| F6 | AIS Gap | 신호 소실 → 재등장으로 IDS 이력 리셋 유도 | T_silence > 300 s → 재등장 후 이력 재시작 |

---

## 파일 재생 모드

NMEA 파일(`.txt`, `.nmea`)을 불러와 지정 주기로 반복 전송할 수 있다.

```
파일 선택 → 전송 주기(초) 설정 → 반복 여부 체크 → [파일 재생 시작]
```

실제 녹화된 AIS 로그를 재주입할 때 사용한다.

---

## 아키텍처

```
AttackPlugin (추상 클래스)
  ↓  @_reg 데코레이터로 AttackRegistry에 자동 등록
SimEngine
  ↓  tick 루프: plugin.make() → plugin.update() → Transport.send()
Transport
  ├─ tcp_server  : socket.listen() → accept() 루프
  ├─ tcp_client  : socket.connect()
  └─ udp         : socket.sendto()
```

새 시나리오를 추가하려면 `AttackPlugin`을 상속하고 `@_reg`를 붙이면 GUI에 자동 등록된다.
