---
notion_url: https://www.notion.so/506be0809830829394ed01ef05a2055b
last_synced: 2026-06-11 19:53
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4667223TOBU%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T105312Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDIaCXVzLXdlc3QtMiJIMEYCIQDAqobTzu5Xtd6phY9BJpxvRWZ7%2F%2BsaY71eat6JgsCQmwIhAOOijkTqaJfCMFVYvyMi987bIMWvn8Ugni%2B0yasjfFDVKogECPr%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNjM3NDIzMTgzODA1IgwebSrh3v5YXLNE%2FRwq3AMcBVUXOibUF7K87V0hNyDhjLITdEKAUZKz%2BKFaBGTJVgZqla9y1WzmGqn8xIjD5i5k5q7z4lthNP8QeNWGlRiTl%2BFI6aRzAUCoG6Xixr%2FoP%2FzgPxMy%2BKy9SL8nfUWeBXiF2FuTIMkOtc%2Bs8n%2FdP12XTRKxIENII5NxSr7%2FClfHT%2BhBJgY2Zzjfhq309pcas7rnF3iqDURIlwfCArJhyV1sW4Sx78BiwRw9hcd6bZ9bAZttSK7FpIxwUet22lW%2F%2FNZg35Sp7xLoMQBiZcat9R%2F4cYHdZR%2F1XXF7sqAzODLKG4gKGJb8O%2FLgL4Xl6nPvL8KTGJywJqHt%2Fd8ffzqrptrmJtWkoUxevpstVjkhytKVBLFm2cfmbGsuhfBe9%2FG80E0Hg84sJ%2BELD%2FUxXQYKHD%2Fr8NRvtjCu3ekN8KMjhxwhZpafqnITPIrsV0%2BuasxypgPQgCQiPXcZOUBr0bo%2FUs8a6nZgFDZkixUfgVtIgMAyGs3E4chjANPJf%2BpI7VChK9TLdUWJKDk8LrlisuZoINtuzDppFwA%2Blt%2F9L%2B9MFrMNoBSGkqXweHiLbSSc8BWqkywhNQOeCY8x9RHDpqVG47M3zsP4%2FDFL2ax14ncmXlHGuJyeQ6Rmf7CWc6mYkDCX%2F6nRBjqkAVH7%2B2XlgfyBmyKHS5Dg8YxM7SlmhfNRarNVRZZ6VCwHgm0%2F4cA8x8bcjx8Z3pruvy1EfcCjkthv6d1EuznqwSikLYcvxZh39lCS2Gzu5rhL3kxlhTkuHJBcyKL%2BL6%2FoQkdGcJVwl3LuGCGrMRNqbgNInCG1L%2BZ6m4yeEy0hTKu309BR%2BBd%2ByBwUCuvZQWlsqTeauDCdves9uH%2FuKsNouFqR9SwS&X-Amz-Signature=1df262db2eb5f5e63173c15f8f5a97d6e9483a948467147f01de870859c74275&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
