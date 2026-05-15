// ais_ids.cpp

#include "ais_ids.h"
#include <nlohmann/json.hpp>
#include <fstream>

// ML 설정 파일을 읽어 AIS_ML 인스턴스에 로드하는 헬퍼
// cfg_name: "ensemble_config.json" 또는 "ensemble_config_seq5.json"
// fallback_model/scaler/threshold: 단일 모델 폴백 파일명
static void LoadMLFromConfig(AIS_ML *ml,
                              const std::string &data_dir,
                              const std::string &cfg_name,
                              const std::string &fallback_model,
                              const std::string &fallback_scaler,
                              const std::string &fallback_threshold,
                              std::string &error_msg)
{
    const std::string ensemble_cfg = data_dir + cfg_name;
    std::ifstream cfg_file(ensemble_cfg);

    if (cfg_file.is_open()) {
        // ── 앙상블 모드 ──────────────────────────────────
        // 설정 파일 형식:
        // {
        //   "models":    ["model_a.onnx", "model_b.onnx"],
        //   "scalers":   ["scaler_a.json", "scaler_b.json"],  // 또는 "scaler": "scaler.json"
        //   "threshold": "threshold.txt",
        //   "weights":   [0.7, 0.3]
        // }
        try {
            nlohmann::json cfg;
            cfg_file >> cfg;

            std::vector<std::string> model_paths;
            for (auto &m : cfg["models"])
                model_paths.push_back(data_dir + m.get<std::string>());

            std::vector<std::string> scaler_paths;
            if (cfg.contains("scalers")) {
                for (auto &s : cfg["scalers"])
                    scaler_paths.push_back(data_dir + s.get<std::string>());
            } else if (cfg.contains("scaler")) {
                std::string sc = data_dir + cfg["scaler"].get<std::string>();
                scaler_paths.assign(model_paths.size(), sc);
            }

            std::string threshold_path = data_dir + cfg["threshold"].get<std::string>();

            std::vector<float> weights;
            if (cfg.contains("weights")) {
                for (auto &w : cfg["weights"])
                    weights.push_back(w.get<float>());
            }

            ml->LoadWeightedEnsemble(
                model_paths, scaler_paths, threshold_path, weights, error_msg);

        } catch (const std::exception &e) {
            error_msg = cfg_name + " parse error: " + std::string(e.what());
        }
    } else if (!fallback_model.empty()) {
        // ── 단일 모델 폴백 ────────────────────────────────
        std::ifstream model_check(data_dir + fallback_model);
        if (model_check.is_open()) {
            model_check.close();
            ml->Load(
                data_dir + fallback_model,
                data_dir + fallback_scaler,
                data_dir + fallback_threshold,
                error_msg
            );
        }
        // 파일 없으면 조용히 미로드 상태 유지
    }
}

ais_ids::ais_ids(const std::string &data_dir)
{
    ais_parser   = new AIS_Parser();
    ais_ml       = new AIS_ML(ML_SEQ_LEN);        // seq10
    ais_ml_short = new AIS_ML(ML_SEQ_LEN_SHORT);  // seq5

    if (!data_dir.empty()) {
        // ── seq10 메인 모델 로드 ──────────────────────────
        LoadMLFromConfig(ais_ml, data_dir,
            "ensemble_config.json",
            "model.onnx", "scaler.json", "threshold.txt",
            ml_error_msg);

        // ── seq5 조기 탐지 모델 로드 (선택적) ────────────
        // ensemble_config_seq5.json 또는 model_seq5.onnx 가 있으면 로드
        LoadMLFromConfig(ais_ml_short, data_dir,
            "ensemble_config_seq5.json",
            "model_seq5.onnx", "scaler_seq5.json", "threshold_seq5.txt",
            ml_short_error_msg);

        // ML 로드 결과는 LateInit()에서 AIS 로그 창에 출력
    }
}

ais_ids::~ais_ids()
{
    delete ais_parser;
    delete ais_ml;
    delete ais_ml_short;
}

