#include "ais_ml.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <stdexcept>
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
                  const std::string &threshold_path)
{
    try {
        // 모델 로드
        m_session = new Ort::Session(m_env,
            model_path.c_str(), m_session_options);

        if (!LoadScaler(scaler_path))    return false;
        if (!LoadThreshold(threshold_path)) return false;

        m_loaded = true;
        return true;
    } catch (const std::exception &e) {
        return false;
    }
}

bool AIS_ML::LoadScaler(const std::string &path)
{
    try {
        std::ifstream f(path);
        if (!f.is_open()) return false;

        json j;
        f >> j;

        auto &min_arr = j["min"];
        auto &max_arr = j["max"];

        for (int i = 0; i < ML_FEATURE_COUNT; i++) {
            m_scaler.min_[i] = min_arr[i].get<float>();
            m_scaler.max_[i] = max_arr[i].get<float>();
        }
        return true;
    } catch (...) {
        return false;
    }
}

bool AIS_ML::LoadThreshold(const std::string &path)
{
    try {
        std::ifstream f(path);
        if (!f.is_open()) return false;
        f >> m_threshold;
        return true;
    } catch (...) {
        return false;
    }
}

void AIS_ML::PushFeature(int mmsi, float sog, float cog,
                         float dt, float dist_km)
{
    std::array<float, ML_FEATURE_COUNT> feat = {sog, cog, dt, dist_km};
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

    // 정규화 후 입력 데이터 생성 [1, SEQ_LEN, FEATURE_COUNT]
    std::vector<float> input_data;
    input_data.reserve(ML_SEQ_LEN * ML_FEATURE_COUNT);
    for (auto &feat : seq) {
        for (int i = 0; i < ML_FEATURE_COUNT; i++) {
            input_data.push_back(m_scaler.scale(i, feat[i]));
        }
    }

    // 입력 텐서 생성
    std::vector<int64_t> input_shape = {1, ML_SEQ_LEN, ML_FEATURE_COUNT};
    Ort::MemoryInfo mem_info = Ort::MemoryInfo::CreateCpu(
        OrtArenaAllocator, OrtMemTypeDefault);

    Ort::Value input_tensor = Ort::Value::CreateTensor<float>(
        mem_info,
        input_data.data(), input_data.size(),
        input_shape.data(), input_shape.size()
    );

    // 추론
    const char *input_names[]  = {"input"};
    const char *output_names[] = {"output"};

    auto output_tensors = m_session->Run(
        Ort::RunOptions{nullptr},
        input_names,  &input_tensor, 1,
        output_names, 1
    );

    // 재구성 오차 (MSE)
    float *output_data = output_tensors[0].GetTensorMutableData<float>();
    float mse = 0.0f;
    int n = ML_SEQ_LEN * ML_FEATURE_COUNT;
    for (int i = 0; i < n; i++) {
        float diff = output_data[i] - input_data[i];
        mse += diff * diff;
    }
    mse /= n;
    out_error = mse;

    return out_error > m_threshold;
}