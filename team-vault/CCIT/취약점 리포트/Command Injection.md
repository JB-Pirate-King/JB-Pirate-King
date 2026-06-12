---
notion_url: https://www.notion.so/fdbbe080983082b48fa201e011068681
last_synced: 2026-06-12 18:07
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4663MGLT6WA%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090739Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJIMEYCIQCtlILtnhICA3VXSD5b4zPu8gO8KDSkO%2By7SXxAa8YYMwIhAKYUVq8FJmxUPCC176dy38yNabq67MUETnkSvIPTB84ZKv8DCBEQABoMNjM3NDIzMTgzODA1IgwyWYL8xlQht5YV%2Bd8q3ANhUbyBrcOXI7QHwMvfkR%2B8CtyaNFis8%2F6OQVH%2FbPFqYsOv8GZweMPglS9OUy2Wuh2Fvx6ZpnWdvZrefdlGoKrM4VXnRBggD3BsptTw%2FqijOEyRy3FV0xXWWk65cA2V7EOesmgdOdx7BCROtNwNXQ71RZJXUn11jsZNcZ6xoMHkpVvIrIoKQFyqkdaVtSecS7rmweB4brNFp0lFxHKDhlswyIQ1Cx0vYOhpFkPOoeZ%2BJ0TeDOrQF6kDY5KP1vpkA8x%2BaYRAlISrtLnRuTYxQ3o7zk3INDpYgQiA8gmG2fMdezXOwDqGjs5QP5lbbctW6x4ZI8SoVPZza%2FJ5toIO5xQg9c5hyLYsOJ6OTW%2FVImie%2FH%2FGH6Ir0PYWGo0y0MxlqAbfqr46kNkMdqmMoNA4gBPy%2FBQCSCpSrd0vxlcBLQE%2B2NJ4FHwSg01%2BLBK1SFbzHmDwozPuoiSAA%2Bh07odVJ4oihBYuBkGmS5Y1hLvi7zg42vhBL0RnNxLhOcBlvIDDaS%2FcXZWSAFAgc0ft71rKgG5rERxvvwbQq1QZRec%2BuNDMFSqyE2sqZ8rLY4sPA9ISvB%2FNMWXtMVwnsMg86bVX4uEPxqBZwA0bvK7szUX2DQrKR909F3%2F1tgDf3P%2FTHDDW8q7RBjqkAQctaP3dpjLCC4NtcDJ4Tvgslntlsx2vxYF94mf3e3F3yt62qiOIa2k%2FLCJUkuqGLCZToEVm%2BQONxH7%2FhUFnFAcp8RxsFxHKm10Ve0AFzlj5oHuAHVhWXjXesBt4%2FI2m3%2F1fyHrD4PCKKnt9aJDGWM%2FqDeeo8S%2FVgYZcXwdUKgdC4G8%2Ff%2FjLo35%2FawjL8R0WnrZJi9CTbrqq5gGRpvONSv0p%2BY8M&X-Amz-Signature=ba236751f8ef9a52c37bf5a9ad7778db4009e926daf545f440cb4b3e8d66ff89&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)


- 📄 [[OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점/OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점|OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점]]
