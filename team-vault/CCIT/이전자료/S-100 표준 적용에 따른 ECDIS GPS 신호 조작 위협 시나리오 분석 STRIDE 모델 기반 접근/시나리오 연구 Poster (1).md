---
source_file: 시나리오 연구 Poster (1).pdf
last_synced: 2026-06-21 10:05
tags: [notion-sync, attachment]
---

# 시나리오 연구 Poster (1)

S-100 표준 적용에 따른 ECDIS GPS 신호 조작 위협
시나리오 분석: STRIDE 모델 기반 접근

이태용1 유인서1 김주찬2 이규민3 진형권4 양승권5 윤현빈6 박지후7 노용훈8 이민우9* 
순천향대(대학생) 1 , 동국대(대학생) 2 , 인하공업전문대(대학생) 3 , 세종대(대학생) 4 , 중부대(대학생) 5 ,
 고려대(대학생) 6 , 서울디지텍고등학교(고등학생) 7 , CYTUR(WHS PL)8 , 국립한국해양대(WHS 멘토) 

1. 연구 배경 및 목적

4. 주요 위협 시나리오 분석

S-100 표준은 다양한 센서 및 시스템과의 연동을 가능하게 하는 개방형
구조를 갖는다.이는 AIS, GPS, 수로 서버, 기상 데이터 등과 RESTful API로
실시간 통신이 가능해졌다는 의미다.하지만 이러한 확장성은 시스템
외부로부터의 사이버 공격 벡터를 급격히 증가시키며,S-57 구조보다 훨씬 더
정교한 보안 분석이 요구된다.본 연구는 STRIDE 위협 모델을 활용해 S-100 
기반 ECDIS 구조의 취약성을 분석하고,위험도 높은 공격 시나리오를
도출하여 시각화하는 것을 목적으로 한다.

STRIDE 기반 분석 결과, 두 가지 시나리오가 가장 위험성이 높게 평가되었다

첫 번째 시나리오는 해도 서버와 단말 간 TLS가 적용되지 않고 ENC 무결성
검증이 부재한 상황에서, 중간자 공격을 통해 변조된 ENC(.000) 파일을 주입
함으로써 잘못된 항로를 표시하여 좌초 또는 충돌을 유도할 수 있다.

두 번째 시나리오는 AIS나 GPS 신호를 위조할 수 있는 환경에서 NMEA 
메시지를 가로채 조작하여 자율 운항 시스템 등과 연동될 때 잘못된 위치를
표시하도록 유도함으로써 충돌 위험을 증가시킨다.

2. S-100 ECDIS 데이터 흐름

S-100 기반 ECDIS는 ENC 파일, GPS 위치 정보, 자동조타 명령 등 다양한
해양 데이터를 수로기관·위성·관제센터·기상 시스템 등 외부 서버와
실시간으로 교환한다. 이와 같은 구조는 해상 운항의 정밀도를
높이지만,데이터 위조, 탈취, 인증 우회 시 항해 안정성에 치명적 영향을 줄 수
있다.

ECDIS Data Flow Diagram

3. STRIDE 기반 위협 분류

Microsoft STRIDE 모델을 기반으로 ECDIS의 위협 요소를 다음과 같이
분류하였다

요소

Spoofing

Tampering

Repudiation

Information
Disclosure

Denial of
Service

Elevation of
Privilege

설명

GPS, AIS 등의 신호 위조를 통해
잘못된 위치 정보 전달

해도 데이터(.000 파일 등)의 변조
및 조작

로그 미기록으로 인한 공격 행위
부정 가능성

외부 연동 과정에서의 해양 작전
정보 유출

해도 파일의 크기 조작 또는 반복
요청을 통한 ECDIS 다운

미검증 API 호출을 통한 관리자 권한
상승

STRIDE 분석을 통해 도출된 ENC 변조와 GPS 조작 시나리오는
단일 선박에 그치지 않고, 항로 혼잡 구역이나 해상 교차점에서
다수 선박의 연쇄적인 사고로 확산될 위험이 존재한다.  

또한 변조된 정보는 관제센터나 인근 선박에도 전파될 수 있어,  
해상 교통 시스템 전체의 신뢰성 저하 및 해양 환경오염으로 이어질 수 있다.

Team
SeaBugs

ECDIS 위협 시나리오 공격 트리 분석

공격 트리는 각 공격 시나리오의 단계를 시각적으로 표현하여 공격자가
목표를 달성하기 위해 거치는 흐름과 시스템 약점을 명확히 파악할 수 있도록
도와준다.

6. 결론 및 향후 연구 방향 

상호운용성과 실시간 통신 기능이 강화된 S-100 ECDIS는, 동시에 새로운
보안 위협에 직면하고 있다.본 연구는 STRIDE 모델을 적용하여, S-100 
환경에서 발생 가능한 대표적인 사이버 공격 시나리오를 체계적으로
분석하였다. 특히 ENC 파일 변조와 GPS 신호 조작은 선박 항로 판단에
직접적인 영향을 주며, 해양 사고로 이어질 수 있는 고위험 위협으로
평가되었다. 공격 트리를 통해 시나리오의 단계와 전파 경로를 구조화하고, 
향후 탐지 및 대응 전략 수립에 활용할 수 있다.앞으로는 데이터 무결성 검증, 
디지털 서명 적용, 보안 채널 구축과 같은 기술적 방안과 함께,국제기구
수준의 표준화된 해양 사이버보안 대응 프레임워크 수립이 요구된다.

6. 참고문헌

[1] International Maritime Organization (IMO), Guidelines on 
Maritime Cyber Risk Management(MSC-FAL.1/Circ.3/Rev.2)

[2] European Maritime Safety Agency (EMSA), Guidance on 
Cybersecurity Onboard Ships during Audits and Inspections

[3] S-100 ECDIS Governance Document Edition 1:
 v010 16th March 2022

[4] Keskin, O. F., Lubja, K., Bahsi, H., & Tatar, U., “Systematic Cyber 
Threat Modeling for Maritime Operations: Attack Trees for 
ShipboardSystems,” Journal of Marine Science and Engineering, 
vol. 13, no. 4, pp. 645, 2025.
