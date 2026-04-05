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
#define LocalSttManifestRelativePath "_internal\puripuly_heart\data\models\qwen3-asr-0.6b-int8-sherpa.manifest.json"

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

[CustomMessages]
english.LocalSttPageTitle=Local Speech Model
english.LocalSttPageDescription=Choose where to download the bundled local speech model. App installation continues even if the model download fails.
english.LocalSttSourceLabel=Model source:
english.LocalSttReinstall=Reinstall model even if a valid copy already exists
korean.LocalSttPageTitle=로컬 음성 모델
korean.LocalSttPageDescription=번들된 로컬 음성 모델을 어디서 다운로드할지 선택하세요. 모델 다운로드에 실패해도 앱 설치는 계속됩니다.
korean.LocalSttSourceLabel=모델 소스:
korean.LocalSttReinstall=유효한 설치가 있어도 모델을 다시 설치
japanese.LocalSttPageTitle=ローカル音声モデル
japanese.LocalSttPageDescription=同梱のローカル音声モデルをどこからダウンロードするか選択してください。モデルのダウンロードに失敗してもアプリのインストールは続行されます。
japanese.LocalSttSourceLabel=モデルの取得元:
japanese.LocalSttReinstall=有効なコピーがあってもモデルを再インストールする
chinesesimplified.LocalSttPageTitle=本地语音模型
chinesesimplified.LocalSttPageDescription=选择捆绑的本地语音模型下载来源。即使模型下载失败，应用安装也会继续。
chinesesimplified.LocalSttSourceLabel=模型来源：
chinesesimplified.LocalSttReinstall=即使已有有效安装也重新安装模型
chinesetraditional.LocalSttPageTitle=本地語音模型
chinesetraditional.LocalSttPageDescription=選擇內建本地語音模型的下載來源。即使模型下載失敗，應用程式安裝仍會繼續。
chinesetraditional.LocalSttSourceLabel=模型來源：
chinesetraditional.LocalSttReinstall=即使已有有效安裝也重新安裝模型

[Files]
Source: "{#MyPackagedAppDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyStagedOverlayDir}\{#MyOverlayExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyPackagedAppDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "scripts\installer\install-local-stt-model.ps1"; Flags: dontcopy
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
var
  LocalSttSourcePage: TWizardPage;
  LocalSttSourceLabel: TNewStaticText;
  LocalSttSourceComboBox: TNewComboBox;
  LocalSttReinstallCheckBox: TNewCheckBox;

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

function PathEqualsOrIsUnder(Path: String; RootPath: String): Boolean;
var
  NormalizedPath: String;
  NormalizedRoot: String;
