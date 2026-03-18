from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / 'dist'
BUILD_DIR = ROOT / 'build'
ICON_GENERATOR = ROOT / 'scripts' / 'generate_windows_icon.py'
ICON_PATH = ROOT / 'build' / 'app_icon.ico'
ENTRYPOINT = ROOT / 'gui.py'
APP_NAME = 'DeepgramSubtitleGUI'


def main() -> int:
    pyinstaller = shutil.which('pyinstaller') or shutil.which('pyinstaller.exe')
    if pyinstaller is None:
        print('PyInstaller is not installed. Please run: python -m pip install pyinstaller')
        return 1

    subprocess.run([sys.executable, str(ICON_GENERATOR)], check=True, cwd=ROOT)

    cmd = [
        pyinstaller,
        '--noconfirm',
        '--clean',
        '--windowed',
        '--name', APP_NAME,
        '--icon', str(ICON_PATH),
        '--add-data', f'{ROOT / "assets"}{";" if sys.platform.startswith("win") else ":"}assets',
        '--hidden-import', 'PySide6.QtSvg',
        '--collect-all', 'PySide6',
        '--collect-all', 'qdarktheme',
        str(ENTRYPOINT),
    ]

    print('Running:', ' '.join(str(part) for part in cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe_path = DIST_DIR / APP_NAME / (APP_NAME + ('.exe' if sys.platform.startswith('win') else ''))
    print(f'Build complete: {exe_path}')
    print(f'Build artifacts: {BUILD_DIR}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
