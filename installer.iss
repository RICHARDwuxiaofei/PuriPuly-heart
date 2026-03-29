; Inno Setup Script for PuriPuly <3
; Compile with: ISCC installer.iss

#define MyAppName "PuriPuly <3"
#define MyAppDirName "PuriPulyHeart"
#define MyAppGroupName "PuriPulyHeart"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "salee"
#define MyAppURL "https://github.com/kapitalismho/PuriPuly-heart"
#define MyAppExeName "PuriPulyHeart.exe"
#define MyOverlayExeName "PuriPulyHeartOverlay.exe"
#define MyPackagedAppDir "dist\PuriPulyHeart"
#define MyStagedOverlayDir "build\overlay"

#ifndef MyAppId
  #define MyAppId "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
#endif

[Setup]
; NOTE: AppId uniquely identifies this application.
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppDirName}
DefaultGroupName={#MyAppGroupName}
AllowNoIcons=yes
LicenseFile=LICENSE
OutputDir=installer_output
OutputBaseFilename=PuriPulyHeart-Setup-{#MyAppVersion}
SetupIconFile=src\puripuly_heart\data\icons\icon.ico
UninstallDisplayIcon={app}\PuriPulyHeart.exe
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Auto-upgrade: remember previous install location
UsePreviousAppDir=yes
UsePreviousGroup=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "chinesesimplified"; MessagesFile: "installer\Languages\ChineseSimplified.isl"
Name: "chinesetraditional"; MessagesFile: "installer\Languages\ChineseTraditional.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
Source: "{#MyPackagedAppDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyStagedOverlayDir}\{#MyOverlayExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyPackagedAppDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Icons]
Name: "{group}\{#MyAppGroupName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppGroupName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppGroupName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppGroupName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up user config on uninstall (optional)
Type: filesandordirs; Name: "{localappdata}\puripuly-heart"

[Code]
function DirectoryLooksLikeRepositoryCheckout(Path: String): Boolean;
var
  ProbePath: String;
  ParentPath: String;
  Depth: Integer;
begin
  ProbePath := RemoveBackslashUnlessRoot(Path);
  Result := False;

  if ProbePath = '' then begin
    exit;
  end;

  for Depth := 0 to 8 do begin
    if DirExists(AddBackslash(ProbePath) + '.git') or
       FileExists(AddBackslash(ProbePath) + 'pyproject.toml') or
       FileExists(AddBackslash(ProbePath) + 'AGENTS.md') then begin
      Result := True;
      exit;
    end;

    ParentPath := ExtractFileDir(ProbePath);
    if (ParentPath = '') or (ParentPath = ProbePath) then begin
      exit;
    end;

    ProbePath := ParentPath;
  end;
end;

procedure ResetSuspiciousInstallDir();
var
  CandidateDir: String;
  DefaultDir: String;
begin
  CandidateDir := RemoveBackslashUnlessRoot(WizardForm.DirEdit.Text);
  if CandidateDir = '' then begin
    exit;
  end;

  if not DirectoryLooksLikeRepositoryCheckout(CandidateDir) then begin
    exit;
  end;

  DefaultDir := ExpandConstant('{autopf}\{#MyAppDirName}');
  if RemoveBackslashUnlessRoot(DefaultDir) = CandidateDir then begin
    exit;
  end;

  Log('Resetting suspicious install dir inside a repository checkout: ' + CandidateDir);
  WizardForm.DirEdit.Text := DefaultDir;
end;

procedure InitializeWizard();
begin
  ResetSuspiciousInstallDir();
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ResetSuspiciousInstallDir();
  Result := '';
end;
