
# JB-Pirate-King
An AIS Intrusion Detection System (IDS) plugin for OpenCPN project.


## ais_ids_pi Plugin Installation Guide 

This guide explains how to install the **ais_ids_pi plugin** in an environment with **Ubuntu 24.04 LTS** and **OpenCPN 5.13.2**, including all required dependencies and build steps.  
Tested in a VirtualBox Ubuntu 24.04 LTS environment.


---

### 📦 Installation 

Run the following commands in sequence:


## Update system
```bash
sudo apt update
sudo apt upgrade
```

## Install OpenCPN
```bash
sudo apt install software-properties-common -y
sudo add-apt-repository ppa:opencpn/opencpn
sudo apt-get update
sudo apt-get install opencpn
```

## Install plugin dependencies
```bash
sudo apt install cmake libwxgtk3.2-dev gettext libbz2-dev libzip-dev devscripts equivs
sudo mk-build-deps -i -r ci/control
sudo apt-get --allow-unauthenticated install -f
```

## Install generator dependencies
```bash
sudo apt install python3 python3-tk
```

## Plugin Build
```bash
git clone https://github.com/JB-Pirate-King/JB-Pirate-King --recursive
cd JB-Pirate-King/ais_ids_pi/
bash local-build-package.sh
```
