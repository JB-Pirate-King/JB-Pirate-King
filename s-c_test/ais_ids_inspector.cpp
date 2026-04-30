//===================================================================
// ais_ids_inspector.cpp
// Snort 3 Inspector plugin – AIS/NMEA 이상 행동 탐지
//
// 빌드:
//   cmake -DSNORT3_DIR=/usr/local/snort3 ..
//   make
//
// 설치:
//   sudo cp libais_ids.so /usr/local/lib/snort/
//
// snort.lua 설정 예시:
//   ais_ids = {
//     model_dir = "/opt/ais_ids/models/",
//     ais_port  = 10110,
//     ml_checks = true
//   }
//===================================================================

#include "ais_ids_inspector.h"

#include <cstring>
#include <sstream>
#include <algorithm>
#include <cmath>

// Snort 3 headers
#include "framework/inspector.h"
#include "framework/module.h"
#include "detection/detection_engine.h"
#include "events/event_queue.h"
#include "log/messages.h"

namespace snort {

// ──────────────────────────────────────────────
//  Module 파라미터 정의
// ──────────────────────────────────────────────
static const Parameter s_params[] = {
    { "model_dir",   Parameter::PT_STRING, nullptr, "",
      "ONNX 모델 디렉토리 (model.onnx, scaler.json, threshold.txt)" },

    { "ais_port",    Parameter::PT_PORT,   nullptr, "10110",
      "AIS NMEA UDP 포트 번호" },

    { "rule_checks", Parameter::PT_BOOL,   nullptr, "true",
      "규칙 기반 탐지 (위치 점프, 속도 이상 등)" },

    { "ml_checks",   Parameter::PT_BOOL,   nullptr, "true",
      "ML 오토인코더 이상 탐지" },

    { nullptr, Parameter::PT_MAX, nullptr, nullptr, nullptr }
};

// ──────────────────────────────────────────────
//  AisIdsModule
// ──────────────────────────────────────────────
AisIdsModule::AisIdsModule()
    : snort::Module("ais_ids", "AIS 이상 행동 탐지 Inspector", s_params)
{}

const snort::Parameter* AisIdsModule::get_parameters() const {
    return s_params;
}

bool AisIdsModule::begin(const char*, int, snort::SnortConfig*) {
    m_config = AisIdsConfig{};
    return true;
}

bool AisIdsModule::set(const char* fqn, snort::Value& val,
                       snort::SnortConfig*)
{
    if      (!strcmp(fqn, "model_dir"))   m_config.model_dir   = val.get_string();
    else if (!strcmp(fqn, "ais_port"))    m_config.ais_port    = val.get_uint16();
    else if (!strcmp(fqn, "rule_checks")) m_config.rule_checks = val.get_bool();
    else if (!strcmp(fqn, "ml_checks"))   m_config.ml_checks   = val.get_bool();
    else return false;
    return true;
}

// ──────────────────────────────────────────────
//  AisIdsInspector 구현
// ──────────────────────────────────────────────
AisIdsInspector::AisIdsInspector(const AisIdsConfig& cfg)
    : m_config(cfg)
{
    // ais_ids 코어 초기화 (ais_ids.cpp의 wx 의존성 제거 버전)
    m_ids = std::make_unique<ais_ids>(cfg.model_dir);
    LogMessage("=( ais_ids_inspector )= loaded, port=%d ml=%s\n",
               cfg.ais_port, cfg.ml_checks ? "ON" : "OFF");
}

AisIdsInspector::~AisIdsInspector() = default;

bool AisIdsInspector::configure(snort::SnortConfig*) {
    return true;
}

void AisIdsInspector::show(const snort::SnortConfig*) const {
    ConfigLogger::log_value("model_dir",   m_config.model_dir.c_str());
    ConfigLogger::log_value("ais_port",    m_config.ais_port);
    ConfigLogger::log_flag ("rule_checks", m_config.rule_checks);
    ConfigLogger::log_flag ("ml_checks",   m_config.ml_checks);
}

// ──────────────────────────────────────────────
//  eval() – 패킷마다 호출
// ──────────────────────────────────────────────
void AisIdsInspector::eval(snort::Packet* p) {
    // UDP만 처리
    if (!p || !p->is_udp()) return;

    // 설정된 포트만 처리
    if (p->ptrs.dp != m_config.ais_port &&
        p->ptrs.sp != m_config.ais_port)
        return;

    const uint8_t* data = p->data;
    uint16_t       dlen = p->dsize;
    if (!data || dlen < 10) return;

    // 페이로드에서 AIVDM 문장 추출 (개행으로 분리된 여러 문장 처리)
    const char* ptr = reinterpret_cast<const char*>(data);
    const char* end = ptr + dlen;

    while (ptr < end) {
        // 줄 끝 탐색
        const char* nl = reinterpret_cast<const char*>(
            memchr(ptr, '\n', end - ptr));
        size_t line_len = nl ? (nl - ptr) : (end - ptr);
        std::string line(ptr, line_len);
        ptr = nl ? nl + 1 : end;

        // 공백 제거
        while (!line.empty() && (line.back() == '\r' || line.back() == ' '))
            line.pop_back();

        if (line.size() < 10) continue;

        // AIVDM / AIVDO 감지
        if (line.rfind("!AIVDM", 0) != 0 &&
            line.rfind("!AIVDO", 0) != 0)
            continue;

        process_sentence(line, p);
    }
}

// ──────────────────────────────────────────────
//  AIVDM 문장 처리
// ──────────────────────────────────────────────
void AisIdsInspector::process_sentence(const std::string& sentence,
                                       const snort::Packet* p)
{
    // ── 멀티파트 재조합 ────────────────────────────────────
    // !AIVDM,<total>,<frag_num>,<seq_id>,<ch>,<payload>,<fill>*cs
    auto parts = [&]() -> std::vector<std::string> {
        std::vector<std::string> v;
        std::stringstream ss(sentence);
        std::string tok;
        while (std::getline(ss, tok, ',')) v.push_back(tok);
        return v;
    }();

    if (parts.size() < 7) return;

    int total_frags = std::atoi(parts[1].c_str());
    int frag_num    = std::atoi(parts[2].c_str());
    int seq_id      = parts[3].empty() ? 0 : std::atoi(parts[3].c_str());

    std::string full_sentence = sentence;

    if (total_frags > 1) {
        // 멀티파트: 버퍼에 누적
        auto& buf = m_fragment_buf[seq_id];
        buf.push_back(sentence);

        if ((int)buf.size() < total_frags)
            return;  // 아직 안 모임

        // 조합 (페이로드 연결)
        // 단순화: 첫 번째 문장 헤더 + 페이로드 합산
        std::string combined_payload;
        for (auto& frag : buf) {
            auto fp = [&]() {
                std::vector<std::string> v;
                std::stringstream ss2(frag);
                std::string t2;
                while (std::getline(ss2, t2, ',')) v.push_back(t2);
                return v;
            }();
            if (fp.size() >= 6) combined_payload += fp[5];
        }
        // 재구성된 단일 문장으로 교체
        full_sentence = "!AIVDM,1,1,," + parts[4] + "," +
                        combined_payload + ",0*00";  // 체크섬은 무시
        buf.clear();
    }

    // ── AISTarget 파싱 ────────────────────────────────────
    AISTarget target;
    if (!m_ids->ais_parser->Parse(full_sentence, target))
        return;

    // ── 스냅샷 저장 + 피처 추출 ──────────────────────────
    m_ids->to_snapshot(target);

    // ── 이상 탐지 (수정된 detect_anomaly_ais는 std::string 반환) ──
    if (!m_config.rule_checks && !m_config.ml_checks) return;

    std::string result = m_ids->detect_anomaly_ais(target.mmsi);
    if (result.empty()) return;

    // SID 분류
    uint32_t sid = SID_ML_ANOMALY;
    if      (result.find("Position jump")      != std::string::npos) sid = SID_POSITION_JUMP;
    else if (result.find("Speed limit")        != std::string::npos) sid = SID_SPEED_VIOLATION;
    else if (result.find("navigation status")  != std::string::npos) sid = SID_STATUS_SPEED;
    else if (result.find("COG/HDG")            != std::string::npos) sid = SID_COG_HDG;
    else if (result.find("Sudden speed")       != std::string::npos) sid = SID_SPEED_CHANGE;
    else if (result.find("Signal loss")        != std::string::npos) sid = SID_SIGNAL_LOSS;
    else if (result.find("MMSI country")       != std::string::npos) sid = SID_INVALID_MMSI;

    // 규칙/ML 필터 적용
    if (sid == SID_ML_ANOMALY && !m_config.ml_checks)   return;
    if (sid != SID_ML_ANOMALY && !m_config.rule_checks) return;

    raise_alert(const_cast<snort::Packet*>(p), sid, target.mmsi, result);
}

// ──────────────────────────────────────────────
//  Snort 경보 발생
// ──────────────────────────────────────────────
void AisIdsInspector::raise_alert(snort::Packet* p, uint32_t sid,
                                   int mmsi, const std::string& detail)
{
    // Snort 이벤트 큐에 등록
    DetectionEngine::queue_event(GID_AIS_IDS, sid);

    // 추가 컨텍스트 로깅
    LogMessage("[ais_ids] ALERT sid=%u MMSI=%d | %s\n",
               sid, mmsi, detail.c_str());
}

// ──────────────────────────────────────────────
//  플러그인 팩토리 함수
// ──────────────────────────────────────────────
static snort::Module* mod_ctor() {
    return new AisIdsModule();
}
static void mod_dtor(snort::Module* m) {
    delete m;
}

static snort::Inspector* ins_ctor(snort::Module* m) {
    auto* mod = static_cast<AisIdsModule*>(m);
    return new AisIdsInspector(mod->get_config());
}
static void ins_dtor(snort::Inspector* p) {
    delete p;
}

// ──────────────────────────────────────────────
//  InspectApi – Snort 플러그인 등록
// ──────────────────────────────────────────────
static const snort::InspectApi ais_ids_api_impl = {
    {
        PT_INSPECTOR,
        sizeof(snort::InspectApi),
        INSAPI_VERSION,
        0,
        API_RESERVED,
        API_OPTIONS,
        "ais_ids",
        "AIS NMEA 이상 행동 탐지 (ML + 규칙 기반)",
        mod_ctor,
        mod_dtor
    },
    snort::IT_NETWORK,    // 네트워크 레이어 인스펙터
    PROTO_BIT__UDP,       // UDP 프로토콜
    nullptr,              // buffers
    nullptr,              // service
    nullptr,              // pinit
    nullptr,              // pterm
    nullptr,              // tinit
    nullptr,              // tterm
    ins_ctor,
    ins_dtor,
    nullptr,              // ssn
    nullptr               // reset
};

const snort::InspectApi ais_ids_api = ais_ids_api_impl;

// Snort가 로드할 SO_PUBLIC 심볼
SO_PUBLIC const snort::BaseApi* snort_plugins[] = {
    &ais_ids_api.base,
    nullptr
};

} // namespace snort
