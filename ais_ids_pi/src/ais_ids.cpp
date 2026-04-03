#include "ais_ids.h"

ais_ids::ais_ids() 
{

}

ais_ids::~ais_ids() 
{

}

void ais_ids::to_snapshot(PlugIn_AIS_Target *target) 
{
    AISSnapshot snapshot;
    snapshot.lat = target->Lat;
    snapshot.lon = target->Lon;
    snapshot.sog = target->SOG;
    snapshot.cog = target->COG;
    snapshot.hdg = target->HDG;
    snapshot.navStatus = target->NavStatus;
    snapshot.timestamp = wxDateTime::Now().GetTicks();

    // MMSI를 키로 사용하여 이력 저장
    ais_history[target->MMSI].push_back(snapshot);

    // 벡터 크기 제한 (메모리 관리)
    if (ais_history[target->MMSI].size() > MAX_HISTORY) {
        ais_history[target->MMSI].erase(ais_history[target->MMSI].begin());
    }
}

bool ais_ids::detect_anomaly_ais(PlugIn_AIS_Target *target)
{
    if (!target) return false;

    // 선박 타겟만 처리 (기지국, AtoN 등 제외)
    if (target->Class == AIS_CLASS_A || target->Class == AIS_CLASS_B) {

        // Class 위조 검증 - MMSI 범위로 재확인
        // 기지국: 00MID (MMSI < 10000000)
        // AtoN:   99MID (MMSI >= 990000000)
        // 정상 선박: 9자리 (100000000 ~ 989999999)
        if (target->MMSI < 100000000 || target->MMSI >= 990000000)
            return true;

        int    mmsi = target->MMSI;
        time_t now  = wxDateTime::Now().GetTicks();

        // ── 0. 위치 데이터 없음 체크  ──────────────
        if (target->Lat >= 91.0 || target->Lon >= 181.0)
            return true;

        // ── 1. MMSI 국가코드 유효성 검사  ──────────
        int mid = mmsi / 1000000;
        if (!(201 <= mid && mid <= 775))
            return true;

        // ── 2. 선종별 최대 속도 초과  ──────────────
        if (target->SOG < 102.2) {
            double maxSOG = 30.0;

            if (target->ShipType == 30) {
                maxSOG = 15.0;                                      // 어선
            } else if (target->ShipType == 31 || target->ShipType == 32) {
                maxSOG = 12.0;                                      // 예인선
            } else if (target->ShipType == 33 || target->ShipType == 34) {
                maxSOG = 8.0;                                       // 준설/잠수
            } else if (target->ShipType == 35) {
                maxSOG = 35.0;                                      // 군함
            } else if (target->ShipType == 36 || target->ShipType == 37) {
                maxSOG = 20.0;                                      // 요트/레저
            } else if (target->ShipType >= 60 && target->ShipType <= 69) {
                maxSOG = 30.0;                                      // 여객선
            } else if (target->ShipType >= 70 && target->ShipType <= 79) {
                maxSOG = 25.0;                                      // 화물선
            } else if (target->ShipType >= 80 && target->ShipType <= 89) {
                maxSOG = 18.0;                                      // 탱커
            }

            if (target->SOG > maxSOG)
                return true;
        }

        // ── 3. 정박/계류 중이 아닌데 SOG 0  ────────
        if (target->SOG < 102.2 && target->SOG < 0.1 &&
            target->NavStatus != 1 &&   // 정박
            target->NavStatus != 5 &&   // 계류
            target->NavStatus != 6) {   // 좌초
            return true;
        }

        // ── 이전 기록 없으면 저장만 하고 리턴 ────────────────────────
        if (ais_history.find(mmsi) == ais_history.end()) {
            to_snapshot(target);
            return false;
        }

        AISSnapshot &last = ais_history[mmsi].back();
        time_t dt = now - last.timestamp; // 현재와 마지막 기록 간 시간 간격 (초)

        // ── 4. 급격한 속도 변화  ─────────────────────
        if (target->SOG < 102.2 && last.sog < 102.2 && dt > 0 && dt <= 60) {
            double delta = std::abs(target->SOG - last.sog);
            if (delta > 10.0)
                return true;
        }

        // ── 5. COG/HDG 불일치  ─────────────────────
        if (target->COG < 360.0 && target->HDG < 360.0) {
            double diff = std::abs(target->COG - target->HDG);
            if (diff > 180.0) diff = 360.0 - diff;
            if (diff > 60.0)
                return true;
        }

        // ── 6. 위치 점프  ────────────────────────────
        if (dt > 0 && dt <= 60) {
            double dLat = (target->Lat - last.lat) * M_PI / 180.0;
            double dLon = (target->Lon - last.lon) * M_PI / 180.0;
            double a    = std::sin(dLat/2) * std::sin(dLat/2)
                        + std::cos(last.lat * M_PI/180.0)
                        * std::cos(target->Lat * M_PI/180.0)
                        * std::sin(dLon/2) * std::sin(dLon/2);
            double dist_km = 6371.0 * 2.0 * std::atan2(std::sqrt(a), std::sqrt(1-a));
            if (dist_km > 5.0)
                return true;
        }

        // ── 7. 신호 소실 후 재등장 ──────────────────
        if (dt > 300)
            return true;

        // 현재 타겟의 상태 스냅샷 저장
        to_snapshot(target);
    }

    return false;
}