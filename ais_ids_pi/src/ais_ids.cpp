// ais_ids.cpp

#include "ais_ids.h"
#include <nlohmann/json.hpp>
#include <fstream>

ais_ids::ais_ids(const std::string &data_dir)
{
    ais_parser = new AIS_Parser();
    ais_ml     = new AIS_ML();

    if (!data_dir.empty()) {
        // 앙상블 설정 파일이 있으면 가중 앙상블 모드로 로드,
        // 없으면 기존 단일 모델 모드로 폴백.
        // 앙상블 설정 파일 형식 (ensemble_config.json):
        // {
        //   "models":    ["model_dcdetect.onnx", "model_tranad.onnx"],
        //   "scaler":    "scaler_dcdetect.json",
        //   "threshold": "threshold_weighted_ensemble.txt",
        //   "weights":   [0.7, 0.3]
        // }
        const std::string ensemble_cfg = data_dir + "ensemble_config.json";
        std::ifstream cfg_file(ensemble_cfg);

        if (cfg_file.is_open()) {
            // ── 앙상블 모드 ──────────────────────────────────
            try {
                nlohmann::json cfg;
                cfg_file >> cfg;

                std::vector<std::string> model_paths;
                for (auto &m : cfg["models"])
                    model_paths.push_back(data_dir + m.get<std::string>());

                // scaler는 모델별로 각각 지정 (scalers 배열),
                // 없으면 첫 번째 모델명 기반으로 자동 생성
                std::vector<std::string> scaler_paths;
                if (cfg.contains("scalers")) {
                    for (auto &s : cfg["scalers"])
                        scaler_paths.push_back(data_dir + s.get<std::string>());
                } else if (cfg.contains("scaler")) {
                    // 하위 호환: 단일 scaler → 모든 모델에 동일 적용
                    std::string sc = data_dir + cfg["scaler"].get<std::string>();
                    scaler_paths.assign(model_paths.size(), sc);
                }

                std::string threshold_path = data_dir + cfg["threshold"].get<std::string>();

                std::vector<float> weights;
                if (cfg.contains("weights")) {
                    for (auto &w : cfg["weights"])
                        weights.push_back(w.get<float>());
                }

                ais_ml->LoadWeightedEnsemble(
                    model_paths, scaler_paths, threshold_path, weights, ml_error_msg);

            } catch (const std::exception &e) {
                ml_error_msg = "ensemble_config.json parse error: " + std::string(e.what());
            }
        } else {
            // ── 단일 모델 모드 (기존 동작) ───────────────────
            ais_ml->Load(
                data_dir + "model.onnx",
                data_dir + "scaler.json",
                data_dir + "threshold.txt",
                ml_error_msg
            );
        }

        // ML 로드 결과는 LateInit()에서 AIS 로그 창에 출력
    }
}

ais_ids::~ais_ids()
{
    delete ais_parser;
    delete ais_ml;
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

    if (ais_ml->IsLoaded() && history.size() >= 2) {
        AISTarget &prev = history[history.size() - 2];
        AISTarget &cur  = history.back();

        float dt = (float)(cur.rxTime - prev.rxTime);

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
        if (cur.sog >= 0.1f) {
            float expected = (float)(cur.sog * dt / 3600.0 * 1.852);
            speed_consistency = dist_km / (expected + 1e-6f);
        }

        // lat_speed / lon_speed: 위도/경도 방향 변화율 (도/초)
        float lat_speed = (float)((cur.lat - prev.lat) / (dt + 1e-6));
        float lon_speed = (float)((cur.lon - prev.lon) / (dt + 1e-6));

        ais_ml->PushFeature(target.mmsi,
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

    // 마지막 수신 후 600초 이상 경과 시 정상 처리
    if (now - latest.rxTime > 600)
        return wxEmptyString;

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

    // ── 3. 정박/계류 중인데 SOG 0 아님 ───────────────────────
    // if (latest.sog >= 0.5 &&
    //     (latest.navStatus == 1 ||   // 정박
    //      latest.navStatus == 5 ||   // 계류
    //      latest.navStatus == 6))    // 좌초
    //     return wxString::Format("Invalid navigation status (MMSI: %d) (Nav Status: %d), (sog: %f)", mmsi, latest.navStatus, latest.sog);

    // ── 4. COG/HDG 불일치 ─────────────────────────────────────
    // if (latest.cog < 360.0 && latest.hdg < 360.0) {
    //     double diff = std::abs(latest.cog - latest.hdg);
    //     if (diff > 180.0) diff = 360.0 - diff;
    //     if (diff > 100.0)
    //         return wxString::Format("COG/HDG mismatch (MMSI: %d) (COG: %.1f) (HDG: %d) (Diff: %.1f)", mmsi, latest.cog, latest.hdg, diff);
    // }

    // ── 이전 기록 없으면 정상 ─────────────────────────────────
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

    // ── 8. ML 이상 탐지 ────────────────────────────────────────
    if (ais_ml->IsLoaded()) {
        float ml_error = 0.0f;
        if (ais_ml->DetectAnomaly(mmsi, ml_error)) {
            const char *mode = ais_ml->IsEnsemble() ? "ENSEMBLE" : "SINGLE";
            return wxString::Format(
                "ML anomaly detected [%s] (MMSI: %d) (Score: %.6f / Threshold: %.6f)",
                mode, mmsi, ml_error, ais_ml->GetThreshold());
        }
    }

    return wxEmptyString;
}