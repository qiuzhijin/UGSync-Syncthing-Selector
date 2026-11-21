"""Read-only tray activity panel. Network reads run outside the UI thread."""
from contextlib import closing
from datetime import datetime

import httpx
from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from syncthing_bridge.api import Syncthing
from syncthing_bridge.diagnostics import report_exception


class Result(QObject):
    ready = Signal(object, object)


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


class ReadActivity(QRunnable):
    def __init__(self, endpoint, key, folder, tracked, page=1, nas_endpoint='', nas_key=''):
        super().__init__()
        self.endpoint, self.key, self.folder = endpoint, key, folder
        self.tracked = tracked
        self.page = page
        self.nas_endpoint, self.nas_key = nas_endpoint.strip(), nas_key.strip()
        self.signals = Result()

    def run(self):
        try:
            with closing(Syncthing(self.endpoint, self.key)) as api:
                api.http.timeout = httpx.Timeout(4)
                result = api.activity(self.folder, tracked=self.tracked, page=self.page)
                peers = set()
                if self.nas_endpoint and self.nas_key:
                    try:
                        task = next(row for row in api.folders() if row['id'] == self.folder)
                        local_id = api.request('GET', '/rest/system/status')['myID']
                        peers = {device['deviceID'] for device in task.get('devices', [])} - {local_id}
                    except Exception as error:
                        report_exception('verify_nas_activity_peer', error)
            if bool(self.nas_endpoint) != bool(self.nas_key):
                result['notices'].append({'name': 'NAS 动态未连接', 'status': '请同时填写 NAS Syncthing 地址和 API 密钥'})
            elif self.nas_endpoint:
                try:
                    with closing(Syncthing(self.nas_endpoint, self.nas_key)) as nas:
                        nas.http.timeout = httpx.Timeout(4)
                        if not peers or nas.request('GET', '/rest/system/status')['myID'] not in peers:
                            raise ValueError('NAS 设备与此同步任务不匹配')
                        remote = nas.download_activity(self.folder, page=self.page)
                    result['current'].extend(remote['current'])
                    result['queue'].extend(remote['queue'])
                    result['has_more'] |= remote['has_more']
                except Exception as error:
                    report_exception('read_nas_activity', error)
                    result['queue_incomplete'] = True
                    result['notices'].append({'name': 'NAS 动态未读取', 'status': '请检查 NAS 地址、密钥及设备是否共享此任务'})
            self.signals.ready.emit(result, None)
        except Exception as error:
            report_exception('read_local_activity', error)
            self.signals.ready.emit(None, '无法读取文件动态，请检查同步引擎连接。')


class ActivityPanel(QFrame):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Popup)
        from syncthing_bridge.appearance import apply_appearance

        self.setObjectName('activitySurface')
        apply_appearance(self)
        self.setWindowTitle('同步动态')
        self.resize(460, 540)
        self.setMinimumSize(360, 420)
        self.page = 1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        self.heading = QLabel('同步动态')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setProperty('role', 'title')
        layout.addWidget(self.heading)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.detail = QLabel('正在读取状态…')
        self.detail.setProperty('role', 'muted')
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.tabs = QTabWidget()
        self.lists = {}
        for key, title in (('current', '正在传输'), ('queue', '等待队列'), ('recent', '最近完成'), ('notices', '通知')):
            tree = QTreeWidget()
            tree.setHeaderLabels(['文件 / 状态', '时间'])
            tree.setRootIsDecorated(False)
            tree.setColumnWidth(0, 310)
            tree.setWordWrap(True)
            self.lists[key] = tree
            self.tabs.addTab(tree, title)
        layout.addWidget(self.tabs, 1)
        pages = QHBoxLayout()
        self.previous_page = QPushButton('上一页')
        self.previous_page.setEnabled(False)
        self.previous_page.clicked.connect(lambda: self.change_page(-1, owner))
        self.page_label = QLabel('传输 / 队列：第 1 页')
        self.next_page = QPushButton('下一页')
        self.next_page.setEnabled(False)
        self.next_page.clicked.connect(lambda: self.change_page(1, owner))
        pages.addWidget(self.previous_page)
        pages.addWidget(self.page_label)
        pages.addWidget(self.next_page)
        layout.addLayout(pages)
        self.note = QLabel('等待连接。最近完成包含接收记录和本次运行确认的对端接收记录。')
        self.note.setProperty('role', 'muted')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
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
        self.page_label.setText('传输 / 队列：第 %s 页（读取中）' % self.page)
        self.previous_page.setEnabled(False)
        self.next_page.setEnabled(False)
        self.lists['current'].clear()
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

    def overview(self, percent, title, detail, folder):
        self.heading.setText(folder or '同步动态')
        self.progress.setRange(0, 1000 if percent is not None else 0)
        if percent is not None:
            self.progress.setValue(percent)
        self.progress.setFormat(title)
        self.detail.setText(detail)

    def display(self, result, error):
        for tree in self.lists.values():
            tree.clear()
        if error:
            QTreeWidgetItem(self.lists['notices'], [error])
            self.previous_page.setEnabled(self.page > 1)
            self.next_page.setEnabled(False)
            self.page_label.setText('传输 / 队列：第 %s 页（读取失败，可刷新重试）' % self.page)
            self.note.setText('文件动态暂时不可用，旧清单已清空。')
            return
        self.previous_page.setEnabled(self.page > 1)
        self.next_page.setEnabled(result.get('has_more', False))
        page_items = len(result.get('current', [])) + len(result.get('queue', []))
        suffix = '（部分队列未读取）' if result.get('queue_incomplete') else ''
        self.page_label.setText('传输 / 队列：第 %s 页 · 本页 %s 项%s' % (self.page, page_items, suffix))
        for key, rows in result.items():
            if key not in self.lists:
                continue
            for row in rows:
                stamp = row.get('time', '')
                try:
                    stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().strftime('%H:%M:%S')
                except ValueError:
                    stamp = ''
                description = row['status'] + (' · ' + row['direction'] if row.get('direction') else '')
                item = QTreeWidgetItem(self.lists[key], [row['name'] + '\n' + description, stamp])
                item.setToolTip(0, row['name'] + '\n' + description)
            if not rows:
                QTreeWidgetItem(self.lists[key], {
                    'current': ['没有正在下载文件；本机上传状态见顶部摘要。'],
                    'queue': ['部分对端队列暂不可用，详情见通知。' if suffix else
                              '本页没有等待下载或等待上传项目。'],
                    'recent': ['暂无完成记录'],
                    'notices': ['暂无文件错误通知']}[key])
        self.note.setText('“正在传输”仅列出 Syncthing 确认的本机下载文件；本机上传速度和对端剩余量显示在顶部。等待队列不是正在传输列表。最近完成为有限记录。悬停文件查看完整路径。')
