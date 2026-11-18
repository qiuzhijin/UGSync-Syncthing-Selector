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

当前仓库提供源码。Windows 打包可使用 `scripts/syncthing-selector.spec`，它需要官方 Syncthing Windows x64 归档中的文件和 `scripts/build_overlays.ps1` 生成的角标组件。

发布构建可通过 `UGSYNC_UPDATE_CHANNEL_FILE` 指向名为 `update-channel.json` 的文件，包含 `repository`（`owner/repo`）、`version`（例如 `v0.1.0-preview.25`）和 `signer_certificate_sha256`（Windows 代码签名证书 DER 字节的 SHA-256）。配置后，客户端可以从托盘检查更新。用户确认后，客户端下载 `UGSync-Setup-<版本标签>-x64.exe`，核对 GitHub 的资源摘要和 Authenticode 签名者，启动安装程序。

当前没有配置正式的签名安装包，更新入口需要在发布构建时启用。

项目自有代码采用 [MIT](LICENSE)。Syncthing、Qt for Python 等第三方软件遵循各自的许可。同步会传播选中范围内的删除，重要数据应另有独立备份。
