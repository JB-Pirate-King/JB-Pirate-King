#pragma once

#include <onnxruntime_cxx_api.h>
#include <vector>
#include <string>
#include <deque>
#include <array>
#include <unordered_map>
#include <fstream>

// 피처 순서 (12개):
//   0  sog               속력 (knots)
//   1  cog               진행 방향 (도)
//   2  heading           선수 방향 (도)
//   3  status            항행 상태
//   4  dt                이전 신호와 시간 차이 (초)
//   5  dist_km           실제 이동 거리 (km)
//   6  cog_hdg_diff      COG vs HDG 차이 (도, -1=미정의)
//   7  sog_change        이전 대비 SOG 변화량 (knots)
//   8  cog_hdg_change    이전 대비 cog_hdg_diff 변화량
//   9  speed_consistency 실제 이동거리 / SOG 기반 예상 거리
//  10  lat_speed         위도 방향 변화율 (도/초)
//  11  lon_speed         경도 방향 변화율 (도/초)
#define ML_FEATURE_COUNT 12
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

// ── 앙상블 모델 항목 ──────────────────────────────────────────────
struct EnsembleModel {
    Ort::Session *session;  // ONNX 세션
    MLScaler      scaler;   // 모델별 독립 스케일러
    float         weight;   // 가중치 (정규화된 값, 합산 = 1.0)
};

class AIS_ML {
public:
    AIS_ML();
    ~AIS_ML();

    // ── 단일 모델 로드 (기존 인터페이스 유지) ────────────────────
    bool Load(const std::string &model_path,
              const std::string &scaler_path,
              const std::string &threshold_path,
              std::string &error_msg);

    // ── 가중 앙상블 로드 ─────────────────────────────────────────
    // model_paths  : ONNX 파일 경로 목록 (순서 = weights 순서)
    // scaler_path  : 공통 scaler.json (동일 데이터로 학습했으므로 공유)
    // threshold_path: threshold_weighted_ensemble.txt
    // weights      : 각 모델 가중치 (비어있으면 균등 분배, 자동 정규화)
    bool LoadWeightedEnsemble(const std::vector<std::string> &model_paths,
                              const std::vector<std::string> &scaler_paths,
                              const std::string &threshold_path,
                              const std::vector<float> &weights,
                              std::string &error_msg);

    // ── 피처 추가 (단일/앙상블 공통) ────────────────────────────
    void PushFeature(int mmsi,
                     float sog, float cog, float heading,
                     float status, float dt, float dist_km,
                     float cog_hdg_diff, float sog_change,
                     float cog_hdg_change,
                     float speed_consistency,
                     float lat_speed, float lon_speed);

    // ── 이상 탐지 (단일/앙상블 자동 분기) ───────────────────────
    // out_error: 단일=MSE, 앙상블=가중 평균 MSE
    bool DetectAnomaly(int mmsi, float &out_error);

    bool   IsLoaded()     const { return m_loaded; }
    float  GetThreshold() const { return m_threshold; }
    bool   IsEnsemble()    const { return m_ensemble_mode; }
    size_t GetEnsembleSize() const { return m_ensemble.size(); }

    size_t GetSequenceSize(int mmsi) const {
        auto it = m_sequences.find(mmsi);
        return (it != m_sequences.end()) ? it->second.size() : 0;
    }

private:
    // ── 공통 ──────────────────────────────────────────────────────
    Ort::Env            m_env;
    Ort::SessionOptions m_session_options;

    // 단일 모델 전용 스케일러 (앙상블 모드에서는 EnsembleModel::scaler 사용)
    MLScaler            m_scaler;
    float               m_threshold;
    bool                m_loaded;
    bool                m_ensemble_mode;

    // ── 단일 모델 ─────────────────────────────────────────────────
    Ort::Session       *m_session;

    // ── 앙상블 모델 목록 ──────────────────────────────────────────
    std::vector<EnsembleModel> m_ensemble;

    // ── MMSI → 슬라이딩 윈도우 시퀀스 ───────────────────────────
    std::unordered_map<int, std::deque<std::array<float, ML_FEATURE_COUNT>>> m_sequences;

    // ── 내부 헬퍼 ────────────────────────────────────────────────
    bool LoadScaler    (const std::string &path, std::string &error_msg);
    bool LoadScalerInto(const std::string &path, MLScaler &out, std::string &error_msg);
    bool LoadThreshold (const std::string &path, std::string &error_msg);

    // 입력 피처를 스케일러로 변환 후 세션 추론 → MSE 반환
    float RunSession(Ort::Session *session,
                     const MLScaler &scaler,
                     const std::deque<std::array<float, ML_FEATURE_COUNT>> &seq) const;
};