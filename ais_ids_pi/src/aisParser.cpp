#include "aisParser.h"
#include <wx/tokenzr.h>

AIS_Parser::AIS_Parser()  {}
AIS_Parser::~AIS_Parser() {}

// ── 비트 헬퍼 ────────────────────────────────────────────────────
int AIS_Parser::get_int(const wxString &p, int start, int len)
{
    int acc = 0;
    int s   = start - 1;

    for (int i = 0; i < len; i++) {
        int ci = (s + i) / 6;
        int bi =  5 - (s + i) % 6;
        if (ci >= (int)p.Length()) break;

        unsigned char c = (unsigned char)p[ci].GetValue();
        c -= 48;
        if (c > 40) c -= 8;

        acc = (acc << 1) | ((c >> bi) & 1);
    }
    return acc;
}

int AIS_Parser::get_signed(const wxString &p, int start, int len)
{
    int val = get_int(p, start, len);
    if (val & (1 << (len - 1)))
        val -= (1 << len);
    return val;
}

wxString AIS_Parser::get_str(const wxString &p, int start, int len)
{
    wxString result;
    int chars = len / 6;
    for (int i = 0; i < chars; i++) {
        int c = get_int(p, start + i * 6, 6);
        if (c < 32) c += 64;
        if (c == '@') break;
        result += (wxChar)c;
    }
    result.Trim();
    return result;
}

// ── 타입별 파서 ──────────────────────────────────────────────────
bool AIS_Parser::parse_type_1_2_3(const wxString &p, AISTarget &t)
{
    t.navStatus = get_int   (p, 39,  4);
    t.rotAIS    = get_signed(p, 43,  8);
    t.sog       = 0.1 * get_int(p, 51, 10);
    t.lon       = get_signed(p, 62, 28) / 600000.0;
    t.lat       = get_signed(p, 90, 27) / 600000.0;
    t.cog       = 0.1 * get_int(p, 117, 12);
    t.hdg       = get_int   (p, 129,  9);
    t.timestamp = get_int   (p, 138,  6);

    if (t.lon < -180.0 || t.lon > 180.0) return false;
    if (t.lat <  -90.0 || t.lat >  90.0) return false;
    return true;
}

bool AIS_Parser::parse_type_5(const wxString &p, AISTarget &t)
{
    t.imoNum      = get_int(p, 41,  30);
    t.callSign    = get_str(p, 71,  48);
    t.shipName    = get_str(p, 113, 120);
    t.shipType    = get_int(p, 233,  8);
    t.dimA        = get_int(p, 241,  9);
    t.dimB        = get_int(p, 250,  9);
    t.dimC        = get_int(p, 259,  6);
    t.dimD        = get_int(p, 265,  6);
    t.etaMonth    = get_int(p, 275,  4);
    t.etaDay      = get_int(p, 279,  5);
    t.etaHour     = get_int(p, 284,  5);
    t.etaMin      = get_int(p, 289,  6);
    t.draught     = 0.1 * get_int(p, 295, 8);
    t.destination = get_str(p, 303, 120);
    return true;
}

bool AIS_Parser::parse_type_18(const wxString &p, AISTarget &t)
{
    t.sog = 0.1 * get_int   (p, 47, 10);
    t.lon = get_signed(p, 58, 28) / 600000.0;
    t.lat = get_signed(p, 86, 27) / 600000.0;
    t.cog = 0.1 * get_int   (p, 113, 12);
    t.hdg = get_int   (p, 125,  9);

    if (t.lon < -180.0 || t.lon > 180.0) return false;
    if (t.lat <  -90.0 || t.lat >  90.0) return false;
    return true;
}

bool AIS_Parser::parse_type_19(const wxString &p, AISTarget &t)
{
    t.sog      = 0.1 * get_int   (p, 47, 10);
    t.lon      = get_signed(p, 58, 28) / 600000.0;
    t.lat      = get_signed(p, 86, 27) / 600000.0;
    t.cog      = 0.1 * get_int   (p, 113, 12);
    t.hdg      = get_int   (p, 125,  9);
    t.shipName = get_str   (p, 143, 120);
    t.shipType = get_int   (p, 263,  8);
    t.dimA     = get_int   (p, 271,  9);
    t.dimB     = get_int   (p, 280,  9);
    t.dimC     = get_int   (p, 289,  6);
    t.dimD     = get_int   (p, 295,  6);

    if (t.lon < -180.0 || t.lon > 180.0) return false;
    if (t.lat <  -90.0 || t.lat >  90.0) return false;
    return true;
}

