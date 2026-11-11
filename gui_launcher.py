"""Entry point for PyInstaller: pyinstaller --onefile --windowed --name Prusa2Panda gui_launcher.py"""

from prusa2panda.gui import main

if __name__ == "__main__":
    main()
