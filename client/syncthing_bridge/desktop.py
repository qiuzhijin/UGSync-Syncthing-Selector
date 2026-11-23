"""Current-user startup and the bundled background engine."""
import os
from pathlib import Path
from shutil import which
import subprocess
import sys

import httpx

from syncthing_bridge.api import SyncthingTimeout

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'UGSyncSyncthing'


def startup_command():
    if getattr(sys, 'frozen', False):
        args = [sys.executable, '--autostart']
    else:
        args = [sys.executable, str(Path(__file__).resolve().parents[2] / 'syncthing_start.py'), '--autostart']
    return subprocess.list2cmdline(args)


def startup_enabled(registry=None):
    if registry is None:
        import winreg as registry
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, kind = registry.QueryValueEx(key, RUN_VALUE)
            return kind == registry.REG_SZ and value == startup_command()
    except FileNotFoundError:
        return False


def set_startup(enabled, registry=None):
    if type(enabled) is not bool:
        raise ValueError('自启动选项无效')
    if registry is None:
        import winreg as registry
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
        if enabled:
            registry.SetValueEx(key, RUN_VALUE, 0, registry.REG_SZ, startup_command())
        else:
            try:
                registry.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def engine_available(endpoint):
    try:
        with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
            response = client.get(endpoint + '/rest/noauth/health')
            if response.status_code != 200:
                raise RuntimeError('本机引擎健康检查失败')
            return True
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except httpx.TimeoutException:
        raise SyncthingTimeout('本机同步引擎响应超时，请稍后重试连接') from None


def launch_engine():
    roots = [Path(sys.executable).parent]
    if getattr(sys, 'frozen', False):
        roots.append(Path(sys._MEIPASS))
    else:
        roots.append(Path(__file__).resolve().parents[2])
    name = 'syncthing.exe' if os.name == 'nt' else 'syncthing'
    executable = next((p / 'engine' / name for p in roots if (p / 'engine' / name).is_file()), None)
    if executable is None and os.name != 'nt':
        executable = which(name)
    if executable is None:
        raise RuntimeError('未找到 Syncthing 同步引擎，请检查 UGSync 安装是否完整。')
    return subprocess.Popen([str(executable), 'serve', '--no-browser', '--no-upgrade'],
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True)
