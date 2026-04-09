// ais_ids.cpp

#include "ais_ids.h"

ais_ids::ais_ids()
{
    ais_parser = new AIS_Parser();
}

ais_ids::~ais_ids()
{
    delete ais_parser;
}

void ais_ids::to_snapshot(AISTarget &target)
{
    if (target.mmsi <= 0) return;
    if (target.lat == 0.0 && target.lon == 0.0) return;

    // 수신 시각 기록
    target.rxTime = wxDateTime::Now().GetTicks();

    auto &history = ais_history[target.mmsi];
    history.push_back(target);
    if ((int)history.size() > MAX_HISTORY)
        history.erase(history.begin());
}

wxString ais_ids::detect_anomaly_ais(int mmsi)
{
    auto it = ais_history.find(mmsi);
    if (it == ais_history.end() || it->second.empty()) return wxEmptyString;

    auto &history = it->second;
    AISTarget &latest = history.back();
    time_t now = wxDateTime::Now().GetTicks();

    // 마지막 수신 후 600초 이상 경과 시 정상 처리
    if (now - latest.rxTime > 600) {
        return wxEmptyString;
    }

    // ── 0. 위치 데이터 없음 ───────────────────────────────────
    if (latest.lat >= 91.0 || latest.lon >= 181.0)
        return wxString::Format("Invalid position data (MMSI: %d) (LAT: %.5f) (LON: %.5f)", mmsi, latest.lat, latest.lon);

    // ── 1. MMSI 국가코드 유효성 ───────────────────────────────
    int mid = mmsi / 1000000;
    if (!(201 <= mid && mid <= 775))
        return wxString::Format("Invalid MMSI country code (MMSI: %d) (MID: %d)", mmsi, mid);

    // ── 2. 선종별 최대 속도 초과 ──────────────────────────────
    if (latest.sog < 102.2) {
        double maxSOG = 30.0;
        int    st     = latest.shipType;

        if      (st == 30)                  maxSOG = 15.0;  // 어선
        else if (st == 31 || st == 32)      maxSOG = 12.0;  // 예인선
        else if (st == 33 || st == 34)      maxSOG =  8.0;  // 준설/잠수
        else if (st == 35)                  maxSOG = 35.0;  // 군함
        else if (st == 36 || st == 37)      maxSOG = 20.0;  // 요트/레저
        else if (st >= 60 && st <= 69)      maxSOG = 30.0;  // 여객선
        else if (st >= 70 && st <= 79)      maxSOG = 25.0;  // 화물선
        else if (st >= 80 && st <= 89)      maxSOG = 18.0;  // 탱커

        if (latest.sog > maxSOG)
            return wxString::Format("Speed limit exceeded (MMSI: %d) (SOG: %.1f kn) (Max: %.1f kn) (ShipType: %d)", mmsi, latest.sog, maxSOG, st);
    }

    // ── 3. 정박/계류 중인데 SOG 0 아님 ───────────────────────
    if (latest.sog >= 0.5 &&
        (latest.navStatus == 1 ||   // 정박
         latest.navStatus == 5 ||   // 계류
         latest.navStatus == 6))    // 좌초
        return wxString::Format("Invalid navigation status (MMSI: %d) (Nav Status: %d), (sog: %f)", mmsi, latest.navStatus, latest.sog);

    // ── 4. COG/HDG 불일치 ─────────────────────────────────────
    if (latest.cog < 360.0 && latest.hdg < 360.0) {
        double diff = std::abs(latest.cog - latest.hdg);
        if (diff > 180.0) diff = 360.0 - diff;
        if (diff > 100.0)
            return wxString::Format("COG/HDG mismatch (MMSI: %d) (COG: %.1f) (HDG: %d) (Diff: %.1f)", mmsi, latest.cog, latest.hdg, diff);
    }

    // ── 이전 기록 없으면 정상 ─────────────────────────────────
    if (history.size() < 2) return wxEmptyString;

    AISTarget &last = history[history.size() - 2];
    time_t dt = latest.rxTime - last.rxTime;

    // ── 5. 급격한 속도 변화 ────────────────────────────────────
    if (latest.sog < 102.2 && last.sog < 102.2 && dt > 0 && dt <= 60) {
        double delta = std::abs(latest.sog - last.sog);
        if (delta > 10.0)
            return wxString::Format("Sudden speed change detected (MMSI: %d) (SOG: %.1f kn -> %.1f kn) (Delta: %.1f kn)", mmsi, last.sog, latest.sog, delta);
    }

    // ── 6. 위치 점프 ───────────────────────────────────────────
    if (dt > 0 && dt <= 60) {
        double dLat = (latest.lat - last.lat) * M_PI / 180.0;
        double dLon = (latest.lon - last.lon) * M_PI / 180.0;
        double a    = std::sin(dLat/2) * std::sin(dLat/2)
                    + std::cos(last.lat * M_PI/180.0)
                    * std::cos(latest.lat * M_PI/180.0)
                    * std::sin(dLon/2) * std::sin(dLon/2);
        double dist_km = 6371.0 * 2.0 * std::atan2(std::sqrt(a), std::sqrt(1-a));
        if (dist_km > 5.0)
            return wxString::Format("Position jump detected (MMSI: %d) (Dist: %.2f km) (%.5f,%.5f -> %.5f,%.5f)", mmsi, dist_km, last.lat, last.lon, latest.lat, latest.lon);
    }

    // ── 7. 신호 소실 후 재등장 ─────────────────────────────────
    if (dt > 300)
        return wxString::Format("Signal loss detected (MMSI: %d) (Gap: %lld sec)", mmsi, (long long)dt);

    return wxEmptyString;
}