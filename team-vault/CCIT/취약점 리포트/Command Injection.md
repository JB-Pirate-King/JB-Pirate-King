---
notion_url: https://www.notion.so/fdbbe080983082b48fa201e011068681
last_synced: 2026-06-12 05:51
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

> 📎 첨부(미변환): [%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/86b64d73-e6e3-4711-a7aa-ec73b8c65e5a/%EB%85%B9%ED%99%94_2025_07_25_16_41_30_902.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466V6VJIEMX%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205120Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJHMEUCIAj5Tqm6btHdng0kwdTIvIh8iMRS1TVemz7zGEZ1O49vAiEAmn8AuojH3HeHM3G%2Ffx1rWvfHQ6onAKTSOe687956RXkq%2FwMIBRAAGgw2Mzc0MjMxODM4MDUiDHcj60afb8VRRtUmByrcA8ABfY%2FHtACOy9sChG9lKglP20CKJVPmCcfmKc2mkwYlEP17qsHhzy4QuVPsObCX%2FJPWOqjQQXP09TFf8VMg1XaFsntGnYkO4KZEus5M4XyX8nNzKvhww%2FRXrbpI5TYuc%2FtikYFsHPPQi6pikSDBe%2F%2F6gYjN7NJV9JBGXf9%2Fz31ZAbAer9CAUUqZycrv2h%2FN7gnaW9XbQAyNND27sC5vRS8zxafcEQs6sacpMD3dXY0ooraLV4lHP4w%2F2W6CH6ZRzaCzzGx%2FWojmWAYzZ8NnDkDkGeYt0yBmmBVZ3GCUAZ1dsV1CsG4wHfy%2BLP9ULZrtD8xHGE%2BWTKhAIWfL9DQ9HX4q6dIegCRR5QCspBnsA5eNHKWi4uV%2B22BumRR6KnXIK30pkuNTMy8tfE%2F2qfK2VKjdipryzvZYA7dSBAMWuytZrqOefSMnZUq%2FlDcXbzYD7VIQflDmigZ2bZCHGwyajcyQNKRemSl7gIdFqNi3nQjHkuRyut3XMOwh28nrhOclluX3oIsoixI%2Fon9j%2BENIXtJKBYxCCItdp3aG1pFoGfI%2F%2BRUv3z48mzg%2B1vCRQCI8PPJKgRpFghVRVGDs98iVO5oV0zmq6V3Y5P%2F9lRFkHXLZlJTuiq3za%2FURwxDiMMqqrNEGOqUB3CZRZnCOB6OxaJ7zMViZVIYvyWKYTR21EgOuX9b8uRdK7mpGZadt2aao8hGCAj%2FjAd48oDR2DO05z1l7MYK5w4fqiY8pI9fWi4KnpbmrFXpU1s9s3438l1ghayXB%2FFPEyZqxDlB%2Fk7OmiL9R%2BoRw5OXDzIJeorCZI4FWh1%2FYUCKOx%2BzyMDXSO9wLxZLUcQ0ouRJCS%2BmO6xOHOnrcZKsQ3sZo73g8&X-Amz-Signature=914bf5179f1b5b067b11b5859cdf010c6e3607e1520a3fc820eeb48bfbcc7268&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)


- 📄 [[OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점/OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점|OpenCPN Launcher Plugin- 사용자 정의 명령 실행을 통한 명령어 주입 취약점]]
