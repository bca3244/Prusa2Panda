"""Entry point for PyInstaller: pyinstaller --onefile --console --name prusa2panda-cli cli_launcher.py"""

from prusa2panda.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
