---
notion_url: https://www.notion.so/506be0809830829394ed01ef05a2055b
last_synced: 2026-06-12 09:07
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466VHCIQV2Y%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T000754Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEAaCXVzLXdlc3QtMiJGMEQCIDHo%2Fvz5kIWD5t4zZNQWVijWAFFxdWwj2lpHujfpbbKFAiAeGaZmBLlF5mwdS7G1HHx8r%2B6MYMaxlb5pXpAQePINlCr%2FAwgJEAAaDDYzNzQyMzE4MzgwNSIM%2B1kGZHJ9Olc2n8FlKtwDr2y0z79rWmfPWG%2BD72SDahrwLnsZQLAmpAnu4pTlxSbPNBvn0k7BBdLze%2Fbj3J8VmNhrbfHALbEApGGMdyfC0l2Ol7exYMHNsgb%2F0ZJzxmmVs4TUJ4RdlF9Nv%2BG%2F03w88cw4AjMZ1S2dfEtFTwd%2FwymtpR9Kl3DdqX5CF2wjr0Vh9k5Yb9BPi3f0CJK9tkaeGTor%2FB053dZoUc8mS6hFSe3dFSqSJql1Z7xLLYJerKTaScRYc947ZzIlVtKaTFecIuj9%2BFZbMBZuW9rLt1Y2OxsfDGUC8V93kKt94%2F99cAcKbEZADcnTDbtLTyb9JOhdHBe4aZZxgMfRrdlyQjMHFDPDVDodkZ%2BJa7oujASe9Iyz%2FbADjEr1a%2Fd6NlknXd4bFK1JRZ7hkJlJ1SAQZi41%2FebiVAIr%2BXgbSPqGPekfbxd2hLr9qEMQAYDGK9%2BIb7UQFt2J0AtbYr%2F99eMAYReo%2BhNs9KCtDde4EYmD4prBLI8KZUQ%2BBkHGaLfRajLzBF67n68ZG5zKCLw9HrzyARwZcLO%2BINFOrjRFFN5aYeSZ%2BKSkAYkiGckO1r2iC%2F7%2B%2FLCTeBLN5bfbqXyUdCV9fxEa3sCQ6mRGiRjfY4R0MvTP4KrXC9H9oKOQ%2FJ4u610w8Y6t0QY6pgF0NZpxA2HauezxpCV80TSFovVcofcTbcv%2BNwQ4cHTNE0VAs0ojuNui1Gd11QmIi7aDo0WpRFj3Irzvwd%2B3qoxBi3AfJqhQWTDWfAkRw5XF4gFXmiqC7ABsc%2BPjk9frvRBwZMPrO9oFqbxHmJd5SNsyDU985jod0k5eKUAuPNbEzhTDkRduvT6OSfSgeanNoKndqe7GknwnDJW9QR4XEz5k4qdBOgxR&X-Amz-Signature=f58efd172605e1f766cbb81e8f03fcb6d1f1df4f1dfe028c53601bf0cf31dfb7&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
