///////////////////////////////////////////////////////////////////////////
// Minimal AIS Log dialog definition
///////////////////////////////////////////////////////////////////////////

#pragma once

#include <wx/dialog.h>
#include <wx/sizer.h>
#include <wx/textctrl.h>
#include <wx/string.h>

///////////////////////////////////////////////////////////////////////////////
/// Class tpControlDialogDef
///////////////////////////////////////////////////////////////////////////////
class tpControlDialogDef : public wxDialog
{
protected:
    wxBoxSizer* m_SizerControl;
    wxTextCtrl* m_aisLogText;

public:
    tpControlDialogDef();
    tpControlDialogDef( wxWindow* parent, wxWindowID id = wxID_ANY,
                        const wxString& title = wxEmptyString,
                        const wxPoint& pos = wxDefaultPosition,
                        const wxSize& size = wxDefaultSize,
                        long style = wxDEFAULT_DIALOG_STYLE|wxRESIZE_BORDER );

    bool Create( wxWindow* parent, wxWindowID id, const wxString& title,
                 const wxPoint& pos, const wxSize& size, long style );
};
