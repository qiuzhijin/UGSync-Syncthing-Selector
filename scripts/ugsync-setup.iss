#ifndef BundleDir
  #error Specify the absolute path to the prepared Windows bundle with /DBundleDir=...
#endif
#ifndef ProductVersion
  #error Specify the release version with /DProductVersion=...
#endif

[Setup]
AppId=UGSync.Syncthing.Selector
AppName=UGSync Syncthing Selector
AppVersion={#ProductVersion}
AppPublisher=UGSync Contributors
DefaultDirName={autopf}\UGSync-Syncthing-Selector
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no
OutputBaseFilename=UGSync-Setup-v{#ProductVersion}-x64
UninstallDisplayIcon={app}\UGSync-Syncthing-Selector.exe

[Dirs]
Name: "{app}\logs"; Permissions: users-modify

[Files]
Source: "{#BundleDir}\UGSync-Syncthing-Selector.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BundleDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\THIRD-PARTY\*"; DestDir: "{app}\THIRD-PARTY"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\UGSync Syncthing Selector"; Filename: "{app}\UGSync-Syncthing-Selector.exe"

[Run]
Filename: "{app}\UGSync-Syncthing-Selector.exe"; Description: "Launch UGSync Syncthing Selector"; Flags: postinstall nowait skipifsilent runasoriginaluser
