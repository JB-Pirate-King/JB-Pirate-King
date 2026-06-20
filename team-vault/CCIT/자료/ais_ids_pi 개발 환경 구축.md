---
notion_url: https://www.notion.so/333be080983080abad63fddc4571359c
last_synced: 2026-06-20 10:04
tags: [notion-sync]
---

# ais_ids_pi 개발 환경 구축

버츄얼 박스 우분투 24.04 LTS 환경 

```c
sudo apt update
sudo apt upgrade
```

opencpn_5.13.2 설치 (Ubuntu-24.04)

```c
sudo apt install software-properties-common -y
sudo add-apt-repository ppa:opencpn/opencpn
sudo apt-get update
sudo apt-get install opencpn
```


플러그인 종속성

```plain text
sudo apt install cmake libwxgtk3.2-dev gettext libbz2-dev libzip-dev devscripts equivs && sudo mk-build-deps -i -r ci/control && sudo apt-get --allow-unauthenticated install -f
```

제너레이터 종속성

```c
sudo apt install python3 python3-tk
```
