; Inno Setup script for the Windows installer (compiled by scripts/build_windows.py).
; Per-user install, no administrator rights, Spanish wizard, Start menu and optional
; desktop shortcut, uninstaller in Settings > Apps.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\tomd"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

[Setup]
AppId={{6B3F7C1A-2D4E-4F59-9A1B-7C8D9E0F1A2B}
AppName=tomd
AppVersion={#AppVersion}
AppVerName=tomd {#AppVersion}
AppPublisher=tomd
AppComments=Convierte PDF, EPUB y artículos de Wikipedia a Markdown, en local.
DefaultDirName={localappdata}\Programs\tomd
DefaultGroupName=tomd
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=tomd-setup-{#AppVersion}
SetupIconFile=..\build\tomd.ico
UninstallDisplayIcon={app}\tomd.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\tomd"; Filename: "{app}\tomd.exe"; Comment: "Convertir documentos a Markdown"
Name: "{group}\Carpeta de resultados"; Filename: "{userdocs}\tomd"
Name: "{group}\Licencias de terceros"; Filename: "{app}\THIRD_PARTY_LICENSES.txt"
Name: "{group}\{cm:UninstallProgram,tomd}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\tomd"; Filename: "{app}\tomd.exe"; Tasks: desktopicon

[Dirs]
Name: "{userdocs}\tomd"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\tomd.exe"; Description: "{cm:LaunchProgram,tomd}"; Flags: nowait postinstall skipifsilent
