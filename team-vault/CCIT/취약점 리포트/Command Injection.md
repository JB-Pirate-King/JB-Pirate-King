---
notion_url: https://www.notion.so/fdbbe080983082b48fa201e011068681
last_synced: 2026-06-12 09:07
tags: [notion-sync]
---

# Command Injection

- 📄 [[KOR OpenCPN Launcher Plugin - Command Injection via User-defined Command Execution/KOR OpenCPN Launcher Plugin - Command Injection via User-defined Command Execution|KOR OpenCPN Launcher Plugin - Command Injection via User-defined Command Execution]]

Vulnerability Title: OpenCPN Launcher Plugin - Command Injection via User-defined Command Execution
Vulnerability Summary: The Launcher Plugin in OpenCPN executes user-defined commands by directly passing them to the system shell without proper filtering. This allows attackers to inject arbitrary shell metacharacters and execute unintended commands, resulting in a Command Injection vulnerability.
Vendor: GitHub Open Source Project
Software Name: OpenCPN
Version: OpenCPN 5.12.0, Launcher Plugin v1.3.5 
Software Type: ECS (Electronic Chart System)
Attack Type: Command Injection
Impact: Arbitrary Code Execution
Vulnerable File Name: `launcher_pi.cpp` (`nohal/launcher_pi.cpp`)
Vulnerable Function Name: `LauncherUIDialog::OnBtnClick`
Vulnerable Parameter: `wxExecute(cmd, wxEXEC_ASYNC)`
Vulnerable Environment: Windows

Proof of Concept:
The following code in the OpenCPN Launcher Plugin demonstrates the command being executed through the shell without any filtering:

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

Because the command is passed directly to the shell, it is possible to inject shell metacharacters (e.g., `&`, `|`, etc.) to chain and execute multiple arbitrary commands.

Additional Materials (video, report attachments):

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466TVBQ3N63%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T000751Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEAaCXVzLXdlc3QtMiJHMEUCIQC7Z3bDsHoKQfQgFeOPTg%2FuToc3dopCphHWeotbwtCNqAIgUPISk0VMqoBwL1mFMUbdSWS%2FTx%2BxDkGGHlUMNKOk3Qgq%2FwMICRAAGgw2Mzc0MjMxODM4MDUiDFKDh2lgmY2UkkrYqCrcAxaaa6YGxljces9%2FxIMZtXxQmqUtuRIsYwYx1bkl3%2B2X5XM0MCj5q3eW3XZQHzt9PZT5fljNOQhS1%2B4j3l29%2BciLEJodpAmBiaURiet2%2FW6zOs%2BfHCpR4qSbOUNpkQo5%2FGcpLxJJ4i5rMGhaJCh54so1E4Do99GbvHhd1JIJ%2FjL3BZwrS1bwpjRv4F6b0o%2BHMuimEKdf%2FgkFYDOJiXsoLnbyzAyacFwhUCOROLSVHwQu2j42Qxzr6W7ZUwDixpoDxBkqVijKnHMiHIgHOHRLcJcXOMiWbmUlar3JHNC5C9fsRyhnegYdJjIvTtC8hA50yuF4CAQKy95nAwX3tBobjAq0on8NzHAk7qjgrsWAq35fLzjOJgcD8m3xZADZJHEbde0DNWeAyozWHz3dgoZXj70xHpRuJ3%2BLFAVFNmw5pIqZEcAccKJse%2FumHU9N0ZNIYK0kChz2mB2pUeali%2Bs7CPuT51IejP9t%2Bk4b6YKyyE3VTuvgPau0%2FNm%2FFBQ7mii5pnfp86mygv50ICc3tEkW9VB%2FQTPMx%2BbHu1QzAK7rdtJSbMEzvwFwVsH%2FzET1dTvC0tvCS2mdbN266NpH6sL9Nd%2BmE7yhoRC0021X4IKBJLyt8ijMz9N1nNtDuWTQMLSPrdEGOqUB7vEzCpENj8u9PT4EDq9M5epIgfOb2clH%2B08sL8Wp2UIXgoADc0P6bZ2%2Bcf2%2FEajC7tzLIffnG0tRUNqBPkntCUFECWQ9u19zzqW2gJL0nt1jhESngeAqyA6zaqiNGpM7TIX%2Btqiyl%2BllOCqKhsaKUQ%2BbcegY2t%2B6DDiJAO%2FmiGfjD8LshH5MV0ONNBBh9u%2FIBASU942cX83bt6NygFXLSHpbVdrw&X-Amz-Signature=e504268fb5a8ca79e6317de0da16ab07407b20a038d80405d73f8f9cff6accd7&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)


- 📄 [[OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점/OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점|OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점]]
