; Inno Setup script for JARVIS Chat
; Compile with:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
; Output:        build\Output\JarvisChat-Setup.exe

#define MyAppName       "JARVIS Chat"
#define MyAppVersion    "1.9.4"
#define MyAppPublisher  "Aryan Guerrero"
#define MyAppURL        "https://github.com/STEL-LIUM/Jarvis-General-"
#define MyAppExeName    "JarvisChat.exe"
#define MySetupExeName  "JarvisSetup.exe"

[Setup]
AppId={{C7E1A6A2-6E9A-4F2D-B5C8-1A1E2B3C4D5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\JarvisChat
DefaultGroupName=JARVIS Chat
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir=Output
OutputBaseFilename=JarvisChat-Setup
SetupIconFile=icon.ico
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE
WizardImageStretch=no
CloseApplications=force

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";   Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startupicon";   Description: "Launch JARVIS Chat when Windows starts"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Everything PyInstaller produced under dist\JarvisChat
Source: "dist\JarvisChat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JARVIS Chat";   Filename: "{app}\{#MyAppExeName}"
Name: "{group}\JARVIS Setup";  Filename: "{app}\{#MySetupExeName}";  Comment: "Re-run first-time setup (re-install Ollama / re-pull models)"
Name: "{group}\Uninstall JARVIS Chat"; Filename: "{uninstallexe}"
Name: "{autodesktop}\JARVIS Chat"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\JARVIS Chat"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; After install, offer to launch. The launcher itself will run the setup wizard
; on first run (it checks for the marker file).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch JARVIS Chat (runs first-time setup)"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; Wipe the per-user setup marker so a future reinstall starts fresh.
Type: filesandordirs; Name: "{localappdata}\JarvisChat"
