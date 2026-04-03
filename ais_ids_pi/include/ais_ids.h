#pragma once
#include "ocpn_plugin.h"

class ais_ids {
public:
    ais_ids();
    ~ais_ids();
    bool detect_anomaly_ais(PlugIn_AIS_Target *target);

    // AIS 타겟 클래스 정의
    // ocpn_plugin.h 의 PlugIn_AIS_Target::Class 필드 값
    static const int AIS_CLASS_A    = 0;  // 대형 상선, 여객선 등
    static const int AIS_CLASS_B    = 1;  // 소형 어선, 레저선 등 
   
};