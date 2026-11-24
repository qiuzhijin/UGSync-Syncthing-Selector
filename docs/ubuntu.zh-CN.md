# UGSync Ubuntu 桌面预览版

此 `.deb` 面向 Ubuntu 24.04 LTS 桌面版、Intel/AMD 64 位（`amd64`），包含 Qt 桌面客户端和 Syncthing 2.1.7。安装时由 APT 处理系统依赖。

```bash
sudo apt install ./ugsync-selector_0.1.47_amd64.deb
ugsync-selector
```

也可以在应用菜单搜索「UGSync 文件夹同步」。客户端会先尝试连接当前用户已经运行的 Syncthing，再按需启动随包引擎。首次配对和添加同步任务仍在 Syncthing 本机管理页完成。使用自定义 Syncthing 配置目录时，通过 `STCONFDIR` 或 `STHOMEDIR` 指定，或在设置页手动连接本机引擎。

如果桌面没有系统托盘，可在主窗口使用「同步动态」。首次配对、目录选择和可选 NAS 目录服务与 Windows 版共用逻辑。Windows 资源管理器角标、Windows 登录自启动和 WinSparkle 更新属于 Windows 版。Ubuntu 版升级时用 APT 安装新版 `.deb`，用户配置保留在当前用户的 XDG 目录。

卸载应用使用 `sudo apt remove ugsync-selector`。Syncthing 配置、索引和同步目录由当前用户管理。

第三方版本与源码：[Syncthing 2.1.7](https://github.com/syncthing/syncthing/tree/v2.1.7)、[Qt 6.10.1](https://download.qt.io/archive/qt/6.10/6.10.1/single/)、[Qt for Python 6.10.1](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.1-src/)、[PyInstaller 6.22.3](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3)。安装后的第三方许可位于 `/usr/share/doc/ugsync-selector/licenses`。
