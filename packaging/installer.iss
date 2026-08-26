; 打怪升级 - 安装包配置（Inno Setup 6）
; 编译：ISCC.exe packaging/installer.iss
; 注意：本文件必须保存为 UTF-8 带 BOM 编码，否则中文会乱码

#define MyAppName "打怪升级"
#define MyAppVersion "1.5.0"
#define MyAppExeName "MonsterUpgrade.exe"
#define MyAppId "{{8E2B1A3C-4F5D-4A6B-9C1E-3D2F5A6B7C8D}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Monster Upgrade
DefaultDirName={autopf}\monster_update
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\dist\installer
OutputBaseFilename=Setup_MonsterUpgrade
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName={#MyAppName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 游戏本体（PyInstaller onedir 产物：exe + _internal/）
Source: "..\dist\MonsterUpgrade\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 游戏图标（快捷方式/卸载器显示用）
Source: "..\assets\icon.ico"; DestDir: "{app}"

[Icons]
; 桌面快捷方式（始终创建，双击直接启动游戏 exe，无终端窗口）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
; 开始菜单快捷方式
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"

[UninstallDelete]
; data_dir.txt 由 [Code] 在安装时生成（不在 [Files] 跟踪列表），卸载时手动删除
Type: files; Name: "{app}\data_dir.txt"

[Run]
; 安装完成页勾选启动游戏（静默安装时不自动启动）
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DataDirPage: TInputDirWizardPage;

procedure InitializeWizard;
var
  defData: String;
  paramData: String;
begin
  // 支持命令行参数 /DATA=xxx（静默安装测试用），未提供则默认用户主目录下 .monster_update
  paramData := ExpandConstant('{param:DATA}');
  if paramData <> '' then
    defData := paramData
  else
    defData := ExpandConstant('{userpf}\.monster_update');
  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '选择游戏数据目录',
    '游戏存档数据将保存在您选择的目录中',
    '请选择游戏数据（存档）存放位置。默认为用户主目录下的 .monster_update，' +
      '安装后也可在游戏安装目录的 data_dir.txt 文件中修改。',
    True,
    '');
  DataDirPage.Add('');
  DataDirPage.Values[0] := defData;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // 安装完成后把用户选择的数据目录写入安装目录下的 data_dir.txt，
  // 游戏启动时读取该文件定位存档位置（见 db/connection.py）
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\data_dir.txt'), DataDirPage.Values[0], False);
end;