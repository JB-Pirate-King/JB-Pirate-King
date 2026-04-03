#pragma once
#include "ocpn_plugin.h"
#include <unordered_map>
#include <vector>
#include <cmath>
#include <wx/datetime.h>

struct AISSnapshot {
    double  lat;        // 위도
    double  lon;        // 경도
    double  sog;        // 속도 (knots)
    double  cog;        // 침로 (degrees)
    double  hdg;        // 선수방향 (degrees)
    int     navStatus;  // 항행 상태
    time_t  timestamp;  // 수신 시간
};

class ais_ids {
private:
    // MMSI별 AIS 타겟 이력 (키: MMSI, 값: 타겟 벡터)
    std::unordered_map<int, std::vector<AISSnapshot>> ais_history;
    // 벡터 최대 크기 (메모리 관리)
    static const int MAX_HISTORY = 100;

public:
    ais_ids();
    ~ais_ids();
    void to_snapshot(PlugIn_AIS_Target *target);;
    bool detect_anomaly_ais(PlugIn_AIS_Target *target);

    // AIS 타겟 클래스 정의
    // ocpn_plugin.h 의 PlugIn_AIS_Target::Class 필드 값
    static const int AIS_CLASS_A    = 0;  // 대형 상선, 여객선 등
    static const int AIS_CLASS_B    = 1;  // 소형 어선, 레저선 등 



};