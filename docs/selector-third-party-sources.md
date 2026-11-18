# UGSync Windows installer third-party software

UGSync calls unmodified Syncthing and links dynamically to separately installed Qt and Qt for Python libraries in the program directory. Qt and Qt for Python are provided under LGPL-3.0 where applicable. You may replace or relink the LGPL libraries with compatible modified versions and reverse engineer UGSync as needed to debug those modifications.

- Qt 6.10.1 sources: https://download.qt.io/official_releases/qt/6.10/6.10.1/single/
- Qt for Python 6.10.1 sources: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.1-src/
- Syncthing v2.1.7 source and MPL-2.0 license: https://github.com/syncthing/syncthing/tree/v2.1.7
- WinSparkle v0.9.4 source and notices: https://github.com/vslavik/winsparkle/releases/tag/v0.9.4
- This installer is built using Inno Setup: https://github.com/jrsoftware/issrc

The other runtime notices are provided in this directory.
