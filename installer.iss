[Setup]
AppName=Neon Defender
AppVersion=2.0.0
AppPublisher=Neon Defender
DefaultDirName={autopf}\NeonDefender
DefaultGroupName=Neon Defender
OutputBaseFilename=Setup
OutputDir=dist_installer
Compression=lzma
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=6.1sp1
SetupIconFile=icon.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Files]
Source: "dist\NeonDefender.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Neon Defender"; Filename: "{app}\NeonDefender.exe"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\Neon Defender"; Filename: "{app}\NeonDefender.exe"; Tasks: desktopicon; IconFilename: "{app}\icon.ico"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
Filename: "{app}\NeonDefender.exe"; Description: "{cm:LaunchProgram,Neon Defender}"; Flags: nowait postinstall skipifsilent

; Примечание: сербский язык (SerbianCyrillic.isl) не входит в стандартный
; дистрибутив Inno Setup и требует отдельного файла .isl из папки
; Languages\Unofficial на сайте jrsoftware.org — CI-сборка (build.yml)
; подхватывает его автоматически, если получится его скачать.
