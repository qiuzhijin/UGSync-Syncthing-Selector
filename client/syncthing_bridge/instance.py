"""One selector per user, shared by all copies of the application."""
import hashlib
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QThread
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance:
    def __init__(self, activate, *, directory=None, name=None):
        identity = hashlib.sha256(str(Path.home()).casefold().encode()).hexdigest()[:24]
        self.name = name or 'ugsync-selector-' + identity
        directory = Path(directory or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(directory / (self.name + '.lock')))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._activated)
        self.activate = activate
        self.owned = False

    def acquire(self):
        if not self.lock.tryLock(0):
            # The lock may precede listen while the first process initializes.
            for _ in range(20):
                socket = QLocalSocket()
                socket.connectToServer(self.name)
                if socket.waitForConnected(100):
                    socket.disconnectFromServer()
                    return False
                socket.abort()
                QThread.msleep(50)
            raise RuntimeError('程序已经运行，请从右下角托盘打开已有窗口')
        self.owned = True
        # Only the lock owner may clean a stale endpoint left by a crashed app.
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.release()
            raise RuntimeError('无法建立单实例连接，请稍后重试')
        return True

    def _activated(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
            self.activate()

    def release(self):
        if self.owned:
            self.server.close()
            self.lock.unlock()
            self.owned = False
