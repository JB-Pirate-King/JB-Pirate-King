---
notion_url: https://www.notion.so/378be0809830802a80abd4e8f471c0d0
last_synced: 2026-06-07 23:25
tags: [notion-sync]
---

# openCPN 개발자 메뉴얼 (1)

[https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/intro-AboutThisManual.html](https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/intro-AboutThisManual.html)

리눅스 소스코드 빌드
[https://opencpn-manuals.github.io/main/opencpn-dev/linux.html](https://opencpn-manuals.github.io/main/opencpn-dev/linux.html)

플러그인 문서 작성 방식
[https://opencpn-manuals.github.io/main/opencpn-plugins/authoring/pm-plugin-documentation.html](https://opencpn-manuals.github.io/main/opencpn-plugins/authoring/pm-plugin-documentation.html)

플러그인 개발
[https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/plugin-devel-overview.html](https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/plugin-devel-overview.html)

국내 openCPN 다룬 유일 블로그
[https://94epicenter.tistory.com/m/category/OpenCPN](https://94epicenter.tistory.com/m/category/OpenCPN)

리눅스 플러그인 구조
[https://github.com/leamas/OpenCPN/wiki/Installation-paths#linux](https://github.com/leamas/OpenCPN/wiki/Installation-paths#linux)
~/.local/
├── lib
│    └── opencpn
│          └── LIBRARIES
├── share
│    ├── opencpn
│    │     └── plugins
│    │            └── <plugin>
│    │                    └── DATA FILES
│    └── locale
|          └── *  (en_US, sv_SE, etc.)
|              └── LC_MESSAGES
|                   └── opencpn-<plugin>.mo
└── bin
└── BINARY HELPERS

플러그인 관리자 개발 절차
[https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/pi_installer_dev_procedure.html](https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/pi_installer_dev_procedure.html)
[https://github.com/OpenCPN/plugins/blob/master/TESTING.md](https://github.com/OpenCPN/plugins/blob/master/TESTING.md)
- 📄 [[플러그인/플러그인|플러그인]]
카탈로그는 다운로드 가능한 모든 플러그인을 설명하는 XML 파일

리눅스에서 빌드
[https://opencpn-manuals.github.io/main/opencpn-dev/linux.html](https://opencpn-manuals.github.io/main/opencpn-dev/linux.html)

깃 풀리퀘 하는 방법
[https://wayhome25.github.io/git/2017/07/08/git-first-pull-request-story/](https://wayhome25.github.io/git/2017/07/08/git-first-pull-request-story/)

깃 포크뜬 원래 저장소 내용 풀 받기
git remote add upstream git remote add upstream [https://github.com/JB-Pirate-King/ais_ids_pi/blob/main/README.md](https://github.com/JB-Pirate-King/ais_ids_pi/blob/main/README.md) 
git pull upstream main  
git push origin main

openCPN 플러그인들  깃허브 주소
[https://github.com/search?utf8=✓&q=opencpn&type=repositories](https://github.com/search?utf8=%E2%9C%93&q=opencpn&type=repositories)

openCPN lib 서브모듈
[https://github.com/OpenCPN/opencpn-libs/tree/main](https://github.com/OpenCPN/opencpn-libs/tree/main)

git submodule update --init --recursive

![image](_assets/image.png)
