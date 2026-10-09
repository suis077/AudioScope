# -*- mode: python ; coding: utf-8 -*-
# AudioScope PyInstaller spec —— 目录（onedir）版
#
# 和单文件版内容完全一致，只是 EXE 里不再内嵌整个包，而是输出成
# dist_dir/AudioScope/ 目录：省掉每次启动的解压步骤，实测窗口出现时间
# 从 ~3.3s 降到 ~1.3~1.6s。代价是分发时要整个文件夹一起给。
import os

# 软件图标（由 _mkeico.py 生成，与运行时 QIcon 同一套绘制逻辑）
_ICON = os.path.join(SPECPATH, 'icon.ico')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'sounddevice', '_sounddevice',
        'miniaudio',
        'soundfile',
        'mutagen',
        'imageio_ffmpeg',
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AudioScope',
    icon=_ICON,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AudioScope',
)
