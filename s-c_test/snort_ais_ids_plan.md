# AIS-IDS + Snort 3 통합 기획안
**AIS 이상 행동 탐지 ML 모델을 Snort 3 인스펙터로 이식하는 방안**

---

## 1. 배경 및 목적

현재 `ais_ids_pi` 플러그인은 OpenCPN 내부에서만 동작하며, AIVDM 문장을 OpenCPN이 파싱한 뒤 콜백(`SetAISSentence`)으로 전달받는 구조다. 이는 다음 한계를 갖는다.

- OpenCPN을 반드시 실행해야 탐지 가능
- 네트워크 상의 raw NMEA 트래픽 직접 모니터링 불가
- 함대/항만 단위 다중 센서 통합 어려움

**목적:** 기존 ML 탐지 로직(`ais_ml.cpp`)을 Snort 3 Inspector 플러그인으로 이식해, 네트워크에서 유통되는 NMEA 0183/AIVDM 패킷을 직접 분석·경보한다.

---

## 2. AIS 데이터 네트워크 유통 경로

```
AIS VHF 수신기
     │
     ├─ Serial/USB → 브릿지 소프트웨어 (kplex, gpsd, mxtd 등)
     │
     └─ UDP 브로드캐스트 (기본 포트: 10110 / 4001 / 2000)
              │
              ├─ OpenCPN (현재 탐지 경로)
              └─ [신규] Snort 3 Inspector ← 여기를 공략
```

NMEA 0183 AIS 문장은 일반적으로 UDP로 브로드캐스트되며 평문 텍스트다. Snort는 패킷 레벨에서 이를 캡처 가능.

---

## 3. 시스템 아키텍처

```
┌────────────────────────────────────────────────────────────┐
│                      Snort 3 Pipeline                      │
│                                                            │
│  [Network TAP/Mirror]                                      │
│       │                                                    │
│  [DAQ Layer] → PacketDecoder → StreamDecoder               │
│                                     │                      │
│                              [UDP/TCP Flow]                │
│                                     │                      │
│                    ┌────────────────▼──────────────────┐   │
│                    │  ais_ids_inspector  (신규 모듈)     │   │
│                    │                                   │   │
│                    │  ① NMEA 프레임 감지               │   │
│                    │     (!AIVDM / !AIVDO)             │   │
│                    │                                   │   │
│                    │  ② AIVDM 비트 디코더              │   │
│                    │     (Msg Type 1/2/3/18/21)        │   │
│                    │                                   │   │
│                    │  ③ AISTarget 구성                 │   │
│                    │     mmsi, sog, cog, hdg,          │   │
│                    │     lat, lon, navStatus            │   │
│                    │                                   │   │
│                    │  ④ Feature Extractor              │   │
│                    │     (15개 특징 - ais_ids.cpp 동일) │   │
│                    │                                   │   │
│                    │  ⑤ ONNX ML Autoencoder            │   │
│                    │     (ais_ml.cpp 재사용)            │   │
│                    │     MSE > threshold?              │   │
│                    │                                   │   │
│                    └────────────┬──────────────────────┘   │
│                                 │ 이상 탐지 시              │
│                    ┌────────────▼──────────────────────┐   │
│                    │  Snort Alert                       │   │
│                    │  GID: 9001                         │   │
│                    │  SID: 1 ~ 8 (탐지 유형별)          │   │
│                    └───────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

---

## 4. Snort 3 Inspector 설계

### 4.1 파일 구성

```
snort3-ais-ids/
├── CMakeLists.txt
├── ais_ids_inspector.h       # Inspector 클래스 선언
├── ais_ids_inspector.cpp     # Inspector 구현 (신규)
├── ais_nmea_parser.h/.cpp    # NMEA/AIVDM 파서 (신규)
├── ais_ids.h/.cpp            # 기존 코드 재사용 (wx 의존성 제거)
├── ais_ml.h/.cpp             # 기존 코드 재사용
└── models/
    ├── model.onnx
    ├── scaler.json
    └── threshold.txt
