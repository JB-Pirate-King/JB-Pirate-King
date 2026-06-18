---
notion_url: https://www.notion.so/322be080983081ffbec8f422ccfaa006
last_synced: 2026-06-18 01:06
tags: [notion-sync]
---

# “A Study on the Risks of Visual Information Alteration Using the S-52 Presentation Library and User Protection Measures” 

Title “A Study on the Risks of Visual Information Alteration Using the S-52 Presentation Library and User Protection Measures”
1. **원고 분량**:
  - LNCS(Lecture Notes in Computer Science) 템플릿 사용
  - 본문 최대 4페이지(참고문헌 제외)
1. **제출 형식**:
  - PDF (익명화 불필요)
  - 이미 발표된 논문을 바탕으로 할 경우:
    - 논문 서지사항(제목∙저자∙발표처 등)
    - 공식 공개된 버전의 링크
    - 논문 초록

## 0. 초록
- 영어
  The Electronic Chart Display and Information System (ECDIS) is a crucial navigation tool responsible for modern maritime safety, mandated by the International Maritime Organization (IMO) for installation on ships. ECDIS visualizes Electronic Navigational Chart (ENC) data according to the International Hydrographic Organization's (IHO) S-52 standard, wherein the S-52 Presentation Library plays a pivotal role by converting critical navigational information such as depths, reefs, and navigational buoys into standardized symbols and colors. Previous cybersecurity research on ECDIS primarily focused on external input data integrity, such as GPS spoofing or AIS data tampering. However, even with valid input data, a compromised S-52 Presentation Library responsible for visual representation can mislead navigators with distorted information, potentially causing severe maritime accidents.
In this study, we utilized the open-source ENC software OPENCPN to establish a virtual experimental environment, directly manipulating the Lookup Table of the S-52 Presentation Library. Specifically, we demonstrated attacks by altering hazardous objects (e.g., reefs) to appear as safe zones (e.g., safe water depth) or displaying navigation routes in incorrect locations. This research expands the scope of ECDIS cybersecurity from data reception to the visualization phase, empirically demonstrating the potential threat posed by visual manipulation. Based on the experimental results, practical security measures are proposed to mitigate cybersecurity threats in the visualization stage of ECDIS.
전자해도표시시스템(ECDIS)은 현대 항해 안전을 책임지는 핵심 장비로서 국제해사기구(IMO)에 의해 선박의 의무 탑재가 규정되어 있다. 기존의 ECDIS 사이버 보안 연구는 주로 GPS 스푸핑이나 AIS 데이터 위변조와 같은 외부 입력 데이터의 무결성에 초점을 맞추어 왔다. 그러나 입력 데이터가 정상적이라 하더라도, 시각 정보를 최종적으로 생성하는 S-52 표시 라이브러리가 변조될 경우 항해사는 왜곡된 정보를 인지하지 못해 치명적인 해양 사고로 이어질 가능성이 있다. 본 연구에서는 오픈소스 ENC 소프트웨어인 OPENCPN을 활용해 가상 실험 환경을 구축하고, S-52 라이브러리의 심볼 조회 테이블(Lookup Table)을 직접 수정하여 위험물(예: 암초)을 안전지대(예: 안전 수심)로 위장하거나 항로를 잘못된 위치에 표시하는 공격을 수행하였다. 이를 통해 기존 ECDIS 보안 연구의 범위를 데이터 수신 단계에서 시각화 단계로 확장하고, 실험을 통해 시각적 변조가 실제 항해 안전에 미치는 위협을 실증적으로 입증하였다.


