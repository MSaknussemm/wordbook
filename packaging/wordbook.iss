[Setup]
AppId=Wordbook.MSaknussemm
AppName=Wordbook
AppVersion={#AppVersion}
AppPublisher=MSaknussemm
AppPublisherURL=https://github.com/MSaknussemm/wordbook
DefaultDirName={localappdata}\Programs\Wordbook
DefaultGroupName=Wordbook
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=yes
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\Wordbook.exe
OutputDir=..\build\installer
OutputBaseFilename=Wordbook-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "chinesetrad"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\dist\Wordbook\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Wordbook"; Filename: "{app}\Wordbook.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Wordbook"; Filename: "{app}\Wordbook.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Wordbook.exe"; Description: "{cm:LaunchProgram,Wordbook}"; Flags: nowait postinstall skipifsilent
