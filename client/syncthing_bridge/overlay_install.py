"""Install and detect the bundled Explorer overlay extension."""
import base64
from pathlib import Path
import subprocess
import sys


_IDS = ('{A22F0821-2B78-49CE-B27C-1041D875B011}',
        '{A22F0822-2B78-49CE-B27C-1041D875B011}',
        '{A22F0823-2B78-49CE-B27C-1041D875B011}')
_EXPLORER_OVERLAYS = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers'
_PAYLOAD_FILES = ('UGSyncOverlay.dll', 'blue.ico', 'green.ico', 'red.ico',
                  'overlay-manifest.json', 'install_overlays.ps1')


class OverlayInstallError(RuntimeError):
    pass


def overlay_extension_installed():
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        for color, identifier in zip(('Blue', 'Green', 'Red'), _IDS):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f'{_EXPLORER_OVERLAYS}\\ 08UGSync {color}') as key:
                registered, _ = winreg.QueryValueEx(key, None)
            if not isinstance(registered, str) or registered.casefold() != identifier.casefold():
                return False
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                'SOFTWARE\\Classes\\CLSID\\' + identifier + '\\InprocServer32') as key:
                library, _ = winreg.QueryValueEx(key, None)
            if not isinstance(library, str) or not Path(library).is_file():
                return False
    except OSError:
        return False
    return True


def _payload_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'overlays'
    return Path(__file__).resolve().parents[2] / 'dist' / 'explorer-overlays'


def install_overlay_extension():
    """Request elevation once to register the bundled, hash-checked extension."""
    if sys.platform != 'win32':
        raise OverlayInstallError('资源管理器同步角标仅支持 Windows。')
    if overlay_extension_installed():
        return True
    payload = _payload_directory()
    missing = [name for name in _PAYLOAD_FILES if not (payload / name).is_file()]
    if missing:
        raise OverlayInstallError('角标安装组件不完整，请重新解压完整客户端包。')
    script = payload / 'install_overlays.ps1'
    arguments = subprocess.list2cmdline(['-NoProfile', '-ExecutionPolicy', 'Bypass',
                                         '-File', str(script), '-Action', 'Install'])
    escaped = arguments.replace("'", "''")
    command = ("$process = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru "
               f"-ArgumentList '{escaped}' -ErrorAction Stop; exit $process.ExitCode")
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-EncodedCommand', encoded], check=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise OverlayInstallError('无法启动角标安装程序。') from None
    if result.returncode != 0 or not overlay_extension_installed():
        raise OverlayInstallError('角标扩展未安装；请在 UAC 提示中允许管理员安装。')
    return True