void ais_ids::to_snapshot(AISTarget &target)
{
    if (target.mmsi <= 0) return;
    if (target.lat == 0.0 && target.lon == 0.0) return;

    target.rxTime = wxDateTime::Now().GetTicks();

    auto &history = ais_history[target.mmsi];
    history.push_back(target);
    if ((int)history.size() > MAX_HISTORY)
        history.erase(history.begin());

    if ((ais_ml->IsLoaded() || ais_ml_short->IsLoaded()) && history.size() >= 2) {
        AISTarget &prev = history[history.size() - 2];
        AISTarget &cur  = history.back();

        float dt = (float)(cur.rxTime - prev.rxTime);

        // gap 처리: dt > 600초(10분)이면 시퀀스 연속성 깨짐 → deque 리셋 후 스킵
        if (dt > 600.0f) {
            ais_ml->ClearSequence(target.mmsi);
            ais_ml_short->ClearSequence(target.mmsi);
            return;
        }

        // dt 이상값 처리: 너무 작으면(1초 미만) 동일 메시지 중복 수신으로 간주 → 스킵
        if (dt < 1.0f) {
            return;
        }

        double dLat = (cur.lat - prev.lat) * M_PI / 180.0;
        double dLon = (cur.lon - prev.lon) * M_PI / 180.0;
        double a    = std::sin(dLat/2) * std::sin(dLat/2)
                    + std::cos(prev.lat * M_PI/180.0)
                    * std::cos(cur.lat  * M_PI/180.0)
                    * std::sin(dLon/2)  * std::sin(dLon/2);
        float dist_km = (float)(6371.0 * 2.0 * std::atan2(std::sqrt(a), std::sqrt(1-a)));

        // cog_hdg_diff
        float cog_hdg_diff = -1.0f;
        if (cur.hdg < 511) {
            double diff = std::abs(cur.cog - (double)cur.hdg);
            if (diff > 180.0) diff = 360.0 - diff;
            cog_hdg_diff = (float)diff;
        }

        // sog_change
        float sog_change = std::abs((float)cur.sog - (float)prev.sog);

        // cog_hdg_change
        float cog_hdg_change = 0.0f;
        {
            float prev_cog_hdg_diff = -1.0f;
            if (prev.hdg < 511) {
                double d = std::abs(prev.cog - (double)prev.hdg);
                if (d > 180.0) d = 360.0 - d;
                prev_cog_hdg_diff = (float)d;
            }
            if (cog_hdg_diff >= 0.0f && prev_cog_hdg_diff >= 0.0f)
                cog_hdg_change = std::abs(cog_hdg_diff - prev_cog_hdg_diff);
        }

        // speed_consistency: 실제 이동거리 / SOG 기반 예상 거리 (정상 ≈ 1.0)
        float speed_consistency = 1.0f;
        if (dt > 0.0f && cur.sog >= 0.1f) {
            float expected = (float)(cur.sog * dt / 3600.0 * 1.852);
            speed_consistency = dist_km / (expected + 1e-6f);
        }

        // lat_speed / lon_speed: 위도/경도 방향 변화율 (도/초)
        float lat_speed = (dt > 0.0f) ? (float)((cur.lat - prev.lat) / dt) : 0.0f;
        float lon_speed = (dt > 0.0f) ? (float)((cur.lon - prev.lon) / dt) : 0.0f;

        // seq10 / seq5 모두 동일 피처 push (각자 deque 길이만 다름)
        if (ais_ml->IsLoaded())
            ais_ml->PushFeature(target.mmsi,
                (float)cur.sog, (float)cur.cog,
                (float)cur.hdg, (float)cur.navStatus,
                dt, dist_km,
                cog_hdg_diff, sog_change,
                cog_hdg_change,
                speed_consistency,
                lat_speed, lon_speed);

        if (ais_ml_short->IsLoaded())
            ais_ml_short->PushFeature(target.mmsi,
                (float)cur.sog, (float)cur.cog,
                (float)cur.hdg, (float)cur.navStatus,
                dt, dist_km,
                cog_hdg_diff, sog_change,
                cog_hdg_change,
                speed_consistency,
                lat_speed, lon_speed);
    }
}

