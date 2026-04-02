#pragma once

#include "ais_ids.h"
#include "ocpn_plugin.h"

class ais_ids {
    public:
        ais_ids();
        bool detect_anomaly_ais(PlugIn_AIS_Target *target);
};