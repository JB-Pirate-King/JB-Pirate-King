///////////////////////////////////////////////////////////////////////////
// Minimal AIS Log dialog implementation
///////////////////////////////////////////////////////////////////////////

#include "tpControlDialogDef.h"

tpControlDialogDef::tpControlDialogDef()
{
}

tpControlDialogDef::tpControlDialogDef( wxWindow* parent, wxWindowID id,
                                        const wxString& title,
                                        const wxPoint& pos,
                                        const wxSize& size,
                                        long style )
{
    this->Create( parent, id, title, pos, size, style );
}

bool tpControlDialogDef::Create( wxWindow* parent, wxWindowID id,
                                 const wxString& title,
                                 const wxPoint& pos,
                                 const wxSize& size,
                                 long style )
{
    if (style == 0) {
        style = wxDEFAULT_DIALOG_STYLE | wxRESIZE_BORDER | wxCAPTION;
    }
    if ( !wxDialog::Create( parent, id, title, pos, size, style ) )
        return false;

    this->SetSizeHints( wxSize(480, 320), wxDefaultSize );

    m_SizerControl = new wxBoxSizer( wxVERTICAL );

    m_aisLogText = new wxTextCtrl( this, wxID_ANY, wxEmptyString,
                                  wxDefaultPosition, wxDefaultSize,
                                  wxTE_MULTILINE | wxTE_READONLY | wxTE_RICH2 );
    m_SizerControl->Add( m_aisLogText, 1, wxEXPAND | wxALL, 6 );

    this->SetSizer( m_SizerControl );
    this->Layout();
    if (size == wxDefaultSize || size == wxSize(0, 0)) {
        this->SetSize(wxSize(600, 400));
    }

    return true;
}
