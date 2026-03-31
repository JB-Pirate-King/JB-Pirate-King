/***************************************************************************
* Minimal AIS Log dialog implementation
**************************************************************************/

#include "tpControlDialogImpl.h"


// debug windows
tpControlDialogImpl::tpControlDialogImpl( wxWindow* parent )
    : tpControlDialogDef( parent, wxID_ANY, _("AIS Log") )
{
    m_bCreateBoundaryHasFocus = false;
    m_bCreateBoundaryPointHasFocus = false;
    m_pfdDialog = NULL;

    SetTitle(_("AIS Log"));
    SetMinSize(wxSize(600, 400));
    SetSize(wxSize(800, 600));
    Layout();
}
void tpControlDialogImpl::SendMessage(const wxString &message)
{
    if(!m_aisLogText) return;
    m_aisLogText->AppendText(message);
    if(!message.EndsWith("\n")) {
        m_aisLogText->AppendText("\n");
    }
}

void tpControlDialogImpl::SetDialogSize( void )
{
}

void tpControlDialogImpl::SetLatLon( double lat, double lon )
{
    (void)lat;
    (void)lon;
}

void tpControlDialogImpl::SetPanels(void)
{
}

wxString tpControlDialogImpl::GetJSONSaveFile( void )
{
    return wxEmptyString;
}

void tpControlDialogImpl::SetJSONSaveFile(wxString SaveFile)
{
    (void)SaveFile;
}

wxString tpControlDialogImpl::GetJSONInputFile()
{
    return wxEmptyString;
}

void tpControlDialogImpl::SetJSONInputFile(wxString InputFile)
{
    (void)InputFile;
}

void tpControlDialogImpl::SetSaveJSONOnStartup(bool SaveJSONOnStartup)
{
    (void)SaveJSONOnStartup;
}

void tpControlDialogImpl::SetIncommingJSONMessages(bool IncommingJSONMessages)
{
    (void)IncommingJSONMessages;
}

void tpControlDialogImpl::SetAppendToSaveFile(bool AppendToSaveFile)
{
    (void)AppendToSaveFile;
}

void tpControlDialogImpl::SetCloseFileAfterEachWrite(bool CloseFileAfterEachWrite)
{
    (void)CloseFileAfterEachWrite;
}

void tpControlDialogImpl::OnButtonClickCreateBoundaryODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickDeleteBoundaryODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickCreateBoundaryPointODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickDeleteBoundaryPointODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickCreateTextPointODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickDeleteTextPointODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickPointIconODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickCreateBoundaryJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickCreateBoundaryPointJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickCreateTextPointJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickDeleteBoundaryJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickDeleteBoundaryPointJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickDeleteTextPointJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickPointIconJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnCheckBoxSaveJSONOnStartup( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnFileChangeInputJSON( wxFileDirPickerEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnFileChangeOutputJSON( wxFileDirPickerEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnCheckBoxSaveJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnCheckBoxCloseSaveFileAfterEachWrite( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnCheckBoxAppendToFile( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnCheckBoxDeleteFromConfig( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickGetGUIDSODAPI( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickGetGUIDSJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::tpControlOnClickImportJSON( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::tpControlOKClick( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::tpControlCloseClick( wxCommandEvent& event ) { event.Skip(); }
void tpControlDialogImpl::OnButtonClickFonts( wxCommandEvent& event ) { event.Skip(); }
