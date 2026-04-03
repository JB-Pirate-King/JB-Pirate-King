#include "ais_ids.h"

ais_ids::ais_ids() 
{

}

bool ais_ids::detect_anomaly_ais(PlugIn_AIS_Target *target) 
{
    if (!target) return true;

    // ── 0. MMSI 국가 코드(MID) 유효성 검사 ──────────────────────
    // MMSI 앞 3자리가 MID (Maritime Identification Digits)
    // 유효 범위: 201~775 (ITU-R M.585 기준)

    // Class A, B 일 때만 MID 검사
    if (target->Class != AIS_CLASS_A && target->Class != AIS_CLASS_B) {
        return false;
    }

    int mid = target->MMSI / 1000000;
    if (201 <= mid && mid <= 775) return false;


    return true;
}
