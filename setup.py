from setuptools import setup

APP = ['mac/sidemon.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'iconfile': 'assets/icon.icns',
    'packages': ['psutil', 'requests', 'PIL'],
    'plist': {
        'CFBundleName': 'RpiZeroMon',
        'CFBundleDisplayName': 'RpiZeroMon',
        'CFBundleIdentifier': 'com.sidemon.rpizeromon',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHumanReadableCopyright': 'SideMon Project',
        'LSUIElement': True,  # No dock icon, runs in background
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
