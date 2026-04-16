#pragma once
#include <wx/string.h>
#include <wx/datetime.h>

struct AISTarget {
    int      msgType   = 0;
    int      mmsi      = 0;

    // 위치 (Type 1/2/3/18/19)
    double   lat       = 0.0;
    double   lon       = 0.0;
    double   sog       = 0.0;
    double   cog       = 0.0;
    int      hdg       = 0;
    int      navStatus = 0;
    int      rotAIS    = 0;
    int      timestamp = 0;

    // 정적 정보 (Type 5/19/24)
    wxString shipName;
    wxString callSign;
    wxString destination;
    int      shipType  = 0;
    int      imoNum    = 0;
    double   draught   = 0.0;
    int      dimA=0, dimB=0, dimC=0, dimD=0;

    // ETA (Type 5)
    int      etaMonth  = 0;
    int      etaDay    = 0;
    int      etaHour   = 0;
    int      etaMin    = 0;

    // 수신 시간
    time_t   rxTime    = 0;

    // 수신 시 계산된 파생값
    float    dist_km   = 0.0f;
};

class AIS_Parser
{
public:
    AIS_Parser();
    ~AIS_Parser();

    // 문장 입력 → 파싱 성공 시 true, target에 결과 저장
    bool Parse(const wxString &sentence, AISTarget &target);

    wxString m_partialPayload;

    // 비트 헬퍼
    static int      get_int   (const wxString &p, int start, int len);
    static int      get_signed(const wxString &p, int start, int len);
    static wxString get_str   (const wxString &p, int start, int len);
    static wxString parse_ais_string(const AISTarget &target);
    // 타입별 파서
    bool parse_type_1_2_3(const wxString &p, AISTarget &t);
    bool parse_type_5    (const wxString &p, AISTarget &t);
    bool parse_type_18   (const wxString &p, AISTarget &t);
    bool parse_type_19   (const wxString &p, AISTarget &t);
    bool parse_type_24   (const wxString &p, AISTarget &t);
};