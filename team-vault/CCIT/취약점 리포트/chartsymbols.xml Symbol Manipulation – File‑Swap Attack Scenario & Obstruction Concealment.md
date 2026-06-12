---
notion_url: https://www.notion.so/ba4be080983083169d3501564a31da69
last_synced: 2026-06-12 09:07
tags: [notion-sync]
---

# chartsymbols.xml Symbol Manipulation – File‑Swap Attack Scenario & Obstruction Concealment

**Vulnerability Title :** OpenCPN chartsymbols.xml File‑Swap Tampering Enables Obstruction (OBSTRN) Concealment
**Vulnerability Summary :** An attacker copies the legitimate chartsymbols.xml, creates a modified clone that comments‑out the  definitions for OBSTRN, deletes the original file, and places the tampered file under the original name. Because OpenCPN starts even when the XML is missing, trusts any file that simply has the expected name, and performs no integrity verification, the program loads the attacker‑supplied file and renders the ENC without any visual indication of underwater obstructions.
**Manufacturer :** OpenCPN Project (open‑source)
**Software Name :** OpenCPN
**Version Tested :** 5.8.0
**Software Type :** ECS / ECDIS navigation software
**Vulnerability Type :** Integrity Bypass (Resource File Tampering)
**Impact :** Vessel unknowingly collides with hidden rocks → hull damage, potential capsizing / sinking, loss of life, multi‑million‑dollar downtime, and destruction of high‑value cargo
**Vulnerable File :** s57data/chartsymbols.xml
**Validation Gaps :**
- No hash / signature check
- Program launches even if the XML is absent
- File name alone is accepted as trustworthy (no content validation)
**Proof Of Concept** :

![image](_assets/image.png)

Listing all OBSTRN lookup entries in chartsymbols.xml, then launching OpenCPN.


![imaged](_assets/imaged.png)


![image](_assets/image.png)


![image](_assets/image.png)

Before tampering, OpenCPN correctly displays all obstruction-related symbols (e.g., OBSTRN, WRECKS), allowing navigators to visually identify underwater hazards on the chart and query detailed metadata.



![image](_assets/image.png)

These commands copy the original file, comment out all lookup blocks that contain the keyword "OBSTRN", delete the original file, and rename the modified copy to replace it.


![image](_assets/image.png)

Listing all OBSTRN lookup entries in chartsymbols.xml after tampering.



![image](_assets/image.png)


![image](_assets/image.png)

After executing the program following the XML tampering, the warning symbol (×) representing OBSTRN no longer appears, while the WRECKS warning remains displayed—confirming that only OBSTRN entries were affected.




**취약점 제목:** OpenCPN chartsymbols.xml 파일 교체를 통한 OBSTRN(수중 장애물) 은닉
**취약점 요약:** 공격자는 정식 chartsymbols.xml 파일을 복사한 후 OBSTRN에 대한 정의를 주석 처리한 복제본을 생성하고, 원본 파일을 삭제한 뒤 수정된 파일을 원래 이름으로 교체한다. OpenCPN은 XML 파일이 없더라도 실행되며, 단순히 파일 이름이 예상된 이름과 일치하면 내용을 검증하지 않고 신뢰하여 로드한다. 그 결과, 프로그램은 공격자가 공급한 파일을 읽어 ENC를 렌더링하되, 수중 장애물(OBSTRN)을 화면에 전혀 표시하지 않는다.
**제조사:** OpenCPN 프로젝트 (오픈소스)
**소프트웨어 이름:** OpenCPN
**테스트 버전:** 5.8.0
**소프트웨어 종류:** ECS / ECDIS 항해 소프트웨어
**취약점 종류:** 무결성 우회 (리소스 파일 변조)
**영향:** 선박이 수중 장애물을 인식하지 못하고 충돌 → 선체 손상, 전복 또는 침몰 위험, 인명 피해, 수억 원 규모의 운항 중단, 고가 화물 파손
**취약 파일:** s57data/chartsymbols.xml
**검증 미흡 사항:**
- 해시 또는 서명 검증 없음
- XML 파일이 없어도 프로그램 실행됨
- 단지 파일명이 일치하면 신뢰함 (내용 검증 없음)
**Proof Of Concept** :

![image](_assets/image.png)

"chartsymbols.xml 파일에서 OBSTRN 관련 lookup 항목들을 모두 나열한 뒤, OpenCPN을 실행함.”


![imaged](_assets/imaged.png)


![image](_assets/image.png)


![image](_assets/image.png)

조작 이전에, OpenCPN은 OBSTRN, WRECKS와 같은 장애물 관련 심볼을 정상적으로 표시하여 항해자가 차트에서 수중 위험을 시각적으로 식별하고 세부 정보를 조회할 수 있도록 한다.



![image](_assets/image.png)

위 명령어들은 원본 파일을 복사한 뒤, "OBSTRN" 키워드를 포함한 모든 lookup 블록을 주석 처리하고, 원본 파일을 삭제한 후 수정된 복사본의 이름을 원래 파일명으로 바꿔 교체한다.


![image](_assets/image.png)

조작 이후 chartsymbols.xml에서 OBSTRN lookup 항목들을 모두 나열함.



![image](_assets/image.png)


![image](_assets/image.png)

XML 조작 후 프로그램을 실행하자 OBSTRN을 나타내는 경고 기호(×)는 사라졌고, WRECKS 경고는 그대로 표시되어 OBSTRN 항목만 영향을 받았음을 확인할 수 있다.
