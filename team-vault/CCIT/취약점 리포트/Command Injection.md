---
notion_url: https://www.notion.so/fdbbe080983082b48fa201e011068681
last_synced: 2026-06-11 19:53
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466ZYGE37SQ%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T105309Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDIaCXVzLXdlc3QtMiJHMEUCIED8dMgfYfW0TXT%2BagRDrqX4y122KiK0lsJeHAM6mvuvAiEAmH%2FRui1aq8wNtsBjfXLvhv6vFsyGi90NWsrFN4SBqAYqiAQI%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw2Mzc0MjMxODM4MDUiDI12R4MkApONS76rHCrcA%2FYcpbYv90PK1R0%2BP3Q5TVnycIzgIG8Rh97tQlDC3hc9Qqy4r7%2FaaPhMZH0vimFZ2sK0%2FFxuQookRxe3dw5k%2B4h%2F015QY%2F98onBSpq6fKrlNV0n8DXPNiWijJT9lMRATbt7PseOGCXrXlIiSwGOJQ8Osx3%2FbzMSfIrAsCevpAgsZXiM4mfek1FyV%2FBLOqw%2FT21PVRY1giUdEdt2dp9tUosyWvabE0ypzf002%2BlTpCLg%2BSX%2F4JubvqkdFgeVVk0vRvd%2Bt6YDGtWiMWeoJYZEpnTNLOP8KC32qTRqdUc6t66prO8z9PNubc9BWAwoRIIKGeoC63vNfX4G0%2BsQtWbH87MYny9G6Bc1vENvNiopC6JzDEYIuNaM9a8FvfS6Ym3nN1BaNezwE%2BO%2BD1ebpQrgqe5uJzYbEzS7sDwz0zVp5gAKE%2FbLYgYBxqwfyrIk1ZjMmZkQ0nG%2Bc1OUCKTEnKyKt4D3I98xjwHBj3xUgB1cYRMimtPp99uMOAeCwA27JUMTBk%2BFzDrKcfE3U1w4hWDmySWXsGHaw3kGsAmQQSssqBgb262YQ9dCqPMtKAQosLtNvZPmQtXOGhNk3zkQL8MrqCex46MrI6Yv0HW8rCZp5NvYbkOZIG0XT4Ah7PfpuMP7%2BqdEGOqUBJJaO%2FpiVXtHgZ5mLw49P3TXZ98NSyY5AkrtM1rgvObmqEHdBcfopgFTE3OkMgMC%2B%2FW3KgxxRtij5yducpaOm0zrz8VCocrZ4sYfxjOehorOvCMN346DV7Sd2N4Zw5S5ZDlkPRwhTsV%2FWntQtchxpgq3rmIviDI4XlN5I74se%2Fw1UW4rir6FfZrJ6yI9BliHQWReZILnnz9eb1johHsQYjF50Vc6X&X-Amz-Signature=5dbac2dbc6b1022fec267facf8aa98534b2857d8bf74c5b41187a7660e528afb&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)


- 📄 [[OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점/OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점|OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점]]
