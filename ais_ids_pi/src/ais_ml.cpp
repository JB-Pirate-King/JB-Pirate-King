#include "ais_ml.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <stdexcept>
#include <cmath>
#include <numeric>

using json = nlohmann::json;

AIS_ML::AIS_ML()
    : m_env(ORT_LOGGING_LEVEL_WARNING, "ais_ml")
    , m_session(nullptr)
    , m_threshold(0.0f)
    , m_loaded(false)
{
    m_session_options.SetIntraOpNumThreads(1);
}

AIS_ML::~AIS_ML()
{
    delete m_session;
}

bool AIS_ML::Load(const std::string &model_path,
                  const std::string &scaler_path,
                  const std::string &threshold_path,
                  std::string &error_msg)
{
    try {
        m_session = new Ort::Session(m_env, model_path.c_str(), m_session_options);

        if (!LoadScaler(scaler_path, error_msg))       return false;
        if (!LoadThreshold(threshold_path, error_msg)) return false;

        m_loaded  = true;
        error_msg = "ML load success | model: " + model_path;
        return true;
    } catch (const std::exception &e) {
        error_msg = "ML exception: " + std::string(e.what()) + " | path: " + model_path;
        return false;
    }
}

bool AIS_ML::LoadScaler(const std::string &path, std::string &error_msg)
{
    try {
        std::ifstream f(path);
        if (!f.is_open()) {
            error_msg = "scaler file not found: " + path;
            return false;
        }
        json j;
        f >> j;
        for (int i = 0; i < ML_FEATURE_COUNT; i++) {
            m_scaler.min_[i] = j["min"][i].get<float>();
            m_scaler.max_[i] = j["max"][i].get<float>();
        }
        return true;
    } catch (const std::exception &e) {
        error_msg = "scaler parse error: " + std::string(e.what());
        return false;
    }
}

bool AIS_ML::LoadThreshold(const std::string &path, std::string &error_msg)
{
    try {
        std::ifstream f(path);
        if (!f.is_open()) {
            error_msg = "threshold file not found: " + path;
            return false;
        }
        f >> m_threshold;
        return true;
    } catch (const std::exception &e) {
        error_msg = "threshold parse error: " + std::string(e.what());
        return false;
    }
}

void AIS_ML::PushFeature(int mmsi,
                          float sog, float cog, float heading,
                          float status, float dt, float dist_km,
                          float expected_dist_km, float bearing_cog_diff,
                          float cog_hdg_diff, float sog_change, float cog_change,
                          float sog_status_ratio, float dist_expected_ratio,
                          float cog_hdg_change, float cog_hdg_std)
{
    std::array<float, ML_FEATURE_COUNT> feat = {
        sog, cog, heading, status,
        dt, dist_km,
        expected_dist_km, bearing_cog_diff,
        cog_hdg_diff, sog_change, cog_change,
        sog_status_ratio, dist_expected_ratio,
        cog_hdg_change, cog_hdg_std
    };
    auto &seq = m_sequences[mmsi];
    seq.push_back(feat);
    if ((int)seq.size() > ML_SEQ_LEN)
        seq.pop_front();
}

bool AIS_ML::DetectAnomaly(int mmsi, float &out_error)
{
    if (!m_loaded) return false;

    auto it = m_sequences.find(mmsi);
    if (it == m_sequences.end() || (int)it->second.size() < ML_SEQ_LEN)
        return false;

    auto &seq = it->second;

    std::vector<float> input_data;
    input_data.reserve(ML_SEQ_LEN * ML_FEATURE_COUNT);
    for (auto &feat : seq) {
        for (int i = 0; i < ML_FEATURE_COUNT; i++)
            input_data.push_back(m_scaler.scale(i, feat[i]));
    }

    std::vector<int64_t> input_shape = {1, ML_SEQ_LEN, ML_FEATURE_COUNT};
    Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
        OrtArenaAllocator, OrtMemTypeDefault);

    Ort::Value input_tensor = Ort::Value::CreateTensor<float>(
        mem_info,
        input_data.data(), input_data.size(),
        input_shape.data(), input_shape.size()
    );

    const char *input_names[]  = {"x"};
    const char *output_names[] = {"output"};

    auto output_tensors = m_session->Run(
        Ort::RunOptions{nullptr},
        input_names,  &input_tensor, 1,
        output_names, 1
    );

    float *output_data = output_tensors[0].GetTensorMutableData<float>();
    float mse = 0.0f;
    int n = ML_SEQ_LEN * ML_FEATURE_COUNT;
    for (int i = 0; i < n; i++) {
        float diff = output_data[i] - input_data[i];
        mse += diff * diff;
    }
    mse /= static_cast<float>(n);
    out_error = mse;

    return out_error > m_threshold;
}