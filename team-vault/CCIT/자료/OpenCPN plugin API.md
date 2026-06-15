---
notion_url: https://www.notion.so/333be0809830804ea074e3f99cf8b3b7
last_synced: 2026-06-16 02:49
tags: [notion-sync]
---

# OpenCPN plugin API

**Navigation Data Access**
[https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/pm-plugin-api-navigation-data.html](https://opencpn-manuals.github.io/main/ocpn-dev-manual/0.1/pm-plugin-api-navigation-data.html)

```c
class PlugIn_AIS_Target {
public:
    int MMSI;                // Maritime Mobile Service Identity
    int Class;               // AIS class (Class A: 0, Class B: 1)
    int NavStatus;           // Navigational status (0-15)
    double SOG;              // Speed over ground in knots
    double COG;              // Course over ground in degrees true
    double HDG;              // Heading in degrees true
    double Lon;              // Longitude in decimal degrees
    double Lat;              // Latitude in decimal degrees
    int ROTAIS;              // Rate of turn as per AIS message
    char CallSign[8];        // Call sign, includes NULL terminator
    char ShipName[21];       // Ship name, includes NULL terminator
    unsigned char ShipType;  // Ship type as per ITU-R M.1371
    int IMO;                 // IMO ship identification number

    double Range_NM;         // Range to target in nautical miles
    double Brg;              // Bearing to target in degrees true

    // Collision parameters
    bool bCPA_Valid;         // True if CPA calculation is valid
    double TCPA;             // Time to Closest Point of Approach in minutes
    double CPA;              // Closest Point of Approach in nautical miles

    plugin_ais_alarm_type alarm_state;  // Current alarm state for this target
};
```


[https://emsa.europa.eu/cise-documentation/cise-data-model-1.5.3/model/guidelines/687507181.html](https://emsa.europa.eu/cise-documentation/cise-data-model-1.5.3/model/guidelines/687507181.html)




---

로그 출력 및 보는 방법(클로드 피셜)

```c
로그 출력 코드
wxLogMessage("RenderGL called, m_bShowRedDots: %d", m_bShowRedDots);

출력된 로그는 아래 파일에
~/.opencpn/opencpn.log
```
