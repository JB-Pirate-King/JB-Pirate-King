---
notion_url: https://www.notion.so/506be0809830829394ed01ef05a2055b
last_synced: 2026-06-12 05:51
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466RMEU346Y%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205127Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJGMEQCIQD1NVtSYACYRZl8v9iLWFcspwauNzQZkUOOIf3aaU%2FICAIfW31mN5yvkCUQw2D0BLUa6xrO9zfldLIbsJyazRC2myr%2FAwgFEAAaDDYzNzQyMzE4MzgwNSIMTzbs%2Bg4yS3r1VnQKKtwDqTOFMdITOMKgxWmeow18l8ze6FBWRtZXRgF0l7qBqPqoSKD5bN%2FMqsigVBJhrrWxycXXqoTf5y7hQ64PttRNT5%2F1SCWpb%2BN1JaG3UAZndc7xBV0surfb5Bkw%2B37fdo9ytVCdDBvGRPfPMTt1p9AMq%2Fk1%2Br2t9Ep%2Bjjw%2F0tQKcL5X9I2j0xzs6C5745sS92WTJZpPZghVt54mu1cb2i86ks0ZIza%2FXl00z%2FlEMJ%2BsYEgG5uRO%2FXn113tjpYfHi8pOPQs6ar6G5hRxN7mEVRFudKYW3etM%2FJ65wYv8jyGCc2YokIuDGm1LBu2k6aGlXMMTbFvGN630v4ebX%2BGGkKW17q8xW3hsSpyjHJ%2Bo2PUYrmvrDx4uclsNvpIMQfb%2F8zXM21dq1maL5gt903rl8s37aErrj%2FYL4N4Usn%2FKz9LGXxaP3KIrFDJCOWyiM5pXvzQXpCpJV9D1QYdt45yBaZOn785R5EFWujELIW2eLzduY5BP2VkjK7pH0oHQ17EDj%2B%2B1aDjo%2Bsou2RUrfMEbwkKyq5FtuJyYuSTLrUGmsF9YyKQPNUWrSCcCFRUEqt%2FT1tN9i7gQuNCfI6YUpLyg%2BXqDuEWyLXMo9u7xWtE1p1xCp97BjNmsJJjP%2BH9UCckw%2F6ms0QY6pgHNvocDybaWE0w6XLzimjgRlj2vFy0DI3NH3gegf1wZqnvvfJbeggtUA3RUbmfsA02ClQZunaEWzVMXgfnVH25KmLyWA4WwfWXQ2Y7vOAgxNdwcojM6Cq76DFrxL6LBr9HzSykkIYc8Y9do1VMTHMPIfPZvesTjyAL1vH%2F2atrnlJ2BKrbAPEbCiPdivkLT3pFdwxj%2FhCsYIH0SKQaj7EOpazJocbUr&X-Amz-Signature=c642660d063f72e56d0803fb95b60292bdbfa3ce4523860170b72777a1c552d3&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
