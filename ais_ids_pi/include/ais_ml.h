#pragma once

#include <onnxruntime_cxx_api.h>
#include <vector>
#include <string>
#include <deque>
#include <array>
#include <unordered_map>
#include <fstream>

// 피처 순서: sog, cog, heading, status, dt, dist_km
#define ML_FEATURE_COUNT 6
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

    void PushFeature(int mmsi, float sog, float cog, float heading,
                     float status, float dt, float dist_km);

    bool DetectAnomaly(int mmsi, float &out_error);

    bool IsLoaded() const { return m_loaded; }

private:
    Ort::Env                 m_env;
    Ort::Session            *m_session;
    Ort::SessionOptions      m_session_options;
    MLScaler                 m_scaler;
    float                    m_threshold;
    bool                     m_loaded;

    std::unordered_map<int, std::deque<std::array<float, ML_FEATURE_COUNT>>> m_sequences;

    bool LoadScaler(const std::string &path, std::string &error_msg);
    bool LoadThreshold(const std::string &path, std::string &error_msg);
};