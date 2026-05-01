#include "ais_ml.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <stdexcept>
#include <cmath>
#include <numeric>

using json = nlohmann::json;

// ── 생성자 / 소멸자 ───────────────────────────────────────────────

AIS_ML::AIS_ML()
    : m_env(ORT_LOGGING_LEVEL_WARNING, "ais_ml")
    , m_session(nullptr)
    , m_threshold(0.0f)
    , m_loaded(false)
    , m_ensemble_mode(false)
{
    m_session_options.SetIntraOpNumThreads(1);
}

AIS_ML::~AIS_ML()
{
    delete m_session;
    for (auto &em : m_ensemble)
        delete em.session;
}

// ── 단일 모델 로드 ────────────────────────────────────────────────

bool AIS_ML::Load(const std::string &model_path,
                  const std::string &scaler_path,
                  const std::string &threshold_path,
                  std::string &error_msg)
{
    try {
        m_session = new Ort::Session(m_env, model_path.c_str(), m_session_options);

        if (!LoadScaler(scaler_path, error_msg))       return false;
        if (!LoadThreshold(threshold_path, error_msg)) return false;

        m_ensemble_mode = false;
        m_loaded        = true;
        error_msg = "ML load success | model: " + model_path;
        return true;
    } catch (const std::exception &e) {
        error_msg = "ML exception: " + std::string(e.what()) + " | path: " + model_path;
        return false;
    }
}

// ── 가중 앙상블 로드 ──────────────────────────────────────────────

bool AIS_ML::LoadWeightedEnsemble(const std::vector<std::string> &model_paths,
                                   const std::vector<std::string> &scaler_paths,
                                   const std::string &threshold_path,
                                   const std::vector<float> &weights,
                                   std::string &error_msg)
{
    if (model_paths.empty()) {
        error_msg = "ensemble: model_paths is empty";
        return false;
    }
    if (scaler_paths.size() != model_paths.size()) {
        error_msg = "ensemble: scaler_paths size must match model_paths size";
        return false;
    }

    // 가중치 정규화 (비어있으면 균등)
    std::vector<float> norm_weights;
    if (weights.empty() || weights.size() != model_paths.size()) {
        float eq = 1.0f / static_cast<float>(model_paths.size());
        norm_weights.assign(model_paths.size(), eq);
    } else {
        float wsum = 0.0f;
        for (float w : weights) wsum += w;
        if (wsum <= 0.0f) {
            error_msg = "ensemble: weight sum <= 0";
            return false;
        }
        for (float w : weights)
            norm_weights.push_back(w / wsum);
    }

    // 세션 + 모델별 스케일러 로드
    try {
        for (size_t i = 0; i < model_paths.size(); i++) {
            EnsembleModel em;
            em.session = new Ort::Session(
                m_env, model_paths[i].c_str(), m_session_options);
            em.weight  = norm_weights[i];
            if (!LoadScalerInto(scaler_paths[i], em.scaler, error_msg)) {
                delete em.session;
                for (auto &prev : m_ensemble) delete prev.session;
                m_ensemble.clear();
                return false;
            }
            m_ensemble.push_back(em);
        }
    } catch (const std::exception &e) {
        error_msg = "ensemble session load failed: " + std::string(e.what());
        for (auto &em : m_ensemble) delete em.session;
        m_ensemble.clear();
        return false;
    }

    if (!LoadThreshold(threshold_path, error_msg)) return false;

    m_ensemble_mode = true;
    m_loaded        = true;

    error_msg = "ML ensemble load success | models: " +
                std::to_string(model_paths.size()) +
                " | threshold: " + std::to_string(m_threshold);
    return true;
}

// ── 내부 헬퍼: 스케일러 / 임계값 로드 ───────────────────────────

bool AIS_ML::LoadScaler(const std::string &path, std::string &error_msg)
{
    return LoadScalerInto(path, m_scaler, error_msg);
}


bool AIS_ML::LoadScalerInto(const std::string &path, MLScaler &out, std::string &error_msg)
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
            out.min_[i] = j["min"][i].get<float>();
            out.max_[i] = j["max"][i].get<float>();
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

// ── 피처 추가 (슬라이딩 윈도우) ──────────────────────────────────

void AIS_ML::PushFeature(int mmsi,
                          float sog, float cog, float heading,
                          float status, float dt, float dist_km,
                          float cog_hdg_diff, float sog_change,
                          float cog_hdg_change,
                          float speed_consistency,
                          float lat_speed, float lon_speed)
{
    std::array<float, ML_FEATURE_COUNT> feat = {
        sog, cog, heading, status,
        dt, dist_km,
        cog_hdg_diff, sog_change,
        cog_hdg_change,
        speed_consistency,
        lat_speed, lon_speed
    };
    auto &seq = m_sequences[mmsi];
    seq.push_back(feat);
    if ((int)seq.size() > ML_SEQ_LEN)
        seq.pop_front();
}

// ── 내부 헬퍼: 단일 세션 MSE 계산 ───────────────────────────────

float AIS_ML::RunSession(Ort::Session *session,
                          const MLScaler &scaler,
                          const std::deque<std::array<float, ML_FEATURE_COUNT>> &seq) const
{
    // 모델별 스케일러로 입력 벡터 구성
    std::vector<float> input_data;
    input_data.reserve(ML_SEQ_LEN * ML_FEATURE_COUNT);
    for (auto &feat : seq) {
        for (int i = 0; i < ML_FEATURE_COUNT; i++)
            input_data.push_back(scaler.scale(i, feat[i]));
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

    auto output_tensors = session->Run(
        Ort::RunOptions{nullptr},
        input_names,  &input_tensor, 1,
        output_names, 1
    );

    const float *output_data = output_tensors[0].GetTensorData<float>();
    float mse = 0.0f;
    int n = ML_SEQ_LEN * ML_FEATURE_COUNT;
    for (int i = 0; i < n; i++) {
        float diff = output_data[i] - input_data[i];
        mse += diff * diff;
    }
    return mse / static_cast<float>(n);
}

// ── 이상 탐지 (단일 / 앙상블 자동 분기) ─────────────────────────

bool AIS_ML::DetectAnomaly(int mmsi, float &out_error)
{
    if (!m_loaded) return false;

    auto it = m_sequences.find(mmsi);
    if (it == m_sequences.end() || (int)it->second.size() < ML_SEQ_LEN)
        return false;

    if (m_ensemble_mode) {
        // ── 가중 앙상블: 모델별 스케일러로 각각 스케일링 후 MSE 가중합
        float weighted_mse = 0.0f;
        for (auto &em : m_ensemble)
            weighted_mse += em.weight * RunSession(em.session, em.scaler, it->second);
        out_error = weighted_mse;
    } else {
        // ── 단일 모델: m_scaler 사용
        out_error = RunSession(m_session, m_scaler, it->second);
    }

    return out_error > m_threshold;
}