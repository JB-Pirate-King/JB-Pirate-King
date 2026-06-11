---
notion_url: https://www.notion.so/506be0809830829394ed01ef05a2055b
last_synced: 2026-06-12 00:59
tags: [notion-sync]
---

# [KOR] OpenCPN Launcher Plugin - Command Injection via User-defined Command Execution

취약점 제목: OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점
취약점 요약: OpenCPN의 Launcher Plugin은 사용자가 정의한 명령어를 필터링 없이 운영체제 쉘에 그대로 전달하여 실행합니다. 이로 인해 공격자는 쉘 메타문자를 이용해 임의의 명령어를 주입하고 실행할 수 있어, Command Injection이 가능합니다.
제조사: GitHub Open Source Project
소프트웨어명: OpenCPN
버전: OpenCPN 5.12.0, Launcher Plugin v1.3.5 
소프트웨어 유형: ECS (Electronic Chart System)
공격 유형: 명령어 주입 (Command Injection)
영향: 임의 코드 실행
취약한 파일명: `launcher_pi.cpp` (`nohal/launcher_pi.cpp`)
취약한 함수명: `LauncherUIDialog::OnBtnClick`
취약한 파라미터: `wxExecute(cmd, wxEXEC_ASYNC)`
취약점 발생 환경: Windows

Proof Of Concept  : 
OpenCPN Launcher Plugin에서 필터링 없이 명령어를 쉘로 실행하는 코드를 확인

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

쉘 메타문자(&, | 등)를 이용하여 여러 명령어를 한번에 실행하는 Command Injection이 가능

취약점 기타 (파일 첨부 영상, 보고서 첨부) :

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466SIJX2VGY%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T155939Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDUaCXVzLXdlc3QtMiJHMEUCIDbNOEIgh4eBc2I0SK0pwfxQ342HXiFerSssRCGTIHjAAiEAusIVrD1HzGZs99zxngyh49QfcKsxybsNiR%2FDfnFJdoIqiAQI%2Fv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw2Mzc0MjMxODM4MDUiDB%2FLpyrKqRss%2BTcBVircA%2BTEyag6TsBrUFpcKdZw1m02cN%2FKqnBuD5n6b7vxzSkP%2B%2BqiPADmVR0AdIJ5PbGrbLKwB7g3yFENkYPjy%2FC26d1XuKOz5LxrPt6J8s%2BtF8XLT4wFmNjiaAA38rKX057j%2FOaLIjg3lQVSju2gxSRko%2BYwtLtDouRnGESxBew90DizBeJp8r4lz5fhtje4FHtM90KKwzAY51dx7uUWA%2FglXz%2FrwjE%2BbTU1X4v3zWeY17XOVOxaYdZ5EklGn1QgTR1pfId%2F%2FkwMwQaYiAseCP8SVlV1YK2RPOF2AmzkvrQir5TjSW4N9PJnquWQcBk8szM%2B9LzNmRuRK6rQ96%2BM6PoIguMCURIaTYjP4K1eBUjdy3LvkCyDQdB61erzQsRHR0csC%2FO9QgFRcDnjpUhXC12MlCMIrZvpjI8jv%2BXtTP88TkH2BW4Abo65tTws%2FRqPIri3ptc8koDBdFv8goPYH%2B4JkkngkFi45Fbw8Mw6NW2lqnHV80FwHlyAu1i5IHtKlyZBVfVEIiFbnCmtQOcuixJaAyyM8rWxFCK5ZL3BHaXElM6JPTnzkYEDzmNtwQBb3PLPFBD1RePUpr1EH7Y5uvcxTd7ioyhLi2IJKDepIWZjTtSpFO280CSB9HYQSzhfMLLbqtEGOqUB%2BOOyBx3zPDslaJtjolqGer4YxSjDkk0FpqYXED1GZrfLipE2%2BHunuv0iASqXmD1CwET78i9mq9YiqL%2FvTyTFtmQy7MDCZ9StIGEIEHwqrioi%2Bxc0v9T1tJ0%2Fwc1uCTDNIYmlrb2GzYBHUaCfKDkfe89F0TNQv80u1avf5aK5VbIBqq0YtmlDOkNfZkPt3WmKv0I93D4IITXaW1TEFzLfkYdDGTKS&X-Amz-Signature=dde9d7d01edb4111eaf1f19cc39575a16a40f16b7e9941e32b24dbdb88822431&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