begin
  NormalizedPath := RemoveBackslashUnlessRoot(Path);
  NormalizedRoot := RemoveBackslashUnlessRoot(RootPath);

  if (NormalizedPath = '') or (NormalizedRoot = '') then begin
    Result := False;
    exit;
  end;

  if CompareText(NormalizedPath, NormalizedRoot) = 0 then begin
    Result := True;
    exit;
  end;

  Result :=
    (Length(NormalizedPath) > Length(NormalizedRoot)) and
    (CompareText(Copy(NormalizedPath, 1, Length(NormalizedRoot)), NormalizedRoot) = 0) and
    (
      (NormalizedRoot[Length(NormalizedRoot)] = '\') or
      (NormalizedPath[Length(NormalizedRoot) + 1] = '\')
    );
end;

function DirectoryLooksLikeTemporaryLocation(Path: String): Boolean;
var
  TempRoot: String;
begin
  Result := False;

  TempRoot := RemoveBackslashUnlessRoot(GetEnv('TEMP'));
  if PathEqualsOrIsUnder(Path, TempRoot) then begin
    Result := True;
    exit;
  end;

  TempRoot := RemoveBackslashUnlessRoot(GetEnv('TMP'));
  if PathEqualsOrIsUnder(Path, TempRoot) then begin
    Result := True;
    exit;
  end;

  TempRoot := RemoveBackslashUnlessRoot(ExpandConstant('{localappdata}\Temp'));
  if PathEqualsOrIsUnder(Path, TempRoot) then begin
    Result := True;
    exit;
  end;

  TempRoot := RemoveBackslashUnlessRoot(ExpandConstant('{tmp}'));
  if PathEqualsOrIsUnder(Path, TempRoot) then begin
    Result := True;
    exit;
  end;

  TempRoot := RemoveBackslashUnlessRoot(ExpandConstant('{win}\Temp'));
  if PathEqualsOrIsUnder(Path, TempRoot) then begin
    Result := True;
    exit;
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

  DefaultDir := ExpandConstant('{autopf}\{#MyAppDirName}');
  if RemoveBackslashUnlessRoot(DefaultDir) = CandidateDir then begin
    exit;
  end;

  if DirectoryLooksLikeRepositoryCheckout(CandidateDir) then begin
    Log('Resetting suspicious install dir inside a repository checkout: ' + CandidateDir);
    WizardForm.DirEdit.Text := DefaultDir;
    exit;
  end;

  if DirectoryLooksLikeTemporaryLocation(CandidateDir) then begin
    Log('Resetting suspicious install dir inside a temporary directory: ' + CandidateDir);
    WizardForm.DirEdit.Text := DefaultDir;
    exit;
  end;
end;

function GetDefaultLocalSttSource(): String;
begin
  if ActiveLanguage() = 'chinesesimplified' then begin
    Result := 'modelscope';
  end else begin
    Result := 'huggingface';
  end;
end;

procedure InitializeLocalSttWizardPage();
var
  DefaultSource: String;
begin
  LocalSttSourcePage := CreateCustomPage(
    wpSelectTasks,
    ExpandConstant('{cm:LocalSttPageTitle}'),
    ExpandConstant('{cm:LocalSttPageDescription}')
  );

  LocalSttSourceLabel := TNewStaticText.Create(LocalSttSourcePage);
  LocalSttSourceLabel.Parent := LocalSttSourcePage.Surface;
  LocalSttSourceLabel.Left := 0;
  LocalSttSourceLabel.Top := ScaleY(8);
  LocalSttSourceLabel.Caption := ExpandConstant('{cm:LocalSttSourceLabel}');

  LocalSttSourceComboBox := TNewComboBox.Create(LocalSttSourcePage);
  LocalSttSourceComboBox.Parent := LocalSttSourcePage.Surface;
  LocalSttSourceComboBox.Left := 0;
  LocalSttSourceComboBox.Top := LocalSttSourceLabel.Top + LocalSttSourceLabel.Height + ScaleY(8);
  LocalSttSourceComboBox.Width := LocalSttSourcePage.SurfaceWidth;
  LocalSttSourceComboBox.Style := csDropDownList;
  LocalSttSourceComboBox.Items.Add('Hugging Face');
  LocalSttSourceComboBox.Items.Add('ModelScope');

  DefaultSource := GetDefaultLocalSttSource();
  if DefaultSource = 'modelscope' then begin
    LocalSttSourceComboBox.ItemIndex := 1;
  end else begin
    LocalSttSourceComboBox.ItemIndex := 0;
  end;

  LocalSttReinstallCheckBox := TNewCheckBox.Create(LocalSttSourcePage);
  LocalSttReinstallCheckBox.Parent := LocalSttSourcePage.Surface;
  LocalSttReinstallCheckBox.Left := 0;
  LocalSttReinstallCheckBox.Top := LocalSttSourceComboBox.Top + LocalSttSourceComboBox.Height + ScaleY(16);
  LocalSttReinstallCheckBox.Width := LocalSttSourcePage.SurfaceWidth;
  LocalSttReinstallCheckBox.Checked := False;
  LocalSttReinstallCheckBox.Caption := ExpandConstant('{cm:LocalSttReinstall}');
end;

function GetSelectedLocalSttSource(): String;
begin
  Result := GetDefaultLocalSttSource();

  if LocalSttSourceComboBox = nil then begin
    exit;
  end;

  if LocalSttSourceComboBox.ItemIndex = 1 then begin
    Result := 'modelscope';
  end else begin
    Result := 'huggingface';
  end;
end;

function GetLocalSttReinstallEnabled(): Boolean;
begin
  Result := False;
  if LocalSttReinstallCheckBox <> nil then begin
    Result := LocalSttReinstallCheckBox.Checked;
  end;
end;

function ResolveLocalSttAppDataRoot(): String;
var
  OverrideRoot: String;
begin
  OverrideRoot := GetEnv('PURIPULY_HEART_LOCAL_STT_APPDATA_ROOT');
  if OverrideRoot <> '' then begin
    Result := OverrideRoot;
  end else begin
    Result := ExpandConstant('{localappdata}\puripuly-heart');
  end;
end;

procedure RunLocalSttModelInstall();
var
  ScriptPath: String;
  ManifestPath: String;
  PowerShellPath: String;
  Params: String;
  ResultCode: Integer;
begin
  ManifestPath := ExpandConstant('{app}\{#LocalSttManifestRelativePath}');
  if not FileExists(ManifestPath) then begin
    Log('Local STT manifest not found after install: ' + ManifestPath);
    exit;
  end;

  ExtractTemporaryFile('install-local-stt-model.ps1');
  ScriptPath := ExpandConstant('{tmp}\install-local-stt-model.ps1');
  PowerShellPath := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  if not FileExists(PowerShellPath) then begin
    PowerShellPath := 'powershell.exe';
  end;

  Params :=
    '-NoLogo -NoProfile -ExecutionPolicy Bypass -File ' + AddQuotes(ScriptPath) +
    ' -ManifestPath ' + AddQuotes(ManifestPath) +
    ' -AppDataRoot ' + AddQuotes(ResolveLocalSttAppDataRoot()) +
    ' -SelectedSource ' + AddQuotes(GetSelectedLocalSttSource());

  if GetLocalSttReinstallEnabled() then begin
    Params := Params + ' -Reinstall';
  end;

  if not Exec(PowerShellPath, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
    Log('Failed to launch local STT provisioning script; continuing app install.');
    exit;
  end;

  if ResultCode <> 0 then begin
    Log('Local STT provisioning failed with exit code ' + IntToStr(ResultCode) + '; continuing app install.');
  end else begin
    Log('Local STT provisioning completed successfully.');
  end;
end;

procedure InitializeWizard();
begin
  ResetSuspiciousInstallDir();
  InitializeLocalSttWizardPage();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    RunLocalSttModelInstall();
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ResetSuspiciousInstallDir();
  Result := '';
end;
