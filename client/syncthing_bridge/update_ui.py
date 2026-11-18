"""Check for Windows releases without blocking the sync window."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import httpx
from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from syncthing_bridge.release import check_release, copy_verified


class UpdateJob(QRunnable):
    def __init__(self, operation, complete):
        super().__init__()
        self.operation = operation
        self.complete = complete

    def run(self):
        try:
            result = self.operation()
        except (OSError, ValueError, httpx.HTTPError, subprocess.SubprocessError):
            self.complete.emit(False, None)
        else:
            self.complete.emit(True, result)


def _install(release, signer):
    # The temporary installer must outlive the current process.
    with tempfile.NamedTemporaryFile(prefix='UGSync-Setup-', suffix='.exe', delete=False) as target:
        path = Path(target.name)
        try:
            with httpx.stream('GET', release.url, follow_redirects=True, timeout=20) as response:
                response.raise_for_status()
                copy_verified(response.iter_bytes(), target, release)
        except BaseException:
            target.close()
            path.unlink(missing_ok=True)
            raise
    try:
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        signature_check = (
            '$s=Get-AuthenticodeSignature -LiteralPath $env:UGSYNC_UPDATE_PATH; '
            'if($s.Status -ne "Valid" -or $null -eq $s.SignerCertificate){exit 1}; '
            '$hash=[Security.Cryptography.SHA256]::Create().ComputeHash($s.SignerCertificate.RawData); '
            "([BitConverter]::ToString($hash)).Replace('-','')"
        )
        verified = subprocess.run(
            [str(powershell), '-NoProfile', '-NonInteractive', '-Command', signature_check],
            env={**os.environ, 'UGSYNC_UPDATE_PATH': str(path)}, capture_output=True,
            text=True, timeout=30, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if verified.stdout.strip().casefold() != signer.casefold():
            raise ValueError('Unexpected Windows installer publisher')
        os.startfile(path)
    except (OSError, ValueError, subprocess.SubprocessError):
        path.unlink(missing_ok=True)
        raise


class Updates(QObject):
    checked = Signal(bool, object)
    installed = Signal(bool, object)

    def __init__(self, window, menu):
        super().__init__(window)
        self.window = window
        self.job = None
        self.settings = QSettings('UGSync', 'Updates')
        try:
            path = Path(sys._MEIPASS) / 'update-channel.json'
            self.channel = json.loads(path.read_text(encoding='utf-8-sig'))
            self.repository = self.channel['repository']
            self.version = self.channel['version']
            self.signer = self.channel['signer_certificate_sha256']
            if (not isinstance(self.repository, str) or not isinstance(self.version, str)
                    or not isinstance(self.signer, str) or len(self.signer) != 64
                    or any(c not in '0123456789abcdef' for c in self.signer.lower())):
                return
        except (AttributeError, OSError, ValueError, TypeError, KeyError):
            return
        self.checked.connect(self.show_result)
        self.installed.connect(self.show_install_result)
        menu.addAction('检查更新', lambda: self.check(manual=True))
        auto = menu.addAction('自动检查更新')
        auto.setCheckable(True)
        auto.setChecked(self.settings.value('automatic', True, type=bool))
        auto.toggled.connect(lambda enabled: self.settings.setValue('automatic', enabled))
        if '--startup-check' not in sys.argv:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.check)
            self.timer.start(24 * 60 * 60 * 1000)
            QTimer.singleShot(15000, self.check)

    def check(self, *, manual=False):
        if self.job or (not manual and not self.settings.value('automatic', True, type=bool)):
            return
        self.manual = manual
        self.job = UpdateJob(
            lambda: check_release(self.repository, self.version, previews='-preview.' in self.version),
            self.checked,
        )
        QThreadPool.globalInstance().start(self.job)

    @Slot(bool, object)
    def show_result(self, ok, release):
        self.job = None
        if not ok or not release:
            if self.manual:
                message = '暂时无法检查更新。' if not ok else f'当前版本 {self.version} 已是最新。'
                QMessageBox.information(self.window, '软件更新', message)
            return
        choice = QMessageBox.question(
            self.window, '软件更新',
            f'发现新版本 {release.tag}，下载大小 {release.size / (1024 ** 2):.1f} MiB。\n'
            '现在下载安装程序？',
        )
        if choice == QMessageBox.StandardButton.Yes:
            self.job = UpdateJob(lambda: _install(release, self.signer), self.installed)
            QThreadPool.globalInstance().start(self.job)

    @Slot(bool, object)
    def show_install_result(self, ok, _result):
        self.job = None
        if not ok:
            QMessageBox.warning(self.window, '软件更新', '下载安装程序或校验发布者失败。')