```

### 4.2 핵심 인터페이스

```cpp
// ais_ids_inspector.h
class AisIdsInspector : public snort::Inspector {
public:
    void eval(snort::Packet* p) override;   // 패킷 단위 처리
    void show(const snort::SnortConfig*) const override;

private:
    bool extract_aivdm(const uint8_t* data, size_t len,
                       std::string& sentence);
    std::unique_ptr<ais_ids> m_ids;
};
```

### 4.3 eval() 처리 흐름

```cpp
void AisIdsInspector::eval(snort::Packet* p) {
    if (!p->has_udp_data() && !p->has_tcp_data()) return;

    // 1. 페이로드에서 AIVDM 문장 추출
    std::string sentence;
    if (!extract_aivdm(p->data, p->dsize, sentence)) return;

    // 2. AISTarget 파싱 + 스냅샷
    AISTarget t;
    m_ids->ais_parser->Parse(sentence, t);
    m_ids->to_snapshot(t);

    // 3. 이상 탐지
    std::string result = m_ids->detect_anomaly_ais(t.mmsi);
    if (!result.empty()) {
        // 4. Snort 경보 발생
        DetectionEngine::queue_event(GID_AIS_IDS, SID_ML_ANOMALY);
    }
}
```

---

## 5. AIVDM 파서 설계

NMEA 0183 AIVDM 문장의 비트 디코딩이 핵심이다.

```
!AIVDM,1,1,,B,15M67N0000G?Uf6E`FepT@4n0<05,0*73
       ↑ ↑ ↑  ↑ ────────────────────────── ↑
       | | |  채널  6-bit 페이로드          fill bits
       | | 시퀀스ID
       | 현재조각번호
       전체조각수
```

**Message Type 1/2/3 (Class A Position Report) 필드:**

| 필드 | 비트 | 설명 |
|------|------|------|
| msg_type | 0-5 | 메시지 타입 (1~3) |
| mmsi | 8-37 | 30비트 |
| nav_status | 38-41 | 항법 상태 |
| sog | 50-59 | 속도 (x0.1 knots) |
| lon | 61-88 | 경도 (x1/10000 분) |
| lat | 89-115 | 위도 (x1/10000 분) |
| cog | 116-127 | 침로 (x0.1도) |
| hdg | 128-136 | 선수방위 (도) |

---

## 6. Alert SID 테이블

| SID | 탐지 유형 | 설명 |
|-----|-----------|------|
| 1 | ML_ANOMALY | ML 오토인코더 MSE 초과 |
| 2 | POSITION_JUMP | 단시간 내 위치 급변 (>5km/60s) |
| 3 | SPEED_VIOLATION | 선종별 최대 속도 초과 |
| 4 | STATUS_SPEED_MISMATCH | 정박/계류 중 SOG≥0.5 |
| 5 | COG_HDG_MISMATCH | COG/HDG 불일치 >100° |
| 6 | SUDDEN_SPEED_CHANGE | 60초 내 SOG 변화 >10kn |
| 7 | SIGNAL_LOSS | 신호 소실 후 재등장 >300s |
| 8 | INVALID_MMSI | MMSI 국가코드 비정상 |

---

## 7. Snort 룰 연동

```
# /etc/snort/rules/ais_ids.rules
alert udp any any -> any 10110 \
    (msg:"AIS ML Anomaly Detected"; \
     gid:9001; sid:1; rev:1; \
     classtype:policy-violation; \
     metadata:service ais;)

alert udp any any -> any 10110 \
    (msg:"AIS Position Jump Detected"; \
     gid:9001; sid:2; rev:1; \
     classtype:policy-violation;)
```

---

## 8. 구현 단계

| 단계 | 작업 | 기간 |
|------|------|------|
| 1 | ais_ids.cpp / ais_ml.cpp에서 wx 의존성 제거, std::string 전환 | 1주 |
| 2 | AIVDM 비트 디코더 구현 및 테스트 (PCAP 재생) | 1주 |
| 3 | Snort 3 Inspector 플러그인 CMake 빌드 환경 구성 | 1주 |
| 4 | eval() 구현, Snort 경보 연동 | 1주 |
| 5 | 다중 패킷 재조합 (멀티파트 AIVDM) 처리 | 3일 |
| 6 | 실 환경 PCAP으로 탐지율/오탐율 검증 | 1주 |

---

## 9. 데모 구성 (Python 프로토타입)

Snort 플러그인 개발 전, Python으로 전체 파이프라인을 검증한다.

```
demo_snort_ais_ids.py
├── UDP 소켓 리스너 (포트 10110)  또는  PCAP 재생
├── AIVDM 파서 (bit-level decoder)
├── Feature Extractor (ais_ids.cpp와 동일한 15개 특징)
├── AnomalyDetector (ONNX 또는 heuristic fallback)
└── Alert Emitter (Snort-style 출력 + 로그 파일)
```

---

## 10. 기대 효과

- **독립 운용**: OpenCPN 없이 네트워크 탭 하나로 AIS IDS 구동
- **확장성**: 다중 AIS 피드(UDP 멀티캐스트, TCP aggregator) 동시 처리
- **SIEM 연동**: Snort 경보 → syslog → Elasticsearch/Splunk 파이프라인
- **규정 준수**: SOLAS, IMO 사이버보안 지침(MSC-FAL.1/Circ.3) 대응