bool AIS_Parser::parse_type_24(const wxString &p, AISTarget &t)
{
    int partNum = get_int(p, 39, 2);
    if (partNum == 0) {
        t.shipName = get_str(p, 41, 120);
    } else {
        t.shipType = get_int(p, 41,   8);
        t.callSign = get_str(p, 91,  48);
        t.dimA     = get_int(p, 133,  9);
        t.dimB     = get_int(p, 142,  9);
        t.dimC     = get_int(p, 151,  6);
        t.dimD     = get_int(p, 157,  6);
    }
    return true;
}

// ── 메인 파싱 함수 ───────────────────────────────────────────────
bool AIS_Parser::Parse(const wxString &sentence, AISTarget &target)
{
    if (sentence.IsEmpty()) return false;
    if (sentence.Find(_T("VDM")) == wxNOT_FOUND &&
        sentence.Find(_T("VDO")) == wxNOT_FOUND)
        return false;

    wxArrayString f = wxSplit(sentence, ',');
    if (f.GetCount() < 7) return false;

    long totalFrag = 1, fragNum = 1;
    f[1].ToLong(&totalFrag);
    f[2].ToLong(&fragNum);

    wxString p = f[5];
    if (p.IsEmpty()) return false;

    // 멀티프래그먼트 조립
    if (totalFrag > 1) {
        if (fragNum < totalFrag) {
            m_partialPayload += p;
            return false;
        }
        p = m_partialPayload + p;
        m_partialPayload.Clear();
    }

    target.msgType = get_int(p, 1,  6);
    target.mmsi    = get_int(p, 9, 30);
    if (target.mmsi <= 0) return false;

    switch (target.msgType)
    {
    case 1: case 2: case 3: return parse_type_1_2_3(p, target);
    case 5:                 return parse_type_5    (p, target);
    case 18:                return parse_type_18   (p, target);
    case 19:                return parse_type_19   (p, target);
    case 24:                return parse_type_24   (p, target);
    default:                return false;
    }
}

wxString AIS_Parser::parse_ais_string(const AISTarget &target)
{
    wxString result;

    // 수신 시각
    result += wxString::Format(_T("RxTime=%s "),
    wxDateTime((time_t)target.rxTime).Format(_T("%Y-%m-%d %H:%M:%S")));

    result += wxString::Format(_T("msgType=%d MMSI=%d "), target.msgType, target.mmsi);

    if (!target.shipName.IsEmpty())
        result += wxString::Format(_T("Name=%s "), target.shipName);

    switch (target.msgType)
    {
    case 1: case 2: case 3:
        result += wxString::Format(
            _T("LAT=%.5f LON=%.5f SOG=%.1f COG=%.1f HDG=%d NavStatus=%d ROT=%d TS=%d"),
            target.lat, target.lon, target.sog, target.cog,
            target.hdg, target.navStatus, target.rotAIS, target.timestamp
        );
        break;

    case 5:
        result += wxString::Format(
            _T("CS=%s Dest=%s IMO=%d Type=%d Draught=%.1f Size=%dx%d ETA=%d/%d %02d:%02d"),
            target.callSign, target.destination,
            target.imoNum, target.shipType, target.draught,
            target.dimA + target.dimB, target.dimC + target.dimD,
            target.etaMonth, target.etaDay, target.etaHour, target.etaMin
        );
        break;

    case 18:
        result += wxString::Format(
            _T("LAT=%.5f LON=%.5f SOG=%.1f COG=%.1f HDG=%d"),
            target.lat, target.lon, target.sog, target.cog, target.hdg
        );
        break;

    case 19:
        result += wxString::Format(
            _T("LAT=%.5f LON=%.5f SOG=%.1f COG=%.1f HDG=%d Type=%d Size=%dx%d"),
            target.lat, target.lon, target.sog, target.cog, target.hdg,
            target.shipType,
            target.dimA + target.dimB, target.dimC + target.dimD
        );
        break;

    case 24:
        result += wxString::Format(
            _T("CS=%s Type=%d Size=%dx%d"),
            target.callSign,
            target.shipType,
            target.dimA + target.dimB, target.dimC + target.dimD
        );
        break;

    default:
        result += _T("(Unsupported type)");
        break;
    }

    return result;
}