## 1. Introduction
선박의 안전 운항을 위한 항해술은 기술의 발전에 따라 비약적으로 발전해왔다. 그 중심에는 전자해도표시시스템(ECDIS, Electronic Chart Display and Information System)이 있다. ECDIS는 종이 해도를 대체하여 전자해도(ENC, Electronic Navigational Chart)를 기반으로 선박의 위치, 항로, 주변 위험물 등 다양한 항해 정보를 실시간으로 통합하여 표시하는 디지털 시스템이다. 국제해사기구(IMO)는 SOLAS 협약에 따라 일정 규모 이상의 선박에 ECDIS 탑재를 의무화하며 그 중요성을 공인하였다.
ECDIS가 방대한 양의 ENC 데이터를 모든 선박에서 동일하고 일관된 방식으로 표시할 수 있는 것은 국제수로기구(IHO)가 제정한 S-52 표시 표준(Presentation Standard) 덕분이다. S-52는 ENC의 객체(Object)와 속성(Attribute) 정보를 어떻게 화면에 시각적으로 표현할지에 대한 규칙, 심볼, 색상 등을 정의하는 '표시 라이브러리(Presentation Library)'를 포함한다. 즉, S-52는 ENC 데이터와 항해사 사이의 최종적인 정보 전달 인터페이스로서, 항해사의 정확한 상황 인지와 의사결정을 위한 최후의 보루이다.
그러나 선박을 대상으로 한 사이버 위협이 증가함에 따라 ECDIS의 보안 취약성 또한 주요 문제로 대두되었다. 기존 연구와 보안 대책들은 주로 S-63 데이터 보호 스킴을 통한 ENC 데이터 암호화, 또는 GPS 스푸핑(Spoofing), AIS(선박자동식별장치) 데이터 변조 등 시스템 외부에서 입력되는 데이터의 신뢰성에 집중되어 왔다. 이는 분명 중요한 부분이지만,  본 연구는 공격자의 입장에서 ECDIS의 시각화 직전 단계인 S‑52 표시 라이브러리의 변조와 같은 최종 시각화 단계 보안의 취약성을 지적하며, 그 위협을 실증을 통해 입증했다.

본 연구는 다음과 같은 세 가지 주요 의의를 갖는다.
1. **새로운 위협 벡터 제시:** 기존의 데이터 입력 단계의 위협을 넘어, ECDIS의 '표시 계층(Presentation Layer)'을 직접 공격하는 새로운 사이버 위협 모델을 구체적으로 제시하고 분석한다.
1. **위험성의 실증적 입증:** 이론적 가능성에 머무르지 않고, 오픈소스 ECDIS 환경에서 실제 라이브러리 변조를 통한 공격을 시연함으로써 그 위험성을 명확하고 실증적으로 입증한다.
1. **실용적 보호 방안 제안:** 발견된 취약점에 대해 무결성 검증과 같은 구체적인 기술적 대응책과 절차적 개선 방안을 함께 제시하여 ECDIS 시스템의 실질적인 보안 수준 향상에 기여한다.



## 2. Literature Review

