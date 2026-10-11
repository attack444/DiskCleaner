; Скрипт установщика для приложения «Очиститель и анализатор диска»
; Сборка: ISCC.exe installer.iss  (Inno Setup 6)
; Требует: dist\DiskCleaner.exe (собранный PyInstaller)

#define MyAppName "Очиститель и анализатор диска"
#define MyAppShortName "DiskCleaner"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "User"
#define MyAppExeName "DiskCleaner.exe"

[Setup]
; Уникальный идентификатор приложения (GUID). Inno сам пишет запись
; в «Установка и удаление программ» на основе AppId.
AppId={{2C7D9E4A-9B41-4D86-A1F8-8E9C5D3B7A22}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppShortName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=DiskCleaner_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 64-битная установка (в Program Files)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Требуются права администратора для Program Files
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; Русский мастер без выбора языка
ShowLanguageDialog=no
; Не закрывать приложение при переустановке
CloseApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "contextmenu"; Description: "Добавить пункт «Очистить с помощью DiskCleaner» в контекстное меню папок"; GroupDescription: "Дополнительно:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Пункт контекстного меню проводника (опционально, для текущего пользователя)
[Registry]
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DiskCleanerFolder"; ValueType: string; ValueName: ""; ValueData: "Очистить с помощью DiskCleaner"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DiskCleanerFolder"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DiskCleanerFolder\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%V"""; Tasks: contextmenu

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
