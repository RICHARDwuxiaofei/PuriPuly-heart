#ifndef BetaPayload
  #error BetaPayload is required
#endif
#ifndef BetaCommit
  #error BetaCommit is required
#endif
#ifndef BetaOutput
  #error BetaOutput is required
#endif

[Setup]
AppId=PuriPulyHeart-Fork-QQ-Auth-Beta
AppName=PuriPuly Heart QQ Auth Beta
AppVersion=2.8.2-qq-auth-{#BetaCommit}
AppVerName=PuriPuly Heart QQ Auth Beta 2.8.2-{#BetaCommit}
UninstallDisplayName=PuriPuly Heart QQ Auth Beta
AppPublisher=RICHARDwuxiaofei
AppPublisherURL=https://github.com/RICHARDwuxiaofei/PuriPuly-heart
DefaultDirName={localappdata}\Programs\PuriPulyHeartQQAuthBeta
DefaultGroupName=PuriPuly Heart QQ Auth Beta
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
UsePreviousGroup=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#BetaOutput}
OutputBaseFilename=PuriPulyHeart-QQAuthBeta-Setup-2.8.2-{#BetaCommit}
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayIcon={app}\PuriPulyHeartQQAuthBeta.exe
SetupLogging=yes
CloseApplications=no
RestartApplications=no
WizardStyle=modern
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\src\puripuly_heart\data\icons\icon.ico

[Files]
Source: "{#BetaPayload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "SOURCE.zip"

[Icons]
Name: "{group}\PuriPuly Heart QQ Auth Beta"; Filename: "{app}\PuriPulyHeartQQAuthBeta.exe"; Comment: "Independent QQ Auth Beta"
Name: "{group}\QQ Auth Beta 认证诊断"; Filename: "{app}\QQ认证诊断.cmd"; Comment: "QQ Auth Beta diagnostics"
Name: "{autodesktop}\PuriPuly Heart QQ Auth Beta"; Filename: "{app}\PuriPulyHeartQQAuthBeta.exe"; Comment: "Independent QQ Auth Beta"

[Run]
Filename: "{app}\PuriPulyHeartQQAuthBeta.exe"; Description: "Launch PuriPuly Heart QQ Auth Beta"; Flags: nowait postinstall skipifsilent unchecked

[Code]
const
  ChannelIdentity = 'PuriPulyHeart-Fork-QQ-Auth-Beta';
  ReparsePointAttribute = $400;

function GetFileAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function NoLinkedAncestors(Path: String): Boolean;
var Parent: String; Attributes: LongWord;
begin
  Result := False;
  Path := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  while Path <> '' do begin
    Attributes := GetFileAttributes(Path);
    if (Attributes <> $FFFFFFFF) and ((Attributes and ReparsePointAttribute) <> 0) then exit;
    Parent := ExtractFileDir(Path);
    if CompareText(Parent, Path) = 0 then break;
    Path := Parent;
  end;
  Result := True;
end;

function NoLinkedTree(Path: String): Boolean;
var Find: TFindRec;
begin
  Result := False;
  if not NoLinkedAncestors(Path) then exit;
  Result := True;
  if FindFirst(AddBackslash(Path) + '*', Find) then begin
    try
      repeat
        if (Find.Name <> '.') and (Find.Name <> '..') then begin
          if (Find.Attributes and ReparsePointAttribute) <> 0 then begin Result := False; exit; end;
          if (Find.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            if not NoLinkedTree(AddBackslash(Path) + Find.Name) then begin Result := False; exit; end;
        end;
      until not FindNext(Find);
    finally
      FindClose(Find);
    end;
  end;
end;

function OwnedOrEmpty(Path: String): Boolean;
var Content: AnsiString; Find: TFindRec;
begin
  Result := not DirExists(Path);
  if Result then exit;
  if LoadStringFromFile(AddBackslash(Path) + '.qq-auth-beta-owned', Content) then begin
    Result := String(Content) = ChannelIdentity;
    exit;
  end;
  Result := True;
  if FindFirst(AddBackslash(Path) + '*', Find) then begin
    try
      repeat
        if (Find.Name <> '.') and (Find.Name <> '..') then begin Result := False; exit; end;
      until not FindNext(Find);
    finally
      FindClose(Find);
    end;
  end;
end;

function SafeInstallPath(): Boolean;
var Candidate, Expected: String;
begin
  Candidate := RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}')));
  Expected := RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{localappdata}\Programs\PuriPulyHeartQQAuthBeta')));
  Result := (CompareText(Candidate, Expected) = 0) and NoLinkedTree(Candidate) and OwnedOrEmpty(Candidate);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ExitCode: Integer;
begin
  Result := '';
  if not SafeInstallPath() then Result := 'QQ Auth Beta requires its dedicated per-user directory without links. Existing ownership could not be confirmed.';
  if (Result = '') and FileExists(ExpandConstant('{app}\app\qq_auth_entry.py')) then
    if not Exec(ExpandConstant('{app}\runtime\python.exe'), '"' + ExpandConstant('{app}\app\qq_auth_entry.py') + '" check-idle', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      Result := 'Please close only PuriPuly Heart QQ Auth Beta before upgrading.';
end;

function InitializeUninstall(): Boolean;
var ExitCode: Integer;
begin
  Result := SafeInstallPath();
  if Result then Result := Exec(ExpandConstant('{app}\runtime\python.exe'), '"' + ExpandConstant('{app}\app\qq_auth_entry.py') + '" check-idle', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
  if not Result then MsgBox('QQ Auth Beta ownership/path check failed. No files were removed.', mbError, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var DeleteData: Boolean; ExitCode: Integer; Parameters: String;
begin
  if CurUninstallStep = usUninstall then begin
    DeleteData := False;
    if UninstallSilent then DeleteData := ExpandConstant('{param:DELETEQQAUTHBETADATA|0}') = '1'
    else DeleteData := MsgBox('同时删除此 QQ Auth Beta 的配置、日志、缓存、内部模型及专属密钥？默认保留。外部模型目录不会被删除。', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if DeleteData then begin
      Parameters := '"' + ExpandConstant('{app}\app\qq_auth_entry.py') + '" cleanup-data';
      if not Exec(ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}\app'), SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then begin
        Log('QQ Auth Beta data cleanup refused or incomplete. Data was retained.');
        if not UninstallSilent then MsgBox('无法确认数据归属或安全清理；已保留测试数据，请查看卸载日志。', mbInformation, MB_OK);
      end;
    end;
  end;
end;
