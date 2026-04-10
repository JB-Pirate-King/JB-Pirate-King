#pragma once
#include "ocpn_plugin.h"
#include "aisParser.h"
#include "ais_ml.h"
#include <unordered_map>
#include <vector>
#include <cmath>
#include <string>
#include <wx/datetime.h>

class ais_ids {
private:
    std::unordered_map<int, std::vector<AISTarget>> ais_history;
    static const int MAX_HISTORY = 100;

public:
    ais_ids(const std::string &data_dir = "");
    ~ais_ids();

    void to_snapshot(AISTarget &target);
    wxString detect_anomaly_ais(int mmsi);

    const std::unordered_map<int, std::vector<AISTarget>> &get_history() const { return ais_history; }

    AISTarget *get_latest(int mmsi)
    {
        auto it = ais_history.find(mmsi);
        if (it == ais_history.end() || it->second.empty()) return nullptr;
        return &it->second.back();
    }

    static const int AIS_CLASS_A = 0;
    static const int AIS_CLASS_B = 1;

    AIS_Parser *ais_parser;
    AIS_ML     *ais_ml;
    std::string ml_error_msg;
};