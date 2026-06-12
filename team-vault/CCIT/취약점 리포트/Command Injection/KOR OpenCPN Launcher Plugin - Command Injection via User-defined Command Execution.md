---
notion_url: https://www.notion.so/506be0809830829394ed01ef05a2055b
last_synced: 2026-06-12 13:50
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466S2E4RLE2%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T045024Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJGMEQCIG%2FO7AT%2F07lwFnT96Ia1QnzspnrvSBHYnOoZNRgVKgHpAiA%2B%2BsAZxXQM%2FzBZNY822XnCwGa9%2BRn2wL6eScIRwO2bryr%2FAwgNEAAaDDYzNzQyMzE4MzgwNSIMTDl37tue50ucfsWyKtwDulrIWAFDU1R7oHgh8gJnAla7Tfks5TXy9bQ98xqetQMK%2FNOPJCgKxXwoaU7bAnk%2BuUTKlLtAJUKBeeBz%2FhPM2R0UgObsl66%2F2rFj1a3lLZajlCg81HJfDibU9uGqi2r9X5W4aLFFigU7lBDyrtgOUHRnoQ6lsm3uv0ZnPlOwPtO8F7o4NFxpeUBgGHAhPUSsqw8WIilwQHe%2FCgW9I84OCfmH0fRlWEH0r%2FG667MK6EOEAVUJPFcFzLkSFhWk3ImvB9xjS9CzKjqcrNP3uBTX%2BALv%2BWlAiRPGM5DPvz%2Fg%2FfWwNUiFF1dDGTfT1aB3DuAuWlo1L7Uwl5Axa%2Bb%2FRHDHtPxWLRPHY2E1Z6ECa4CzScCrUF851B%2FJgknADAdJxhmxxgTsx4yfg7QoReLP1x%2FlHm8x5zNMaFmMZc1DoVYRfap%2B%2FlAljM3fDz3WOIphaXtRRiEgzAa9BV4EAzYvPwqjUk3QuIqqgghCH3El0%2FZb2KXzxcm2aAPMpeMsHBi8kDZnqkXgKD%2FleYqzOSsnEDAfMsJoWO%2BogQto5IDTrriQdm%2FrUO1vfQNz%2FATwdR976qyC%2FaAcedLTejFaOdFJoilGSO6MenNFFv%2BUnVMQwhk6R%2FyCspOiOibFfX3Ap8IwmPyt0QY6pgFjG%2BjIswFR%2Bwrn5IZ8DN2IBuIecioTRKcaXSQg5SYgLZ4xk1gEFDI5sp68vX%2BS2RdqagK5PcNwE5ZwXISH3ikQ4ueimq8RkdTgmVSCOcF%2BgVYIPcC86c4O5xzo8iyRra2fDHZQIIzFPCJhPcYTv7Tilyhbmuaf%2BHP7dmSAwjXnDP96jcV%2FxcPl10WUVkWZPBLve7HfCtnNQQklbODn7b8IyJew%2FZQY&X-Amz-Signature=e279d3dad9e31b0132fbe85b8d1950c75a932109998f311290593f8414913419&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
