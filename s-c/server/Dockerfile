FROM python:3.11-slim

LABEL maintainer="JB-Pirate-King AIS IDS"
LABEL description="AIS IDS Server — ML-based anomaly detection for ship AIS signals"

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐싱 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY ais_parser.py feature.py ml_detector.py alert_logger.py main.py ./

# 볼륨 마운트 포인트 생성
RUN mkdir -p /models /logs

# NMEA TCP 포트, REST API 포트
EXPOSE 10110
EXPOSE 5000

# 헬스체크: REST API /status 엔드포인트
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; r=urllib.request.urlopen('http://localhost:5000/status',timeout=5); exit(0 if r.status==200 else 1)"

CMD ["python", "-u", "main.py"]
