# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：打怪升级 → MonsterUpgrade.exe（onedir，无控制台窗口）

说明：
1. arcade 包根目录有一个无扩展名的 VERSION 文件，PyInstaller 6.x 的
   collect_data_files 会把整个包数据收集进去，与模块分析阶段的目录判定冲突
   （ERROR: needs to create a directory ... VERSION, but there already exists a file）。
   arcade 版本号实际来自 arcade/version.py，纯 VERSION 文件运行时并不需要，
   这里显式只收集 arcade/resources/system 下的系统资源（着色器/字体），
   顺带过滤掉 VERSION 文件并跳过 22.7MB 的示例贴图 assets/。
2. 游戏本体只用 SpriteSolidColor/图元绘制，不加载 arcade 示例贴图。
"""

import os

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # 项目根目录（spec 位于 packaging/ 下）

# 只收集 arcade 系统资源（着色器/字体），过滤掉 VERSION 文件与 assets 示例贴图
arcade_datas = [
    (src, dst) for src, dst in collect_data_files("arcade")
    if dst.startswith("arcade/resources/system")
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=arcade_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir 模式：主程序与资源分离到 _internal/
    name="MonsterUpgrade",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 无控制台窗口，双击直接打开游戏窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MonsterUpgrade",
)