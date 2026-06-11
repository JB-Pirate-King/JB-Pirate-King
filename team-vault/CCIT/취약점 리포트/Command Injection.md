---
notion_url: https://www.notion.so/fdbbe080983082b48fa201e011068681
last_synced: 2026-06-12 00:59
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466QDHN22GD%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T155936Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDUaCXVzLXdlc3QtMiJIMEYCIQCGyBYYBFalXTHqMYPgGjiJoNShd4lDbDYbFNIuQGWWQAIhAOHZqbm3GZQIoJAzYGWgIbUn4OB1Mxmb86e6QVxtzW26KogECP7%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEQABoMNjM3NDIzMTgzODA1IgxeiK%2BsIDD5OqPFFJ4q3AOx7PrhVbCmt3cXs5n6Igcocf%2FamcUFM%2BvUjRSnLxZo%2B9ZXA77J%2BQSKjiC7mj3e%2BYDJuSFY5DRdpUYCyizt2aRDg5qW7GvLrqRJsgkKvs6eLQTkK%2FXSA03YoBGqlBuxB1fPjTd5wRvpk3Uh9mv1NU3ZAhzeW4pW%2Bj30C3ZT7ZoAQRNkw60pcT0AAPJDU5JtLk8rzglPPaXsF68VZHXui6wQGVWSFDZgcf%2B31wlMmgNfUldv8kV5AbmECdcvRDS7zISB6ccAZaXYdvEN2vmL4WwqZ71zcJBcFBvalZhja%2Ff5tHHEG0L0jXsaL%2FbIubUfT86X%2FFPAtBUphoQLbO%2BAFCrJ0v7JHjNcjOWQ0yesuRB9fsCSzWKhiewAmh2vh3RUxIA16vwrPYx1BqHtGHKoCdUeo%2Fld1um%2F8kAkP61KA4K8dZ%2Bb%2BSOpumFmrFymjhmWwwi%2B%2FZGqlx8l5NnWrI3r1d8%2BzH7MS9SV5FvGxxvrvHiX6bwtYaSsMLGReTcFddMY2W1%2B%2F5vOxtiNyBK1g0d2%2BEtbKrQStrWSdJ8%2B44JrYWO%2BOTpPpITKTHIZ5cKfmdmZoL5zmhN8I7Lz7q7GJkusKvymG%2FJVQpUkTU0Q5LjvHCpS43dCaZQ7GNFkFqeOyzCQ26rRBjqkASVCoEC%2FY2%2Bz%2Bjd9jIIMZMPw6WywzIUlnynOuPwfFICgr%2FHyhSZcptB3OE9Oto9t6bnfgGRHdD8SlG2szftR5XjPOp172ymuDaiak5eySlK5iJPy%2BbePNA8OnIey2%2BIXiji7T3aUR7b%2FQGWmeRdzOBZzEUgJcoWjhoHOlo0rjzYU%2B8OuO8wCUvzinWW2oGUDTk63q4x46MB1nf8Oyf9mi58fzuEK&X-Amz-Signature=0ac0b08612ed9d11c7a86510524b3cf18c407e8217ca995d981872d06ab0bb00&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)


- 📄 [[OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점/OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점|OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점]]
