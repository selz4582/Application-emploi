#define AppName "Carnet Emploi 42"
#define AppVersion "1.0.0"
#define AppExeName "CarnetEmploi42.exe"

[Setup]
AppId={{5E8D9A0E-3153-4938-A99C-56C34DD00942}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\Carnet Emploi 42
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=Carnet-Emploi-42-Installation
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Files]
Source: "..\dist\CarnetEmploi42.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent
