#pragma once
#include "ocpn_plugin.h"
#include "aisParser.h"
#include <unordered_map>
#include <vector>
#include <cmath>
#include <wx/datetime.h>

class ais_ids {
private:
    // MMSI별 AIS 타겟 이력
    std::unordered_map<int, std::vector<AISTarget>> ais_history;
    static const int MAX_HISTORY = 100;

public:
    ais_ids();
    ~ais_ids();

    // 파싱된 타겟 저장
    void to_snapshot(AISTarget &target);

    // 이상 탐지
    wxString detect_anomaly_ais(int mmsi);

    // 전체 타겟 맵 반환 (렌더링용)
    const std::unordered_map<int, std::vector<AISTarget>> &get_history() const { return ais_history; }

    // 최신 타겟 반환
    AISTarget *get_latest(int mmsi)
    {
        auto it = ais_history.find(mmsi);
        if (it == ais_history.end() || it->second.empty()) return nullptr;
        return &it->second.back();
    }

    static const int AIS_CLASS_A = 0;
    static const int AIS_CLASS_B = 1;

    AIS_Parser *ais_parser;
};