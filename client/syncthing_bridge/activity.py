"""Read-only tray activity panel. Network reads run outside the UI thread."""
from contextlib import closing
from datetime import datetime

import httpx
from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from syncthing_bridge.api import Syncthing, SyncthingError, SyncthingTimeout, local_credentials
from syncthing_bridge.desktop import engine_available
from syncthing_bridge.diagnostics import report_exception


class Result(QObject):
    ready = Signal(object, object)


class ReadSelectionData(QRunnable):
    def __init__(self, read):
        super().__init__()
        self.read = read
        self.signals = Result()

    def run(self):
        try:
            result = self.read()
        except Exception as error:
            self.signals.ready.emit(self, (None, error))
        else:
            self.signals.ready.emit(self, (result, None))


class ReadLoginEngine(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = Result()

    def run(self):
        try:
            result = self.read()
        except Exception as error:
            self.signals.ready.emit(None, error)
        else:
            self.signals.ready.emit(result, None)

    def read(self):
        try:
            credentials = local_credentials()
        except SyncthingError:
            if engine_available('http://127.0.0.1:8384'):
                raise
            return None
        if not engine_available(credentials[0]):
            return None
        with closing(Syncthing(*credentials)) as api:
            api.http.timeout = httpx.Timeout(5)
            return credentials, api.folders()


class ReadNasDirectories(QRunnable):
    def __init__(self, endpoint, key, folder, peers, prefixes, *, directories_only, catalog=None):
        super().__init__()
        self.endpoint, self.key, self.folder = endpoint, key, folder
        self.peers, self.prefixes = peers, prefixes
        self.directories_only = directories_only
        self.catalog = catalog
        self.signals = Result()

    @staticmethod
    def retry_timeout(read, *args, **kwargs):
        try:
            return read(*args, **kwargs)
        except SyncthingTimeout:
            return read(*args, **kwargs)

    def run(self):
        try:
            rows = self.read()
        except Exception as error:
            report_exception('browse_nas_selection', error)
            self.signals.ready.emit(self, (None, error))
        else:
            self.signals.ready.emit(self, (rows, None))

    def read(self):
        if self.catalog is not None:
            return self.catalog.listings(self.folder, self.prefixes, directories_only=self.directories_only)
        else:
            with closing(Syncthing(self.endpoint, self.key)) as nas:
                nas.http.timeout = httpx.Timeout(10, connect=4)
                identity = self.retry_timeout(nas.request, 'GET', '/rest/system/status')
                if not identity or identity.get('myID') not in self.peers:
                    raise SyncthingError('NAS 设备与此同步任务不匹配')
                return {prefix: self.retry_timeout(
                    nas.browse, self.folder, prefix, directories_only=self.directories_only)
                    for prefix in self.prefixes}


class ReadProgress(QRunnable):
    def __init__(self, endpoint, key, folder):
        super().__init__()
        self.endpoint, self.key, self.folder = endpoint, key, folder
        self.signals = Result()

    def run(self):
        try:
            with closing(Syncthing(self.endpoint, self.key)) as api:
                api.http.timeout = httpx.Timeout(4)
                snapshot = api.progress_snapshot(self.folder)
        except Exception as error:
            report_exception('read_progress', error)
            self.signals.ready.emit(None, error)
        else:
            self.signals.ready.emit(snapshot, None)


def local_overview(states):
    counts = {state: states.count(state) for state, _ in LOCAL_STATES}
    detail = [f'{len(states)} 个任务']
    detail.extend(f'{counts[state]} 个任务{label}' for state, label in LOCAL_STATES if counts[state])
    for state, label in LOCAL_STATES:
        if counts[state]:
            return label, ' · '.join(detail)
    return ('本机已同步' if states else '尚无同步任务'), ' · '.join(detail)


LOCAL_STATES = (
    ('error', '同步出错'),
    ('syncing', '正在同步'),
    ('scanning', '正在扫描'),
    ('waiting', '有待同步文件'),
    ('unknown', '状态暂不可用'),
    ('offline', '等待设备连接'),
    ('paused', '已暂停'),
)


def local_state(status, current):
    if status.get('state') == 'error' or status.get('folderErrors'):
        return 'error'
    state = status.get('state', '')
    if current or state == 'syncing':
        return 'syncing'
    if state.startswith('scan'):
        return 'scanning'
    if any(status.get(key) for key in ('needTotalItems', 'needFiles', 'needBytes', 'needDeletes')):
        return 'waiting'
    return 'idle' if state == 'idle' else 'unknown'


class ReadActivity(QRunnable):
    def __init__(self, endpoint, key, tracked, uploads, page=1):
        super().__init__()
        self.endpoint, self.key = endpoint, key
        self.tracked, self.uploads = tracked, uploads
        self.page = page
        self.signals = Result()

    def run(self):
        try:
            with closing(Syncthing(self.endpoint, self.key)) as api:
                api.http.timeout = httpx.Timeout(4)
                result = self.read(api)
        except Exception as error:
            report_exception('read_local_activity', error)
            self.signals.ready.emit(None, '无法读取文件动态，请检查同步引擎连接。')
        else:
            self.signals.ready.emit(result, None)

    def read(self, api):
        current, queue, recent, notices = [], [], [], []
        states, tracked, uploads, shared = [], {}, {}, {}
        has_more = False
        for task in api.folders():
            folder = task['id']
            label = task.get('label') or folder
            activity = api.activity(folder, tracked=self.tracked.get(folder, ()), page=self.page,
                                    task=task, shared=shared)
            has_more |= activity['has_more']
            tracked[folder] = activity['tracked']
            uploads[folder] = (activity['uploads'] + self.uploads.get(folder, []))[:100]
            if task.get('paused'):
                states.append('paused')
            else:
                try:
                    status = api.status(folder) or {}
                    if status.get('errors') or status.get('pullErrors'):
                        errors = api.request('GET', '/rest/folder/errors', params={
                            'folder': folder, 'page': 1, 'perpage': 1}) or {}
                        status['folderErrors'] = errors.get('errors')
                        for error in status['folderErrors'] or []:
                            activity['notices'].append({'name': error.get('path', ''),
                                                        'status': error.get('error', '同步出错')})
                except SyncthingError as error:
                    activity['notices'].append({'name': '', 'status': f'任务状态暂不可用：{error}'})
                    status = {}
                state = local_state(status, activity['current'])
                if state == 'idle':
                    identity = shared.get('/rest/system/status')
                    connections = shared.get('/rest/system/connections')
                    if isinstance(identity, dict) and isinstance(connections, dict):
                        peers = {device['deviceID'] for device in task.get('devices', [])} - {identity.get('myID')}
                        if peers and not any(connections.get('connections', {}).get(peer, {}).get('connected')
                                             for peer in peers):
                            state = 'offline'
                states.append(state)
            for destination, rows in ((current, activity['current']), (queue, activity['queue']),
                                      (recent, activity['recent'] + uploads[folder]), (notices, activity['notices'])):
                destination.extend(dict(row, folder=folder, task=label) for row in rows)
        title, detail = local_overview(states)
        rows = {}
        completed = sorted(recent, key=lambda row: row.get('time', ''), reverse=True)
        for row in current + queue + (completed[:100] if self.page == 1 else []):
            rows.setdefault((row['folder'], row['name']), row)
        connections = shared.get('/rest/system/connections')
        traffic = connections.get('total') if isinstance(connections, dict) else None
        return {'title': title, 'detail': detail, 'rows': list(rows.values()) + notices,
                'tracked': tracked, 'uploads': uploads, 'traffic': traffic, 'has_more': has_more}


class ActivityPanel(QFrame):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Popup)
        from syncthing_bridge.appearance import apply_appearance

        self.setObjectName('activitySurface')
        apply_appearance(self)
        self.setWindowTitle('同步动态')
        self.resize(590, 540)
        self.setMinimumSize(360, 420)
        self.page = 1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        self.heading = QLabel('本机同步动态')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setProperty('role', 'title')
        layout.addWidget(self.heading)
        self.detail = QLabel('正在读取状态…')
        self.detail.setProperty('role', 'muted')
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.files = QTreeWidget()
        self.files.setHeaderLabels(['文件', '所属任务', '状态', '时间'])
        self.files.setRootIsDecorated(False)
        self.files.setColumnWidth(0, 195)
        self.files.setColumnWidth(1, 105)
        self.files.setColumnWidth(2, 170)
        layout.addWidget(self.files, 1)
        pages = QHBoxLayout()
        self.previous_page = QPushButton('上一页')
        self.previous_page.setEnabled(False)
        self.previous_page.clicked.connect(lambda: self.change_page(-1, owner))
        self.page_label = QLabel('第 1 页')
        self.next_page = QPushButton('下一页')
        self.next_page.setEnabled(False)
        self.next_page.clicked.connect(lambda: self.change_page(1, owner))
        pages.addWidget(self.previous_page)
        pages.addWidget(self.page_label)
        pages.addWidget(self.next_page)
        layout.addLayout(pages)
        self.note = QLabel()
        self.note.setProperty('role', 'muted')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.note.hide()
        buttons = QHBoxLayout()
        settings = QPushButton('目录选择 / 设置')
        settings.setProperty('primary', True)
        settings.clicked.connect(lambda: (self.hide(), owner.show_window()))
        buttons.addWidget(settings)
        refresh = QPushButton('刷新')
        refresh.clicked.connect(owner.refresh_activity)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)

    def change_page(self, delta, owner):
        self.page = max(1, self.page + delta)
        self.page_label.setText('第 %s 页（读取中）' % self.page)
        self.previous_page.setEnabled(False)
        self.next_page.setEnabled(False)
        self.files.clear()
        owner.refresh_activity()

    def show_at(self, tray):
        geometry = tray.geometry()
        screen = QApplication.screenAt(geometry.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(max(area.left(), min(geometry.right() - self.width(), area.right() - self.width())),
                  max(area.top(), min(geometry.top() - self.height(), area.bottom() - self.height())))
        self.show()
        self.raise_()
        self.activateWindow()

    def display(self, result, error):
        self.files.clear()
        self.previous_page.setEnabled(self.page > 1)
        self.page_label.setText(f'第 {self.page} 页')
        self.next_page.setEnabled(bool(result and result.get('has_more')))
        self.heading.setText(result['title'] if result else '本机同步状态暂不可用')
        self.detail.setText(error or result['detail'])
        for row in result['rows'] if result else ():
            stamp = row.get('time', '')
            try:
                stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().strftime('%H:%M:%S')
            except ValueError:
                stamp = ''
            item = QTreeWidgetItem(self.files, [row['name'], row['task'], row['status'], stamp])
            for column in range(3):
                item.setToolTip(column, item.text(column))
        empty = bool(result and not result['rows'])
        self.note.setText('暂无文件动态' if self.page == 1 else '本页暂无文件动态')
        self.note.setVisible(empty)