### 2-1. ECDIS에 대한 사이버 보안 연구 동향 및 한계
ECDIS는 선박 운항의 핵심 시스템으로서 다양한 사이버 위협의 표적이 되어왔다. 관련 선행 연구는 주로 다음과 같은 분야에 집중되어 있다.
첫째, **센서 데이터 스푸핑(Spoofing)** 연구이다. GPS 신호를 조작하여 선박의 위치 정보를 왜곡시키는 GPS 스푸핑 공격은 ECDIS에 표시되는 선위(Ship's Position)를 거짓으로 나타내 항해사를 기만할 수 있다. 다수의 연구가 GPS 스푸핑의 가능성과 탐지 기법에 대해 다루었으며, 이는 ECDIS 보안의 가장 기본적인 연구 분야로 자리 잡았다.
둘째, **네트워크 기반 공격** 연구이다. 선내 네트워크나 위성 통신망을 통해 ECDIS 시스템에 침투하여 악성코드를 감염시키거나, AIS, RADAR 등 연동 장비로부터 수신되는 데이터를 변조하는 시나리오가 연구되었다. 특히 AIS 메시지를 조작하여 '유령 선박'을 출현시키거나 다른 선박의 정보를 왜곡하는 공격은 충돌 위험을 야기할 수 있어 주목받았다.
셋째, **시스템 자체의 취약점** 연구이다. ECDIS가 주로 사용하는 Windows와 같은 범용 운영체제(OS)의 보안 취약점을 이용한 공격 가능성, 또는 USB와 같은 이동식 저장매체를 통한 악성코드 감염 경로에 대한 연구가 수행되었다.
이러한 선행 연구들은 ECDIS로 **'입력되는'** 정보의 무결성 또는 시스템 '외부'의 위협을 방어하는 데 초점을 맞추고 있다. 즉, `데이터 수신 → 데이터 처리 → 데이터 표시` 과정에서 '수신' 단계의 보안에 집중한다. 그러나 정상적인 데이터가 시스템 내부에 들어온 이후, 최종적으로 '표시'되는 과정에서 발생하는 정보의 왜곡 가능성은 거의 다루어지지 않았다. 본 연구는 바로 이 **'데이터 표시' 계층의 무결성**이라는 미개척 영역을 다룬다는 점에서 기존 연구와 근본적인 차별성을 가진다.

### 2-2. S-52에 대한 사이버 보안 연구 동향 및 한계
S-52 표준 자체는 IHO에 의해 제정된 기술 규격으로, 그 목적은 '정보의 일관된 시각화'에 있다. 따라서 S-52와 관련된 연구는 주로 표준의 개정 내용 분석, 효율적인 렌더링 엔진 구현, 사용자 인터페이스 개선 등 기술적 구현과 사용성에 초점이 맞춰져 왔다. 사이버 보안 관점에서 S-52를 직접적으로 다룬 연구는 찾아보기 매우 어렵다. 일부 연구에서 S-63 암호화 표준을 언급하며 데이터 보호의 중요성을 강조하지만, 이는 ENC 데이터 파일의 불법 복제나 변조를 방지하기 위한 것이지, 해당 데이터를 해석하고 그리는 S-52 표시 라이브러리 자체의 보안을 위한 것은 아니다. 결론적으로, **S-52 표시 라이브러리를 사이버 공격의 직접적인 벡터(Vector)로 간주하고 그 보안 취약성을 분석한 선행 연구는 전무한 실정이다.** 이는 보안 연구 커뮤니티가 '데이터'의 보안에는 집중했지만, 그 데이터를 해석하는 '소프트웨어 로직(S-52 라이브러리)'의 보안에는 상대적으로 무관심했음을 시사한다.


## 3. EXPERIMENT
3.1 방법론
본 연구는 그림 1과 같은 방법론으로 실험 환경을 구성하고 그 가능성을 실증했다. 먼저 IHO S‑52 기술 문서를 분석해 `chartsymbols.xml` 파일의 lookup table이 시각 정보 생성의 핵심 요소임을 확인하고 이를 공격 대상으로 선정한다. 다음으로 OpenCPN 5.10.2‑0 환경을 구축하여 취약 파일인 `s57data/chartsymbols.xml`을 이용한 수중 장애물(OBSTRN) 은닉 시나리오를 구체화한다. 이어서 원본 파일을 백업하고 ‘OBSTRN’ 심볼 정의를 주석 처리한 조작 파일로 교체한 뒤 OpenCPN을 실행하여 심볼이 사라지는 것을 시각적으로 검증한다. 마지막으로 공격 전·후 화면을 비교 분석해 시각 왜곡의 위험을 입증하고, 이 결과를 바탕으로 파일 무결성 검증 강화, Secure Boot 도입, 정기적 소프트웨어 공급망 감사 등의 보호 방안을 제안한다.




![Untitled_diagram___Mermaid_Chart-2025-07-06-003931](_assets/Untitled_diagram___Mermaid_Chart-2025-07-06-003931.png)


![image](_assets/image.png)


![image](_assets/image.png)


### 3.1 시각 정보 변경의 위험성 연구 및 실험 환경에서의 실증
본 장에서는 3장에서 설계한 연구 방법론에 따라 S-52 표시 라이브러리 변조 공격을 직접 실행하고, 그 위험성을 실증적으로 증명한다. 또한, 발견된 취약점에 대응하기 위한 보호 방안의 개념을 연구한다.
본 실험의 목표는 오픈소스 ECDIS 소프트웨어인 OpenCPN을 대상으로 `chartsymbols.xml` 파일 변조를 통해 주요 항해 위험 정보인 '수중 장애물(OBSTRN)'을 은닉시키는 것이다.

**1. 실험 전 정상 상태 확인 (Before Tampering)**
먼저, 변조되지 않은 순정 상태의 OpenCPN(버전 5.10.2-0)에서 수중 장애물(OBSTRN)과 난파선(WRECKS) 객체가 포함된 전자해도를 로드했다. 그 결과, 해도 상에 수중 장애물과 난파선을 나타내는 심볼(Symbol)이 정상적으로 표시되었으며, 해당 심볼을 클릭하면 상세 속성 정보를 조회할 수 있었다. 이는 항해사가 잠재적 위험을 시각적으로 명확히 인지할 수 있는 정상적인 상태이다.

**2. 파일 교체(File-Swap) 공격 실행**
3장에서 설계한 '파일 교체를 통한 수중 장애물 은닉' 공격을 수행했다. 그 과정은 `chartsymbols.xml` 파일의 사본을 만든 뒤, "OBSTRN" 키워드를 포함한 모든 lookup 블록을 주석 처리하고, 원본 파일을 삭제한 후 수정된 사본의 이름을 원래 파일명으로 바꿔 교체하는 것이다.

**3. 실험 후 비정상 상태 확인 (After Tampering)**
공격 실행 후, OpenCPN을 재시작하여 동일한 전자해도를 다시 로드했다. OpenCPN은 파일의 내용이 변조되었는지 확인하는 절차 없이, 단순히 파일명이 일치한다는 이유만으로 공격자가 수정한 `chartsymbols.xml` 파일을 신뢰하고 로드했다.
그 결과, **해도 상에서 수중 장애물(OBSTRN)을 나타내는 모든 심볼이 완전히 사라진 것**을 확인할 수 있었다. 반면, 의도적으로 수정하지 않은 난파선(WRECKS) 심볼은 여전히 정상적으로 표시되었다. 이는 공격이 매우 선별적이고 은밀하게 이루어질 수 있음을 증명한다. 항해사는 어떠한 경고 메시지 없이 위험 요소가 제거된, 거짓으로 안전해 보이는 해도를 보게 되는 것이다.


### 3.2 보호 방안 연구
본 실험을 통해 증명된 취약점은 OpenCPN이 S-52 표시 라이브러리의 무결성을 검증하지 않는다는 근본적인 설계상 허점에서 비롯된다. 이에 대응하기 위해 다음과 같은 기술적·절차적 보호 방안을 연구하고 제안한다.
**1. 기술적 보호 방안**
- **무결성 검증 메커니즘 도입:** S-52 라이브러리 파일(`chartsymbols.xml` 등)에 대한 암호학적 해시(예: SHA-256) 값 또는 디지털 서명을 ECDIS 소프트웨어에 내장하거나 안전하게 보관해야 한다. ECDIS가 시작될 때마다 라이브러리 파일의 해시/서명을 계산하여 저장된 값과 비교하는 절차를 추가해야 한다. 만약 값이 일치하지 않을 경우, 화면에 치명적 오류 경고를 표시하고 해당 라이브러리의 로드를 거부하거나 안전 모드로만 동작하도록 제한해야 한다. 이는 "해시 또는 서명 검증 없음"이라는 취약점을 직접적으로 보완하는 핵심적인 방안이다.
- **필수 파일 존재 여부 검사:** 현재 OpenCPN은 XML 파일이 없어도 실행되는 문제가 있다. S-52 라이브러리와 같은 핵심 파일이 존재하지 않을 경우, 시스템 실행을 중단하고 사용자에게 명확히 알려야 한다.
**2. 절차적 보호 방안**
- **안전한 소프트웨어 공급망(Supply Chain) 관리:** ECDIS 제조사는 소프트웨어 및 관련 라이브러리 업데이트 시, 반드시 암호화된 보안 채널을 통해 배포하고 각 파일의 무결성을 사용자가 검증할 수 있는 수단(예: 해시 값 공개)을 함께 제공해야 한다.
- **사용자 보안 인식 교육:** 항해사를 대상으로 ECDIS 시스템 정보가 조작될 수 있다는 가능성을 인지시키고, 의심스러운 점(예: 평소에 보이던 심볼이 보이지 않음)이 발견될 경우 RADAR, 예비 ECDIS 등 다른 항해 장비와 교차 검증(Cross-check)을 수행하도록 절차를 마련하고 훈련해야 한다.

## 4. Results and Discussion

### 4.1 Experimental Result
본 연구의 실험을 통해 S-52 표시 라이브러리(`chartsymbols.xml`)의 핵심 내용을 조작함으로써, 전자해도 상의 특정 항해 정보를 사용자 몰래 은닉시키는 것이 기술적으로 가능함을 성공적으로 입증했다. 공격자는 수중 장애물(OBSTRN)의 심볼 정의를 주석 처리하는 간단한 방법으로 해당 위험 요소를 화면에서 완벽하게 제거할 수 있었다.
이러한 공격이 성공한 근본적인 원인은 실험 대상인 OpenCPN v5.8.0이 **표시 라이브러리 파일에 대한 해시/서명 검사를 수행하지 않고, 파일이 없어도 프로그램이 실행되며, 단순히 파일명만 일치하면 내용을 신뢰하는** 등 총체적인 무결성 검증 체계를 갖추고 있지 않기 때문이다. 이로 인해 발생하는 결과는 매우 치명적이다. 항해사는 위험이 제거된 것이 아니라 단지 보이지 않을 뿐인 '깨끗한' 해도를 신뢰하여 항해하다가, **수중의 암초와 충돌하여 선체 손상, 전복 및 침몰, 인명 피해, 그리고 막대한 경제적 손실로 이어지는 대형 해양 사고**를 유발할 수 있다.


### 4.2 Discussion 및 한계 나열
본 연구의 결과는 ECDIS 사이버 보안에 대한 중요한 시사점을 제공한다. 기존의 보안 패러다임이 S-63 암호화나 GPS 스푸핑 방지 등 '데이터' 자체의 신뢰성에 집중했다면, 본 연구는 **'데이터의 시각화' 단계 역시 치명적인 공격 벡터가 될 수 있음**을 명확히 보여준다. 이 공격은 정보 전달 체계의 가장 마지막 단계, 즉 인간-기계 인터페이스(HMI)를 직접 기만하여 항해사의 인지 자체를 왜곡시킨다는 점에서 매우 교활하고 위험하다. 따라서 향후 ECDIS 보안 표준과 인증 요구사항은 데이터의 무결성뿐만 아니라, 그 데이터를 해석하고 표시하`는 소프트웨어(Presentation Library)의 무결성까지 반드시 포함해야 할 것이다.



## 5. References
**[1] International Hydrographic Organization (IHO)**, *S-52 - Specifications for Chart Content and Display Aspects of ECDIS*, Edition 6.1.1, 2015.
**[2] International Maritime Organization (IMO)**, *Revised Performance Standards for Electronic Chart Display and Information Systems (ECDIS)*, Resolution MSC.530(106)/Rev.1, May 2024.
**[3] NIST**, *Guide for Conducting Risk Assessments*, NIST SP 800‑30r1, Sep 2012.
[4] **European Maritime Safety Agency (EMSA)**, *Guidelines on Cybersecurity Onboard Ships during Audits and Inspections*, 2022.
**[5]** F. Yüksel, “ECDIS Cyber Security Dynamics Analysis Based on the Fuzzy‑FUCOM Method”, *Journal of Marine Systems*, 2023.
**[6]** S. Andersson, “ECDIS Implementation of Annual Performance Test (APT): A Reliability‑Centered Maintenance Adaption”, MSc Thesis, Chalmers University, 2016.
**[7]** IHO, *S‑52 Presentation Library Annex A, Addendum to Part I*, Edition 4.0.3, 2020.
