---
notion_url: https://www.notion.so/350be08098308063909bc1a563b189e6
last_synced: 2026-06-18 01:02
tags: [notion-sync]
---

# 머신러닝 기반 선박 AIS IDS 설계 및 구현

양승권1*, 임성빈2
중부대학교 정보보호학과1
**Design and Implementation of Machine Learning-based AIS IDS for Maritime Vessels**
SeungGwon Yang1*, SungBin Im2
Dept. of Information Security, ○○ University1, ○○ University2

---


## 1. 서 론
현대 선박은 GPS, AIS, ECDIS 등 다양한 디지털 항해 장비를 탑재하고 있으며, IT/OT 융합에 따라 외부 신호 의존도가 지속적으로 증가하고 있다. 특히 선박 내 항법 시스템은 NMEA 기반 네트워크로 확장되면서 외부 신호 위·변조에 대한 보안 위협이 증가하고 있다.
최근 중동 및 동유럽 해역을 중심으로 GNSS Jamming 및 AIS Spoofing 사례가 지속적으로 발생하고 있으며, 이는 항로 왜곡, 위치 오인, 충돌 사고 등 심각한 항해 안전 문제를 야기한다. 대표적으로 2025년 호르무즈 해협 인근에서는 GPS Jamming으로 인해 다수 선박의 위치 정보가 비정상적으로 동일 지점으로 표시되는 사고가 발생하였다.
또한 International Maritime Organization의 MSC.428(98)과 International Association of Classification Societies UR E26/E27 등 국제 규정이 강화되며 선박 사이버보안 관리가 의무화되고 있다. 이에 따라 AIS 신호 기반 이상 탐지 시스템의 필요성이 증대되고 있다.
본 연구에서는 AIS Spoofing 공격을 탐지하기 위한 머신러닝 기반 AIS IDS(Intrusion Detection System)를 설계 및 구현하였다. 본 시스템은 OpenCPN 플러그인 형태로 개발되어 실제 항해 환경과 유사한 GUI 환경에서 이상 신호를 시각적으로 탐지할 수 있도록 구성하였다.

---


## 2. 본 론

### 2.1 AIS Spoofing 공격 및 보안 문제
AIS는 선박의 위치, 속도, 식별 정보를 브로드캐스트 방식으로 송수신하는 시스템으로, AIVDM 규격의 평문 메시지를 사용한다. 이러한 구조는 인증 및 무결성 검증 기능이 부족하여 허위 위치·속도·식별 정보가 삽입된 가짜 신호를 무방비하게 수신하는 문제가 있다.
AIS Spoofing은 허위 정보를 삽입하여 주변 선박 및 항해 시스템의 상황 인식을 왜곡하는 공격이다. 이는 운항 중단, 경제적 손실, 심각한 경우 선박 간 충돌 사고를 초래할 수 있다.

### 2.2 시스템 설계
본 연구의 전체 시스템은 크게 OpenCPN 기반 IDS 플러그인과 머신러닝 기반 탐지 모듈로 구성된다.

### 2.2.1 OpenCPN AIS IDS 플러그인
OpenCPN은 오픈소스 ECS(Electronic Chart System) 소프트웨어로, 다양한 운영체제를 지원하며 플러그인 기능을 제공한다. 본 연구에서는 OpenCPN 플러그인 API를 활용하여 다음 기능을 구현하였다.
- AIVDM 메시지 수신
- MMSI별 시퀀스 데이터 저장
- 이상 탐지 결과 GUI 시각화
- 이상 탐지 로그 출력
이상 신호로 판단된 선박은 GUI 상에서 빨간 점으로 표시되며, 로그 창에 MMSI가 출력된다.

### 2.2.2 머신러닝 기반 이상 탐지
학습 데이터는 Marine Cadastre에서 제공하는 AIS 데이터를 활용하였다. 해당 데이터셋은 별도의 Label이 존재하지 않으므로 비지도 학습 방식을 채택하였다.
본 연구에서는 시계열 데이터 기반 이상 탐지를 위해 LSTM Autoencoder를 사용하였다. Encoder에서 입력 시퀀스를 압축하고 Decoder에서 복원한 후, 원본과 복원값의 MSE를 계산하여 이상 여부를 판단한다.

![image](_assets/image.png)


임계값은 정상 데이터의 MSE 분포 하위 95%를 기준으로 설정하였다.

### 2.3 데이터 전처리 및 Feature Engineering
학습에 사용된 주요 피처는 다음과 같다.
- SOG (Speed Over Ground)
- COG (Course Over Ground)
- Heading
- Status (NavStatus)
추가적으로 다음과 같은 파생 피처를 생성하였다.
- dt : 신호 수신 시간 간격
- dist_km : 이동 거리
- sog_change : 속도 변화량
- cog_hdg_diff : COG와 Heading 차이
- cog_hdg_change : 방향 변화량
- speed_consistency : 속도와 실제 이동 거리 비율
- lat_speed, lon_speed : 위도/경도 변화율

### 2.4 공격 시나리오 및 실험
공격 시나리오는 다음과 같이 구성하였다.
| Pattern | Description |
| A1 | 순간/주기적 속도 급증 |
| A2 | 정박 상태 이동 이상 |
| A3 | COG/HDG 불일치 |
| A4 | 위치 점프 |
| B1 | 원형 정지 선박 |
| B2 | 원형 순찰 선박 |
| B3 | 직선 왕복 이동 |
| B4 | 유사 실제 항로 + 속도 이상 |

![image](_assets/image.png)

실험은 정상 시퀀스와 이상 시퀀스를 생성하여 탐지율과 오탐율을 측정하였다. 또한 Pearson 상관계수를 통해 피처 간 상관관계를 분석하고, 피처별 MSE 및 중요도를 확인하였다.

![image](_assets/image.png)


---


## 3. 결 론
본 연구에서는 AIS Spoofing 공격을 탐지하기 위한 머신러닝 기반 AIS IDS를 설계 및 구현하였다. OpenCPN 플러그인 기반으로 실제 항해 환경과 유사한 GUI 환경에서 탐지가 가능하도록 구현하였으며, 다양한 공격 시나리오에 대해 이상 탐지 성능을 확인하였다.
향후에는 피처 개선 및 앙상블 모델 적용을 통해 탐지율을 향상시키고, 서버-클라이언트 구조 기반의 실시간 분석 시스템으로 확장할 예정이다.

---


## 참고문헌
[1] IMO, “MSC.428(98) Maritime Cyber Risk Management in Safety Management Systems,” 2017.
[2] IACS, “UR E26 Cyber Resilience of Ships,” 2022.
[3] IACS, “UR E27 Cyber Resilience of On-board Systems and Equipment,” 2022.
[4] Marine Cadastre, “Vessel Traffic Data,” 2025.
[5] Malhotra, P., et al., “LSTM-based Encoder-Decoder for Multi-sensor Anomaly Detection,” ICML Workshop, 2016.
