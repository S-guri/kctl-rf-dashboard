# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 명세 - KCTL RF Inspector.

빌드: ekctl_env1\\Scripts\\pyinstaller.exe KCTL_RF_Inspector.spec --noconfirm
산출: dist\\KCTL_RF_Inspector\\KCTL_RF_Inspector.exe
"""

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = [], [], []

# Streamlit 실행에 필요한 패키지 전체(데이터 파일 + 서브모듈) 수집
for pkg in ("streamlit", "plotly", "altair", "pydeck", "pyarrow", "docxtpl", "docx", "narwhals"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# importlib.metadata 로 버전을 조회하는 패키지들의 메타데이터
for pkg in ("streamlit", "openai", "plotly", "pandas", "docxtpl", "python-docx",
            "python-dotenv", "pillow", "altair", "pyarrow", "narwhals"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# 앱 본체와 계측 자산
datas += [
    ("app.py", "."),
    ("kctl-rf-dashboard/report_template.docx", "kctl-rf-dashboard"),
    ("kctl-rf-dashboard/img_1.png", "kctl-rf-dashboard"),
    ("kctl-rf-dashboard/img_2.png", "kctl-rf-dashboard"),
    ("kctl-rf-dashboard/img_3.png", "kctl-rf-dashboard"),
]

hiddenimports += [
    "streamlit.runtime.scriptrunner.magic_funcs",
    "streamlit.web.bootstrap",
    "openai",
    "dotenv",
    "PIL._tkinter_finder",
]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KCTL_RF_Inspector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # 서버 로그/오류 확인용 콘솔 유지
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
    name="KCTL_RF_Inspector",
)