wxString ais_ids::detect_anomaly_ais(int mmsi)
{
    auto it = ais_history.find(mmsi);
    if (it == ais_history.end() || it->second.empty()) return wxEmptyString;

    auto &history = it->second;
    AISTarget &latest = history.back();
    time_t now = wxDateTime::Now().GetTicks();

    // 마지막 수신 후 600초 이상 경과 시 시퀀스 정리 후 정상 처리
    if (now - latest.rxTime > 600) {
        if (ais_ml->IsLoaded())       ais_ml->ClearSequence(mmsi);
        if (ais_ml_short->IsLoaded()) ais_ml_short->ClearSequence(mmsi);
        return wxEmptyString;
    }

    // ── 0. 위치 데이터 없음 ───────────────────────────────────
    // if (latest.lat >= 91.0 || latest.lon >= 181.0)
    //     return wxString::Format("Invalid position data (MMSI: %d) (LAT: %.5f) (LON: %.5f)", mmsi, latest.lat, latest.lon);

    // ── 1. MMSI 국가코드 유효성 ───────────────────────────────
    // int mid = mmsi / 1000000;
    // if (!(201 <= mid && mid <= 775))
    //     return wxString::Format("Invalid MMSI country code (MMSI: %d) (MID: %d)", mmsi, mid);

    // ── 2. 선종별 최대 속도 초과 ──────────────────────────────
    // if (latest.sog < 102.2) {
    //     double maxSOG = 30.0;
    //     int    st     = latest.shipType;
    //     if      (st == 30)                  maxSOG = 15.0;  // 어선
    //     else if (st == 31 || st == 32)      maxSOG = 12.0;  // 예인선
    //     else if (st == 33 || st == 34)      maxSOG =  8.0;  // 준설/잠수
    //     else if (st == 35)                  maxSOG = 35.0;  // 군함
    //     else if (st == 36 || st == 37)      maxSOG = 20.0;  // 요트/레저
    //     else if (st >= 60 && st <= 69)      maxSOG = 30.0;  // 여객선
    //     else if (st >= 70 && st <= 79)      maxSOG = 25.0;  // 화물선
    //     else if (st >= 80 && st <= 89)      maxSOG = 18.0;  // 탱커
    //     if (latest.sog > maxSOG)
    //         return wxString::Format("Speed limit exceeded (MMSI: %d) (SOG: %.1f kn) (Max: %.1f kn) (ShipType: %d)", mmsi, latest.sog, maxSOG, st);
    // }

    // ── 규칙 기반: 단일 메시지 내부 모순 검사 ────────────────
    // (이전 기록 없어도 탐지 가능)

    // 3. 정박/계류 중인데 이동 중
    if (latest.sog >= 3.0 &&
        (latest.navStatus == 1 ||   // 정박
         latest.navStatus == 5 ||   // 계류
         latest.navStatus == 6))    // 좌초
        return wxString::Format(
            "[규칙] 정박/계류 중 이동 (MMSI: %d) (NavStatus: %d) (SOG: %.1f kn)",
            mmsi, latest.navStatus, latest.sog);

    // 4. COG/HDG 심각 불일치 (둘 다 유효한 경우만)
    if (latest.cog < 360.0 && latest.hdg < 511) {
        double diff = std::abs(latest.cog - (double)latest.hdg);
        if (diff > 180.0) diff = 360.0 - diff;
        if (diff > 90.0)
            return wxString::Format(
                "[규칙] COG/HDG 불일치 (MMSI: %d) (COG: %.1f) (HDG: %d) (차이: %.1f°)",
                mmsi, latest.cog, latest.hdg, diff);
    }

    // ── 이전 기록 없으면 규칙만 적용 ─────────────────────────
    if (history.size() < 2) return wxEmptyString;

    AISTarget &last = history[history.size() - 2];
    time_t dt = latest.rxTime - last.rxTime;

    // ── 5. 급격한 속도 변화 ────────────────────────────────────
    // if (latest.sog < 102.2 && last.sog < 102.2 && dt > 0 && dt <= 60) {
    //     double delta = std::abs(latest.sog - last.sog);
    //     if (delta > 10.0)
    //         return wxString::Format("Sudden speed change detected (MMSI: %d) (SOG: %.1f kn -> %.1f kn) (Delta: %.1f kn)", mmsi, last.sog, latest.sog, delta);
    // }

    // ── 6. 위치 점프 ───────────────────────────────────────────
    // if (dt > 0 && dt <= 60) {
    //     double dLat = (latest.lat - last.lat) * M_PI / 180.0;
    //     double dLon = (latest.lon - last.lon) * M_PI / 180.0;
    //     double a    = std::sin(dLat/2) * std::sin(dLat/2)
    //                 + std::cos(last.lat * M_PI/180.0)
    //                 * std::cos(latest.lat * M_PI/180.0)
    //                 * std::sin(dLon/2) * std::sin(dLon/2);
    //     double dist_km = 6371.0 * 2.0 * std::atan2(std::sqrt(a), std::sqrt(1-a));
    //     if (dist_km > 5.0)
    //         return wxString::Format("Position jump detected (MMSI: %d) (Dist: %.2f km) (%.5f,%.5f -> %.5f,%.5f)", mmsi, dist_km, last.lat, last.lon, latest.lat, latest.lon);
    // }

    // ── 7. 신호 소실 후 재등장 ─────────────────────────────────
    // if (dt > 300)
    //     return wxString::Format("Signal loss detected (MMSI: %d) (Gap: %lld sec)", mmsi, (long long)dt);

    // ── 8a. ML 조기 탐지 (seq5) ────────────────────────────────────
    // seq10이 쌓이기 전 seq5 모델로 먼저 판단
    if (ais_ml_short->IsLoaded() &&
        ais_ml_short->GetSequenceSize(mmsi) >= (size_t)ML_SEQ_LEN_SHORT) {
        float ml_error = 0.0f;
        if (ais_ml_short->DetectAnomaly(mmsi, ml_error)) {
            const char *mode = ais_ml_short->IsEnsemble() ? "ENSEMBLE" : "SINGLE";
            return wxString::Format(
                "ML anomaly detected [%s/seq5] (MMSI: %d) (Score: %.6f / Threshold: %.6f)",
                mode, mmsi, ml_error, ais_ml_short->GetThreshold());
        }
    }

    // ── 8b. ML 정밀 탐지 (seq10) ───────────────────────────────────
    if (ais_ml->IsLoaded()) {
        float ml_error = 0.0f;
        if (ais_ml->DetectAnomaly(mmsi, ml_error)) {
            const char *mode = ais_ml->IsEnsemble() ? "ENSEMBLE" : "SINGLE";
            return wxString::Format(
                "ML anomaly detected [%s/seq10] (MMSI: %d) (Score: %.6f / Threshold: %.6f)",
                mode, mmsi, ml_error, ais_ml->GetThreshold());
        }
    }

    return wxEmptyString;
}