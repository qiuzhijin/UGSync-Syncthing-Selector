# UGSync · Syncthing 文件夹同步

面向 Windows 和 NAS 的中文 Syncthing 界面。通过目录树选择同步范围，在托盘查看进度和近期传输，在资源管理器中查看同步角标。Syncthing 负责扫描、传输、冲突副本和历史版本。

## 从源码运行

在 Windows 安装 Python 3.12 或更新版本，并运行已配置配对与共享的 Syncthing。然后在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ui,dev]"
.\.venv\Scripts\python.exe -m syncthing_bridge
```

安装、目录选择、NAS Docker 和恢复操作见[使用手册](docs/user-guide.zh-CN.md)。

## 更新与发布

Windows x64 安装程序见 [GitHub Releases](https://github.com/qiuzhijin/UGSync-Syncthing-Selector/releases)。旧 ZIP 版本的用户首次升级需退出旧客户端并运行完整安装程序。安装到原程序目录可沿用既有的快捷方式和自启动路径。安装前如正在同步，请在托盘退出客户端并停止本机引擎。

安装版支持在托盘菜单检查更新。WinSparkle 用内置 EdDSA 公钥验证新版 EXE，再启动安装程序。预览版安装程序可能显示 Windows「未知发布者」，其项目级更新签名由 UGSync 验证。

打包使用 `scripts/syncthing-selector.spec` 和 `scripts/ugsync-setup.iss`。前者需要官方 Syncthing Windows x64 归档和 `scripts/build_overlays.ps1` 生成的角标组件。配置 `UGSYNC_WINSPARKLE_DLL` 与 `UGSYNC_WINSPARKLE_CHANNEL` 可把 WinSparkle x64 DLL 和 `scripts/winsparkle-channel.json` 的公开更新配置加入构建。Inno Setup 接收 `/DBundleDir=<完整客户端路径>` 与 `/DProductVersion=<版本>`。程序目录须包含项目许可证和 [第三方材料](docs/selector-third-party-sources.md)。

项目自有代码采用 [MIT](LICENSE)。Syncthing、Qt for Python 等第三方软件遵循各自的许可。同步会传播选中范围内的删除，重要数据应另有独立备份。

## 鸣谢

感谢 [Syncthing](https://github.com/syncthing/syncthing) 的维护者和贡献者。UGSync 使用 Syncthing 作为文件同步引擎，由它负责设备间的文件传输、冲突处理和版本保留。
