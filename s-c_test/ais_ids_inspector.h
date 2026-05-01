//===================================================================
// ais_ids_inspector.h
// Snort 3 Inspector plugin – AIS/NMEA 이상 행동 탐지
//===================================================================
#pragma once

#include <memory>
#include <string>
#include <unordered_map>
#include <deque>
#include <ctime>

// Snort 3 headers
#include "framework/inspector.h"
#include "framework/module.h"
#include "detection/detection_engine.h"
#include "protocols/packet.h"

// 기존 ais_ids 코드 재사용 (wx 의존성 제거 버전)
#include "ais_ids_core.h"   // ais_ids.h에서 wxString → std::string으로 변환한 헤더

namespace snort {

// ──────────────────────────────────────────────
//  Alert SID
// ──────────────────────────────────────────────
static constexpr uint32_t GID_AIS_IDS         = 9001;
static constexpr uint32_t SID_ML_ANOMALY      = 1;
static constexpr uint32_t SID_POSITION_JUMP   = 2;
static constexpr uint32_t SID_SPEED_VIOLATION = 3;
static constexpr uint32_t SID_STATUS_SPEED    = 4;
static constexpr uint32_t SID_COG_HDG         = 5;
static constexpr uint32_t SID_SPEED_CHANGE    = 6;
static constexpr uint32_t SID_SIGNAL_LOSS     = 7;
static constexpr uint32_t SID_INVALID_MMSI    = 8;

// ──────────────────────────────────────────────
//  Inspector 설정 파라미터 (snort.lua에서 설정)
// ──────────────────────────────────────────────
struct AisIdsConfig {
    std::string model_dir;          // ONNX 모델 디렉토리
    uint16_t    ais_port    = 10110; // 감시 UDP 포트
    bool        rule_checks = true;  // 규칙 기반 탐지 활성화
    bool        ml_checks   = true;  // ML 탐지 활성화
};

// ──────────────────────────────────────────────
//  Snort Module (설정 파싱)
// ──────────────────────────────────────────────
class AisIdsModule : public snort::Module {
public:
    AisIdsModule();

    bool set(const char* fqn, snort::Value& val, snort::SnortConfig* sc) override;
    bool begin(const char* fqn, int idx, snort::SnortConfig* sc) override;

    const snort::Parameter* get_parameters() const override;
    const AisIdsConfig& get_config() const { return m_config; }

private:
    AisIdsConfig m_config;
};

// ──────────────────────────────────────────────
//  Inspector
// ──────────────────────────────────────────────
class AisIdsInspector : public snort::Inspector {
public:
    explicit AisIdsInspector(const AisIdsConfig& cfg);
    ~AisIdsInspector() override;

    // Snort Inspector 인터페이스
    bool configure(snort::SnortConfig* sc) override;
    void show(const snort::SnortConfig* sc) const override;
    void eval(snort::Packet* p) override;

private:
    // NMEA 문장 추출
    bool find_aivdm(const uint8_t* data, size_t len, std::string& sentence);

    // 이상 탐지 처리
    void process_sentence(const std::string& sentence,
                          const snort::Packet* p);

    // 경보 발생
    void raise_alert(snort::Packet* p, uint32_t sid,
                     int mmsi, const std::string& detail);

    AisIdsConfig             m_config;
    std::unique_ptr<ais_ids> m_ids;    // ais_ids.cpp 재사용 (wx 제거 버전)

    // 멀티파트 AIVDM 재조합 버퍼
    // key: sequential_msg_id, value: partial sentences
    std::unordered_map<int, std::vector<std::string>> m_fragment_buf;
};

// ──────────────────────────────────────────────
//  플러그인 등록 매크로
// ──────────────────────────────────────────────
extern const snort::InspectApi ais_ids_api;

} // namespace snort
