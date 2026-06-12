---
notion_url: https://www.notion.so/fdbbe080983082b48fa201e011068681
last_synced: 2026-06-12 13:50
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4666GKSLLAB%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T045021Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJHMEUCIAoQp7LE07P82YOJZsyZs2leox374OLW0QcdZQRvYUDgAiEAlE4ZoFnWQ3gb%2FhSEp5qcY%2F1dwxLNwatd6ONeC7BRB2Qq%2FwMIDRAAGgw2Mzc0MjMxODM4MDUiDLpOvoJH5Mk2bq1mzyrcAzRUZWoUm4JcexLXreeGSSWa3K1TCBHAGIHBMAFRnQSGs3jucMaMfbZqK%2BgtmLF6KvkuS6yi9OkC8rQv7vzTXPV7d968zCUtpD%2BMikvXxNFhGIFopOwpYt2clcy462FkelqhVnarv5RfgeuUjK9BWpo3xpuVnGjJG%2B5HUA0ZR9lqGR89HKfUYGKfqSn4GP%2BtZqKbCEk3Ll6mjr9O7Y7GWnKXPw1oFFYlP4ryR5iNpEKkpKvE9FSbKamtPSnZEc6Mk88UujjaANsCSE01bKomAdQ7oSvM1RdqmDXD7MW8dSB%2FRQaTNdgK2Rw0QY5mofJV17O6J1wvBwWWSL5LOZZS2ovxbNAEVYOlMq%2BaLEnhHzK6ToRAAE9QGvATVoT1lZJh%2BdgBr61spT5lH6F9sd6ntgMlTieijMWSQh0sUEnXj7k8Rxwk8eZxH84wcrXxwfYeRmFdtjo0AlSR%2BQqtWoohDrnCsRdN0HsX%2BS1EnEw6UppSfIUyh60Ht7d3dQwTB%2FiXd5aIUfB5gha8HORPliJ2eoOcz5vj1XQwdTkINQEaqFOcYtr0SHM0ISk8apXk0lvh%2F7%2BpItZ5FlLeo6yrUKKHlsJOjr62k6jjV%2F5cD%2F8Y5t5WnuCrW4AJxfcZNOjGMPP5rdEGOqUBvEKVN3sthRFdC5mOjgqsAPUULR5AWYkF3mamv8somLtQ%2BkfEwxxaq2v6ShrbckxFifqfOi2e4WTBIAq17PSVXej37ETydV566nGlF%2BrFTPUS98bL90S98Wq0EumnRO6LRjHYDryDdrAl2Z69IhC7FlFmfU7WHOv4SVeLILoKFuLjaOppTJlPUp0Tr09YJ%2BRKSIeFcGVJ0Wrw0S%2BXcHbbgX4LGvXS&X-Amz-Signature=00af028a37571d4f28250b7a5e6fd02bc706a571be778a3ebe52bbbe507de9c5&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)


- 📄 [[OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점/OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점|OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점]]
