# -*- mode: python ; coding: utf-8 -*-
# AudioScope PyInstaller spec —— 单文件（onefile）版
#
# 启动时会先把整个包解压到临时目录：93MB 实测多花约 1.8s（总计 ~3.3s 才见到
# 窗口）。追求秒开请改用它旁边的 AudioScope_dir.spec（目录版，实测 1.3~1.6s）。
import os

# 软件图标（由 _mkeico.py 生成，与运行时 QIcon 同一套绘制逻辑）
_ICON = os.path.join(SPECPATH, 'icon.ico')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # sounddevice / miniaudio 已改为按需导入（写在函数体内），静态扫描不一定
    # 能全部识别，这里显式保留
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
    a.binaries,
    a.datas,
    [],
    name='AudioScope',
    icon=_ICON,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
