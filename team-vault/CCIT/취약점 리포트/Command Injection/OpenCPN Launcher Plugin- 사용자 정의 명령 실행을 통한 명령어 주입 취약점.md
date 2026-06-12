---
notion_url: https://www.notion.so/a9dbe0809830837687040156b9e6cb8c
last_synced: 2026-06-12 13:50
tags: [notion-sync]
---

# OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점

### 1. 취약점 제목
OpenCPN Launcher 플러그인 - 사용자 정의 명령 실행을 통한 명령어 주입 취약점

### 2. 취약점 개요 및 가능한 영향
**취약점 설명**
OpenCPN의 Launcher 플러그인은 사용자가 OpenCPN 내에서 버튼을 통해 외부 프로그램이나 명령어를 실행할 수 있도록 설계되었습니다. 플러그인 설명에는 “외부 명령은 컴퓨터에 제공할 수 있는 모든 명령”이라고 명시되어 있으며, 이는 사용자에게 높은 유연성을 제공하는 동시에 심각한 보안 위험을 초래할 수 있습니다.
이 취약점은 사용자로부터 입력받은 명령어 문자열을 적절한 필터링이나 이스케이프 처리 없이 운영체제 쉘에 직접 전달함으로써 발생합니다. 이로 인해 공격자는 셸 메타문자(&, |, &&, ||, >, <, ", ' 등)를 이용해 의도치 않은 임의의 명령어를 실행시킬 수 있습니다.
영향
- 임의 명령어 실행: 시스템 명령어(powershell, rm -rf /, 리버스 쉘 등)를 실행 가능
- 권한 상승 및 시스템 장악: 관리자 권한 하에서 악성 코드 실행 시 전체 시스템 통제 가능
- 정보 유출 및 파괴: 내부 파일 탈취, 시스템 설정 변경, 파괴 가능

### 3. 취약점이 발견된 정확한 제품명 및 버전 정보
제품명: OpenCPN Launcher Plugin (`nohal/launcher_pi.cpp`)
영향 버전: OpenCPN 5.12.0-0 및 Launcher Plugin v1.3.5 (가장 최신 공식 released버전)
플랫폼: Windows (Poc 기준, 다른 플랫폼도 영향 가능성 존재)

### 4. 근본 원인 분석
**a. 취약점에 대한 상세 설명**
사용자 정의 명령은 opencpn.ini의 [PlugIns/LAUNCHER] 섹션의 Commands 항목에 저장됩니다. 이 명령어는 LauncherUIDialog::OnBtnClick() 함수에서 다음과 같은 방식으로 처리됩니다.

```c++
wxExecute(cmd, wxEXEC_ASYNC);
```

명령어 문자열은 사용자로부터 입력된 그대로 실행되며, 쉘 메타문자에 대한 필터링이나 이스케이프 처리 없이 운영체제에 전달됩니다. 이로 인해 악의적인 명령어 주입이 가능하게 됩니다.
1. `LauncherUIDialog()`

```c++
void LauncherUIDialog::OnBtnClick(wxCommandEvent& event){
	LauncherButton* button = (LauncherButton*)event.GetEventObject();
	if (m_hide_on_btn)
		this->Hide();
	wxString cmd = button->GetCommand();
	if (cmd.StartsWith(_T("KBD:"))) {
		SendKbdEvents(cmd);
	} else {
		cmd.Replace(_T( "%BOAT_LAT%" ), wxString::Format(_T( "%f" ), m_Lat));
		cmd.Replace(_T( "%BOAT_LON%" ), wxString::Format(_T( "%f" ), m_Lon));
		cmd.Replace(_T( "%BOAT_SOG%" ), wxString::Format(_T( "%f" ), m_Sog));
		cmd.Replace(_T( "%BOAT_COG%" ), wxString::Format(_T( "%f" ), m_Cog));
		cmd.Replace(_T( "%BOAT_VAR%" ), wxString::Format(_T( "%f" ), m_Var));
		cmd.Replace(_T( "%BOAT_FIXTIME%" ), wxString::Format(_T( "%d" ), m_FixTime));
		cmd.Replace(_T( "%BOAT_NSATS%" ), wxString::Format(_T( "%d" ), m_nSats));
		wxExecute(cmd, wxEXEC_ASYNC);
	}
	event.Skip();
}
```

**b. 입력에서 취약 지점에 이르는 코드 흐름**
1. `opencpn.ini` 설정 파일의 [PlugIns/LAUNCHER] Commands=... 항목에 사용자 명령어 정의
1. 플러그인은 이를 필터링 없이 읽어 GUI 버튼에 연결
1. 버튼 클릭 시 `wxExecute()`를 통해 운영체제 쉘에 그대로 전달
1. 결과적으로 악의적인 명령어까지 실행되는 Command Injection 발생
**c. 인젝션 포인트**
GUI 이벤트 핸들러에서 `wxExecute(cmd, wxEXEC_ASYNC)` 형태로 명령어 실행 지점이 존재
이 지점에서 입력된 전체 문자열이 그대로 shell에게 전달되어 실행됨
**d. 제안된 수정 방안**
(1) 플러그인 차원
- 화이트리스트 기반 필터링: 명령어와 인자에 허용 가능한 문자와 형태만 허용
- 쉘 메타문자 제거 또는 이스케이프 처리: &, |, > 등의 문자를 필터링 하거나 안전하게 인코딩
- 쉘 우회 실행 방식 적용: CreateProcess()등을 사용하여 쉘 호출 생략
(2) OpenCPN 플랫폼 측면
- 공식 플러그인 등록 및 배포 프로세스에 보안 코드 리뷰 및 정적 분석 절차 추가
- 명령 실행과 같이 잠재적 위험 기능을 포함한 플러그인에 대해 검증 프로세스 확실히 적용

### 5. 개념 증명 (PoC)
**a. ini 파일 조작**
`opencpn.ini` 파일 내 설정 변조

```plain text
[PlugIns/LAUNCHER]
Labels=Test
Commands=cmd.exe /C start "" "C:\\Windows\\System32\\notepad.exe" &"C:\\Windows\\System32\\calc.exe"
```

**b. PoC 실행 방법**
1. OpenCPN → Options → Update Plugin Catalog: master → Launcher Plugin 활성화
1. `opencpn.ini` 파일 내 설정 변조
1. OpenCPN 실행 후 Launcher Plugin의 “Test” 버튼 클릭
1. notepad.exe 실행 이후 calc.exe 실행

# 6. 소프트웨어 다운로드 링크
- [https://opencpn.org/OpenCPN/info/downloadopencpn.html](https://opencpn.org/OpenCPN/info/downloadopencpn.html)
