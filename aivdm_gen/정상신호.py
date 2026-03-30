import socket
import time

HOST = "127.0.0.1"
PORT = 1111

def load_nmea(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() + "\r\n" for line in f if line.startswith("!AIVDM")]

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

for msg in load_nmea("nmea_data_sample.txt"):
    sock.sendto(msg.encode("ascii"), (HOST, PORT))
    print(msg.strip())
    time.sleep(0.1)