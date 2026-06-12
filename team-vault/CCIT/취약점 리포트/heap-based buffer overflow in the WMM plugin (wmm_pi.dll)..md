---
notion_url: https://www.notion.so/405be08098308307ad0181fb40fa2e6a
last_synced: 2026-06-12 18:07
tags: [notion-sync]
---

# heap-based buffer overflow in the WMM plugin (wmm_pi.dll). 

1. 취약점 제목 (Vulnerability Title)
  OpenCPN 5.12.0 WMM 플러그인 .COF 파일 파싱 과정의 힙 기반 버퍼 오버플로우 취약점
2. 요약 (High‑level Overview)
WMM(World Magnetic Model) 플러그인은 .cof 헤더를 파싱하면서 edit_date필드의 길이를 검증하지 않습니다.
헤더가 예상 길이를 초과하면 힙 버퍼가 덮여 RtlFreeHeap → RtlpCheckBusyBlockTail() 경로에서 크래시가 발생하며,
사용자 제어 문자열(예: 0x41414141)이 힙 메모리를 오염시켜 임의 코드 실행 가능성이 있습니다.
3. 영향 받는 제품 및 버전 (Exact Product & Version Info)
제품명 : OpenCPN
버전 : 5.12.0 (공식 Windows 32‑bit 릴리즈)
플러그인 : WMM_pi.dll (World Magnetic Model)
운영체제 : Windows 11
취약 바이너리 : opencpn.exe (+ wmm_pi.dll)
분석 도구 : WinDbg (x86)
4. Root Cause Analysis
a. 취약점 상세 설명 (Detailed description)
snprintf(header_fmt, sizeof(header_fmt), "%%lf %%%lus %%%ds",
(unsigned long)sizeof(MagneticModel->ModelName), date_size);
fgets(c_str, sizeof(c_str), MAG_COF_File);
sscanf(c_str, header_fmt, &epoch, MagneticModel->ModelName, edit_date);
edit_date 버퍼보다 긴 문자열이 sscanf를 통해 그대로 복사되어 힙 영역을 초과합니다.
b. 입력 → 취약 조건 코드 흐름 (Code flow)
.cof 파일 → WMM_pi::ReadMagneticModel() → 위 sscanf 호출 → 힙 오염 →
free() 시 RtlFreeHeap → RtlpCheckBusyBlockTail() 에서 비정상 블록 감지·크래시
c. 버퍼 크기 / 주입 지점 (Buffer size, injection point)
항목   값
입력 파일   .cof
취약 필드   edit_date
요청 크기   20 바이트 (WinDbg 보고)
오염 주소   0x11E55984(ASLR 걸려있어서 실행 시 주소는 매번 바뀜)
덮인 값   0x41414141 (‘A’)
d. 수정 제안 (Suggested fixes)
sscanf 대신 길이 제한 함수 이용 또는 수동 파싱 후 길이 검사
edit_date버퍼 크기 초과 입력 시 파싱 중단
ASAN/Heap guard 옵션 활성화로 빌드하여 조기 탐지
5. Proof‑of‑Concept (PoC)
a. 첨부 파일
crash.cof : ModelName에 0x80 바이트 ‘A’ 삽입한 PoC 파일 (파일 첨부 예정)
b. 실행 절차
OpenCPN 5.12.0 설치 후 WMM 플러그인 활성화
  WMM.COF내부의 Modelname을 32byte를 넘게 설정 →
OpenCPN 실행 → 플러그인 초기화 시 즉시 크래시 (WinDbg 첨부 시 힙 오염 확인)
6. 소프트웨어 다운로드 링크 (For vetting purposes)
공식 사이트: [https://opencpn.org/OpenCPN/info/download.html](https://opencpn.org/OpenCPN/info/download.html)
