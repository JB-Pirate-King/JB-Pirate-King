from pyais import decode

# -----------------------------
# TXT 파일에서 AIS 메시지 읽기
# -----------------------------
def load_txt(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 공백 제거 + AIVDM만 필터
    return [line.strip() for line in lines if line.strip().startswith("!AIVDM")]


# -----------------------------
# AIS 디코딩
# -----------------------------
def decode_ais_messages(lines):
    decoded_list = []

    for line in lines:
        try:
            parts = line.split(",")
            payload = parts[5] if len(parts) > 5 else ""
            if not payload:
                print(f"[SKIP] 빈 payload: {line}")
                continue

            decoded = decode(line)
            decoded_list.append(decoded.asdict())

        except Exception as e:
            print(f"[ERROR] 디코딩 실패: {line}")
            print(f"        이유: {e}")

    return decoded_list


# -----------------------------
# 출력
# -----------------------------
def print_decoded(decoded_list):
    for i, msg in enumerate(decoded_list, 1):
        print(f"\n[메시지 {i}]")
        for k, v in msg.items():
            print(f"{k}: {v}")


# -----------------------------
# 실행
# -----------------------------
if __name__ == "__main__":
    file_path = "nmea_data_sample.txt"

    lines = load_txt(file_path)
    decoded_list = decode_ais_messages(lines)

    print_decoded(decoded_list)