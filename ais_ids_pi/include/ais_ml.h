#pragma once

#include <onnxruntime_cxx_api.h>
#include <vector>
#include <string>
#include <deque>
#include <array>
#include <unordered_map>
#include <fstream>

// 피처 순서 (15개 — 현재 model.onnx 기준):
//   0  sog                속력 (knots)
//   1  cog                진행 방향 (도)
//   2  heading            선수 방향 (도)
//   3  status             항행 상태
//   4  dt                 이전 신호와 시간 차이 (초)
//   5  dist_km            실제 이동 거리 (km)
//   6  expected_dist_km   예상 이동 거리 (km)
//   7  bearing_cog_diff   GPS 이동방향 vs COG 차이 (도)
//   8  cog_hdg_diff       COG vs HDG 차이 (도, -1=미정의)
//   9  sog_change         이전 대비 SOG 변화량 (knots)
//  10  cog_change         이전 대비 COG 변화량 (도)
//  11  sog_status_ratio   sog / status별 정상최대속도
//  12  dist_expected_ratio 실제/예상 거리 비율
//  13  cog_hdg_change     이전 대비 cog_hdg_diff 변화량
//  14  cog_hdg_std        시퀀스 내 cog_hdg_diff 표준편차
#define ML_FEATURE_COUNT 15
#define ML_SEQ_LEN       10

struct MLScaler {
    float min_[ML_FEATURE_COUNT];
    float max_[ML_FEATURE_COUNT];

    float scale(int idx, float val) const {
        float denom = max_[idx] - min_[idx];
        if (denom == 0.0f) return 0.0f;
        return (val - min_[idx]) / denom;
    }
};

class AIS_ML {
public:
    AIS_ML();
    ~AIS_ML();

    bool Load(const std::string &model_path,
              const std::string &scaler_path,
              const std::string &threshold_path,
              std::string &error_msg);

    void PushFeature(int mmsi,
                     float sog, float cog, float heading,
                     float status, float dt, float dist_km,
                     float expected_dist_km, float bearing_cog_diff,
                     float cog_hdg_diff, float sog_change, float cog_change,
                     float sog_status_ratio, float dist_expected_ratio,
                     float cog_hdg_change, float cog_hdg_std);

    bool DetectAnomaly(int mmsi, float &out_error);

    bool   IsLoaded()        const { return m_loaded; }
    float  GetThreshold()    const { return m_threshold; }
    size_t GetSequenceSize(int mmsi) const {
        auto it = m_sequences.find(mmsi);
        return (it != m_sequences.end()) ? it->second.size() : 0;
    }

private:
    Ort::Env            m_env;
    Ort::Session       *m_session;
    Ort::SessionOptions m_session_options;
    MLScaler            m_scaler;
    float               m_threshold;
    bool                m_loaded;

    // MMSI → 슬라이딩 윈도우 시퀀스
    std::unordered_map<int, std::deque<std::array<float, ML_FEATURE_COUNT>>> m_sequences;

    bool LoadScaler   (const std::string &path, std::string &error_msg);
    bool LoadThreshold(const std::string &path, std::string &error_msg);
};