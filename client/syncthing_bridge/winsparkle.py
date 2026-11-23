"""WinSparkle integration for certificate-free, signed Windows updates."""
from base64 import b64decode
from binascii import Error as Base64Error
from ctypes import CDLL, CFUNCTYPE, c_char_p, c_int, c_wchar_p
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication


def read_channel(channel):
    try:
        url = channel['appcast'].encode('ascii')
        parts = urlsplit(url)
        key = channel['public_key'].encode('ascii')
        build = channel['build']
        if (parts.scheme != b'https' or not parts.hostname or parts.username is not None
                or parts.fragment or len(b64decode(key, validate=True)) != 32
                or type(build) is not int or not 0 < build <= 999999999):
            raise ValueError('Invalid WinSparkle update channel')
    except (TypeError, KeyError, AttributeError, Base64Error) as error:
        raise ValueError('Invalid WinSparkle update channel') from error
    return url, str(build), key


class WinSparkleUpdates(QObject):
    shutdown_requested = Signal()

    def __init__(self, window, menu, root):
        super().__init__(window)
        url, build, key = read_channel(json.loads(
            (root / 'winsparkle-channel.json').read_text(encoding='utf-8-sig')))
        self.library = CDLL(str(root / 'WinSparkle.dll'))
        self._invoke('set_appcast_url', (c_char_p,), url)
        self._invoke('set_app_details', (c_wchar_p,) * 3, 'UGSync', 'UGSync Syncthing Selector', f'v{build}')
        self._invoke('set_app_build_version', (c_wchar_p,), build)
        if self._invoke('set_eddsa_public_key', (c_char_p,), key, result=c_int) != 1:
            raise ValueError('WinSparkle rejected the public key')
        if '--startup-check' in sys.argv:
            return
        self.shutdown_requested.connect(lambda: window.run(window.exit_with_engine),
                                        Qt.ConnectionType.QueuedConnection)
        shutdown_callback_type = CFUNCTYPE(None)
        can_shutdown_type = CFUNCTYPE(c_int)
        self.callback = shutdown_callback_type(self.shutdown_requested.emit)
        self.can_shutdown = can_shutdown_type(lambda: 1)
        self._invoke('set_shutdown_request_callback', (shutdown_callback_type,), self.callback)
        self._invoke('set_can_shutdown_callback', (can_shutdown_type,), self.can_shutdown)
        self._invoke('init', ())
        QApplication.instance().aboutToQuit.connect(lambda: self._invoke('cleanup', ()))
        menu.addAction('检查更新', lambda: self._invoke('check_update_with_ui', ()))
        auto = menu.addAction('自动检查更新')
        auto.setCheckable(True)
        auto.setChecked(bool(self._invoke('get_automatic_check_for_updates', (), result=c_int)))
        auto.toggled.connect(lambda enabled: self._invoke('set_automatic_check_for_updates',
                                                         (c_int,), int(enabled)))

    def _invoke(self, name, argtypes, *args, result=None):
        function = getattr(self.library, 'win_sparkle_' + name)
        function.argtypes = argtypes
        function.restype = result
        return function(*args)


def configure_updates(window, menu):
    if sys.platform != 'win32':
        return None
    root = getattr(sys, '_MEIPASS', None)
    if root and (Path(root) / 'winsparkle-channel.json').is_file():
        return WinSparkleUpdates(window, menu, Path(root))
    from syncthing_bridge.update_ui import Updates
    return Updates(window, menu)
