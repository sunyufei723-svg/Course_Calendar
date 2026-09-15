# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

asset_dir = Path(SPECPATH).resolve().parent / 'assets'
icon_png = asset_dir / 'course_calendar_icon.png'
icon_ico = asset_dir / 'course_calendar_icon.ico'
datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('rapidocr_onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tzdata')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
datas.append((str(icon_png), 'assets'))


a = Analysis(
    ['..\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'cupy', 'cupyx', 'cupy_backends', 'PIL._avif'],
    noarchive=False,
    optimize=0,
)


def keep_needed_binary(entry):
    destination = entry[0].replace('\\', '/').lower()
    # The timetable uses still images; cv2.pyd has no static or delayed
    # import of the FFmpeg video plugin. The GUI does not accept AVIF files.
    return not (destination.startswith('cv2/opencv_videoio_ffmpeg') or
                destination.startswith('pil/_avif.'))


a.binaries = [entry for entry in a.binaries if keep_needed_binary(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CourseCalendar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_ico),
)
