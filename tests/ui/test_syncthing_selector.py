from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from syncthing_bridge.__main__ import Window
from syncthing_bridge.activity import ReadActivity


@pytest.fixture(autouse=True)
def isolate_saved_nas_credentials(monkeypatch):
    import syncthing_bridge.__main__ as ui

    monkeypatch.setattr(ui, 'NasCredentialStore', lambda: SimpleNamespace(load=lambda endpoint: None))


def test_directory_page_is_default_and_keeps_selection_when_switching_tabs():
    app = QApplication.instance() or QApplication([])
    window = Window()
    try:
        tabs = window.main_tabs
        selection = tabs.widget(0)
        settings = tabs.widget(1)
        assert tabs.currentWidget() is selection
        assert selection.isAncestorOf(window.tree)
        assert settings.isAncestorOf(window.endpoint)
        assert settings.isAncestorOf(window.nas_key)
        assert settings.isAncestorOf(window.autostart)
        assert settings.isAncestorOf(window.overlay_enabled)
        window.all.setChecked(True)
        tabs.setCurrentWidget(settings)
        tabs.setCurrentWidget(selection)
        assert window.all.isChecked()
    finally:
        window.exiting = True
        window.close()


def test_lazy_children_inherit_selection_and_can_be_excluded():
    app = QApplication.instance() or QApplication([])
    class Engine:
        def browse(self, folder, prefix=''):
            return [{'name': '工作' if not prefix else '缓存',
                     'type': 'FILE_INFO_TYPE_DIRECTORY'}]
        def close(self):
            pass
    window = Window()
    window.api = Engine()
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.populate(None, '', False)
    parent = window.tree.topLevelItem(0)
    parent.setCheckState(0, Qt.CheckState.Checked)
    window.expand(parent)
    child = parent.child(0)
    assert child.checkState(0) == Qt.CheckState.Checked
    child.setCheckState(0, Qt.CheckState.Unchecked)
    assert parent.checkState(0) == Qt.CheckState.PartiallyChecked
    assert child.data(0, Qt.ItemDataRole.UserRole) == '工作/缓存'
    window.exiting = True
    window.close()


def test_direct_child_selection_keeps_siblings_excluded_and_restores_partial_parent():
    app = QApplication.instance() or QApplication([])
    class Engine:
        def browse(self, folder, prefix=''):
            names = ['工作'] if not prefix else ['项目A', '项目B']
            return [{'name': name, 'type': 'FILE_INFO_TYPE_DIRECTORY'} for name in names]
        def close(self):
            pass
    window = Window()
    window.api = Engine()
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.populate(None, '', False)
    parent = window.tree.topLevelItem(0)
    window.expand(parent)
    assert not window.choices
    parent.child(0).setCheckState(0, Qt.CheckState.Checked)
    assert window.choices == {'工作/项目A': True}
    assert parent.checkState(0) == Qt.CheckState.PartiallyChecked
    assert parent.child(1).checkState(0) == Qt.CheckState.Unchecked
    window.tree.clear()
    window.populate(None, '', False)
    parent = window.tree.topLevelItem(0)
    assert parent.checkState(0) == Qt.CheckState.PartiallyChecked
    window.expand(parent)
    assert parent.child(0).checkState(0) == Qt.CheckState.Checked
    assert parent.child(1).checkState(0) == Qt.CheckState.Unchecked
    parent.setCheckState(0, Qt.CheckState.Checked)
    assert window.choices == {'工作': True}
    assert all(parent.child(i).checkState(0) == Qt.CheckState.Checked for i in range(2))
    window.exiting = True
    window.close()


def test_saving_child_selection_includes_local_and_global_direct_files(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    parent = tmp_path / 'Work'
    parent.mkdir()
    (parent / 'local.txt').write_text('local')
    (parent / 'LocalSibling').mkdir()
    window = Window()
    saved = []

    class Engine:
        def folders(self):
            return [{'id': 'test', 'path': str(tmp_path)}]

        def browse(self, folder, prefix='', *, directories_only=True):
            if directories_only:
                return [{'name': 'Work', 'type': 'FILE_INFO_TYPE_DIRECTORY'}]
            if prefix == 'Work':
                return [{'name': 'remote.txt', 'type': 'FILE_INFO_TYPE_FILE'},
                        {'name': 'RemoteSibling', 'type': 'FILE_INFO_TYPE_DIRECTORY'}]
            return []

        def save_selection(self, folder, rules, previous):
            saved.extend(rules)

        def close(self):
            pass

    window.api = Engine()
    window.folders.blockSignals(True)
    window.folders.addItem('Test', 'test')
    window.folders.blockSignals(False)
    window.all.setChecked(False)
    window.populate(None, '', False)
    window.choices = {'Work/Keep': True}
    monkeypatch.setattr(QDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    try:
        window.preview()
        assert [line for line in saved if line.startswith('!')] == [
            '!/Work/local.txt', '!/Work/remote.txt', '!/Work/Keep', '!/Work/Keep/**']
    finally:
        window.exiting = True
        window.close()


@pytest.mark.parametrize('identity,expected', [
    ('nas', ['AI', '申请专利']), ('other', ['申请专利']), ('timeout', ['申请专利']),
])
def test_directory_tree_uses_verified_nas_to_fill_missing_entries(monkeypatch, identity, expected):
    import syncthing_bridge.__main__ as ui
    import syncthing_bridge.activity as activity_module
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    window = Window()
    directory = 'FILE_INFO_TYPE_DIRECTORY'
    attempts = []

    class Local:
        def folders(self):
            return [{'id': 'test', 'devices': [{'deviceID': 'nas'}]}]

        def browse(self, folder, prefix, *, directories_only=True):
            return [{'name': '申请专利', 'type': directory}]

        def close(self):
            pass

    class Nas:
        def __init__(self, endpoint, key):
            self.http = SimpleNamespace(timeout=None)

        def request(self, method, route):
            if identity == 'timeout':
                raise ui.SyncthingTimeout('NAS timeout')
            return {'myID': identity}

        def browse(self, folder, prefix, *, directories_only=True):
            assert prefix == '学习'
            attempts.append(prefix)
            if len(attempts) == 1:
                raise ui.SyncthingTimeout('first browse timed out')
            return [{'name': 'AI', 'type': directory}, {'name': '申请专利', 'type': directory}]

        def close(self):
            pass

    window.api = Local()
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.nas_endpoint.setText('http://nas.invalid:8384')
    window.nas_key.setText('test-key')
    monkeypatch.setattr(activity_module, 'Syncthing', Nas)
    errors = []
    monkeypatch.setattr(ui.QMessageBox, 'warning', lambda *args: errors.append(args[-1]))
    try:
        window.populate(None, '学习', False)
        assert window.loading_directories == {'学习'}
        for _ in range(100):
            if not window.nas_directory_jobs:
                break
            QTest.qWait(10)
        assert not window.nas_directory_jobs and not errors
        assert len(attempts) == (2 if identity == 'nas' else 0)
        assert [window.tree.topLevelItem(i).text(0) for i in range(window.tree.topLevelItemCount())] == expected
        assert ('不完整' in window.directory_notice.text()) == (identity != 'nas')
        if identity != 'nas':
            with pytest.raises(ui.SyncthingError, match='直属文件清单'):
                window.selection_rows('test', '学习', directories_only=False)
    finally:
        window.exiting = True
        window.close()


def test_preview_nas_timeout_keeps_selection_and_ignores_stale_results(monkeypatch):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])
    window = Window()
    callbacks = []
    notices = []
    monkeypatch.setattr(ui.QMessageBox, 'warning', lambda *args: notices.append(args[-1]))
    monkeypatch.setattr(window, 'read_nas_directories',
                        lambda folder, parents, callback, **kwargs: callbacks.append(callback) or True)
    previews = []
    monkeypatch.setattr(window, 'show_selection_preview', lambda *args: previews.append(args))
    window.api = SimpleNamespace(close=lambda: None)
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.populate_rows(None, '', [{'name': 'Work', 'type': 'FILE_INFO_TYPE_DIRECTORY'}])
    window.choices = {'Work/Keep': True}
    try:
        window.preview()
        assert not window.preview_button.isEnabled()
        worker = object()
        window.nas_directory_jobs[worker] = (
            window.progress_display, window.nas_directory_credentials(), callbacks.pop())
        window.nas_directories_ready(worker, (None, ui.SyncthingTimeout('timeout')))
        assert window.preview_button.isEnabled() and '勾选已保留' in notices[-1]
        assert window.choices == {'Work/Keep': True} and not previews

        window.preview()
        window.nas_directory_jobs[worker] = (
            window.progress_display, window.nas_directory_credentials(), callbacks.pop())
        window.progress_display = ui.ProgressDisplay()
        window.nas_directories_ready(worker, ({'Work': []}, None))
        assert not previews
    finally:
        window.exiting = True
        window.close()


def test_connect_local_attaches_without_starting_second_engine(monkeypatch):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])
    window = Window()
    called = []
    monkeypatch.setattr(ui, 'local_credentials', lambda: ('http://127.0.0.1:8384', 'synthetic'))
    monkeypatch.setattr(ui, 'engine_available', lambda endpoint: True)
    monkeypatch.setattr(ui, 'launch_engine', lambda: called.append('spawn'))
    monkeypatch.setattr(window, 'finish_local_connection', lambda: called.append('connect'))
    window.connect_local()
    assert called == ['connect']
    window.exiting = True
    window.close()


def test_close_hides_to_tray_without_closing_connection(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = Window()
    closed = []
    class Engine:
        def close(self):
            closed.append(True)
    window.api = Engine()
    monkeypatch.setattr(window.tray, 'isVisible', lambda: True)
    window.show()
    window.close()
    assert not window.isVisible()
    assert not closed and window.timer.isActive()
    window.exiting = True
    window.close()
    assert closed == [True]


def test_missing_engine_starts_once_while_connection_pending(monkeypatch):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])
    window = Window()
    called = []
    monkeypatch.setattr(ui, 'local_credentials', lambda: ('http://127.0.0.1:8384', 'synthetic'))
    monkeypatch.setattr(ui, 'engine_available', lambda endpoint: False)
    monkeypatch.setattr(ui, 'launch_engine', lambda: called.append('spawn') or object())
    monkeypatch.setattr(ui.QTimer, 'singleShot', lambda *args: None)
    window.connect_local()
    window.connect_local()
    assert called == ['spawn'] and window.starting
    window.exiting = True
    window.close()


def test_login_retries_timeout_without_modal(monkeypatch):
    import syncthing_bridge.__main__ as ui
    from syncthing_bridge.api import SyncthingTimeout

    app = QApplication.instance() or QApplication([])
    window = Window()
    scheduled, attempts = [], []

    def connect():
        attempts.append(True)
        raise SyncthingTimeout('读取同步引擎超时')

    monkeypatch.setattr(window, 'connect_local', connect)
    monkeypatch.setattr(ui.QTimer, 'singleShot', lambda delay, callback: scheduled.append(callback))
    monkeypatch.setattr(ui.QMessageBox, 'warning', lambda *args: pytest.fail('Unexpected startup dialog'))
    try:
        window.start_local_on_login()
        assert '稍后自动重试' in window.status.text()
        scheduled.pop(0)()
        scheduled.pop(0)()
        assert not scheduled and len(attempts) == 3
        assert not window.startup_connection and '连接本机' in window.status.text()
    finally:
        window.exiting = True
        window.close()


def test_exit_stops_local_engine_before_interface(monkeypatch):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])
    window = Window()
    calls = []
    class Engine:
        def __init__(self, endpoint, key):
            assert endpoint == 'http://127.0.0.1:8384'
        def request(self, method, route):
            calls.append((method, route))
        def close(self):
            calls.append('close')
    monkeypatch.setattr(ui, 'Syncthing', Engine)
    monkeypatch.setattr(ui, 'local_credentials', lambda: ('http://127.0.0.1:8384', 'synthetic'))
    monkeypatch.setattr(ui, 'engine_available', lambda endpoint: True)
    monkeypatch.setattr(window, 'quit_interface', lambda: calls.append('quit'))
    window.exit_with_engine()
    assert calls == [('POST', '/rest/system/shutdown'), 'close', 'quit']
    actions = [action.text() for action in window.tray.contextMenu().actions()]
    assert actions == ['打开同步窗口', '退出（同时停止同步）']
    window.exiting = True
    window.close()


def test_exit_still_works_when_engine_already_stopped(monkeypatch):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])
    window = Window()
    calls = []
    monkeypatch.setattr(ui, 'local_credentials', lambda: ('http://127.0.0.1:8384', 'synthetic'))
    monkeypatch.setattr(ui, 'engine_available', lambda endpoint: False)
    monkeypatch.setattr(window, 'quit_interface', lambda: calls.append('quit'))
    window.exit_with_engine()
    assert calls == ['quit']
    window.exiting = True
    window.close()


def test_progress_bar_shows_upload_still_pending():
    app = QApplication.instance() or QApplication([])
    window = Window()
    class Engine:
        def progress_snapshot(self, folder):
            return {'local': {'inSyncBytes': 100, 'needBytes': 0, 'needFiles': 0,
                              'needTotalItems': 0, 'state': 'idle'},
                    'peers': [{'connected': True, 'remoteState': 'valid',
                               'completion': 60, 'needItems': 2}], 'traffic': {}}
        def close(self):
            pass
    window.api = Engine()
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.display_progress(window.api.progress_snapshot('test'))
    assert window.progress_bar.value() == 600
    assert '60.0%' in window.status.text()
    assert '对端待接收 2 项' in window.progress_detail.text()
    window.exiting = True
    window.close()


def test_progress_refresh_keeps_ui_responsive_and_coalesces_requests(monkeypatch):
    from threading import Event, get_ident
    from PySide6.QtCore import QEventLoop, QTimer
    import syncthing_bridge.activity as activity_module

    app = QApplication.instance() or QApplication([])
    window = Window()
    window.timer.stop()
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.api = SimpleNamespace(close=lambda: None)
    window.engine_credentials = ('http://127.0.0.1:8384', 'test')
    loop = QEventLoop()
    release = Event()
    worker_threads = []
    snapshots = []

    def snapshot(folder):
        worker_threads.append(get_ident())
        if not release.wait(2):
            raise TimeoutError()
        return {'paused': True}

    monkeypatch.setattr(activity_module, 'Syncthing', lambda *args: SimpleNamespace(
        http=SimpleNamespace(), progress_snapshot=snapshot, close=lambda: None))
    monkeypatch.setattr(window, 'display_progress', lambda value: (snapshots.append(value), loop.quit()))
    try:
        window.refresh_status()
        window.refresh_status()
        QTimer.singleShot(0, release.set)
        QTimer.singleShot(2500, loop.quit)
        loop.exec()
        assert snapshots == [{'paused': True}]
        assert len(worker_threads) == 1 and worker_threads[0] != get_ident()
    finally:
        release.set()
        window.exiting = True
        window.close()


def test_enabling_overlays_installs_extension_before_starting_publisher(monkeypatch):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])
    window = Window()
    calls = []

    class Publisher:
        def __init__(self, endpoint, key):
            calls.append(('publisher', endpoint, key))
        def start(self):
            calls.append('start')
        def stop(self, **kwargs):
            calls.append('stop')

    monkeypatch.setattr(ui, 'overlay_extension_installed', lambda: False)
    monkeypatch.setattr(ui, 'install_overlay_extension', lambda: calls.append('install') or True)
    monkeypatch.setattr(ui, 'OverlayPublisher', Publisher)
    monkeypatch.setattr(ui.sys, 'platform', 'win32')
    window.api = SimpleNamespace(close=lambda: None)
    window.key.setText('local-key')
    window.overlay_preferences = SimpleNamespace(setValue=lambda *args: None)
    window.overlay_enabled.setChecked(False)
    window.overlay_enabled.setChecked(True)
    assert calls == ['install', ('publisher', 'http://127.0.0.1:8384', 'local-key'), 'start']
    window.exiting = True
    window.close()


@pytest.mark.parametrize('installed', [False, True])
@pytest.mark.parametrize('browse_fails', [False, True])
def test_connect_engine_restores_overlays_without_installing(monkeypatch, installed, browse_fails):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui, 'QSettings', lambda *args: SimpleNamespace(
        value=lambda key, default=None, **kwargs: key == 'enabled' or default,
        setValue=lambda *args: None))
    window = Window()
    calls = []
    monkeypatch.setattr(ui.sys, 'platform', 'win32')
    monkeypatch.setattr(ui, 'Syncthing', lambda *args: SimpleNamespace(folders=list, close=lambda: None))
    monkeypatch.setattr(ui, 'overlay_extension_installed', lambda: installed)
    monkeypatch.setattr(ui, 'install_overlay_extension', lambda: calls.append('install') or True)
    monkeypatch.setattr(ui, 'OverlayPublisher', lambda *args: SimpleNamespace(
        start=lambda: calls.append('start'), stop=lambda **kwargs: None))
    try:
        if browse_fails:
            def fail():
                raise ui.SyncthingError('目录不可用')
            monkeypatch.setattr(window, 'load_folder', fail)
            with pytest.raises(ui.SyncthingError):
                window.connect_engine()
        else:
            window.connect_engine()
        assert calls == (['start'] if installed else [])
        assert window.overlay_enabled.isChecked()
        assert ('重新勾选' in window.overlay_notice.text()) is not installed
    finally:
        window.exiting = True
        window.close()


def test_selector_restores_saved_nas_connection(monkeypatch):
    import syncthing_bridge.__main__ as ui
    app = QApplication.instance() or QApplication([])

    class Settings:
        def __init__(self):
            self.values = {'endpoint': 'http://nas.invalid:8384'}
        def value(self, key, default=None, type=None):
            return self.values.get(key, default)
        def setValue(self, key, value):
            self.values[key] = value
        def remove(self, key):
            self.values.pop(key, None)

    class Store:
        def load(self, endpoint):
            assert endpoint == 'http://nas.invalid:8384'
            return 'saved-nas-key'

    monkeypatch.setattr(ui, 'QSettings', lambda *args: Settings())
    monkeypatch.setattr(ui, 'NasCredentialStore', Store)
    window = Window()
    assert 'NAS' in window.nas_endpoint.placeholderText()
    assert window.nas_key.echoMode() == QLineEdit.EchoMode.Password
    assert window.nas_endpoint.text() == 'http://nas.invalid:8384'
    assert window.nas_key.text() == 'saved-nas-key'
    window.exiting = True
    window.close()


@pytest.mark.parametrize('nas_identity', ['nas', 'other'])
def test_activity_reader_merges_only_verified_nas_download_rows(monkeypatch, nas_identity):
    import syncthing_bridge.activity as activity_module

    class Engine:
        def __init__(self, endpoint, key):
            self.endpoint = endpoint
            self.http = SimpleNamespace(timeout=None)
        def activity(self, folder, **kwargs):
            return {'current': [], 'queue': [], 'recent': [], 'notices': [], 'tracked': [],
                    'uploads': [], 'page': 1, 'has_more': False}
        def folders(self):
            return [{'id': 'drive', 'devices': [{'deviceID': 'local'}, {'deviceID': 'nas'}]}]
        def request(self, method, route):
            assert route == '/rest/system/status'
            return {'myID': nas_identity if self.endpoint == 'http://nas.invalid:8384' else 'local'}
        def download_activity(self, folder, **kwargs):
            assert nas_identity == 'nas'
            assert self.endpoint == 'http://nas.invalid:8384'
            return {'current': [{'name': 'nas-live.txt', 'status': 'NAS 正在下载'}],
                    'queue': [{'name': 'nas-wait.txt', 'status': 'NAS 等待下载'}], 'has_more': True}
        def close(self):
            pass

    monkeypatch.setattr(activity_module, 'Syncthing', Engine)
    worker = ReadActivity('http://127.0.0.1:8384', 'local', 'drive', [],
                          nas_endpoint='http://nas.invalid:8384', nas_key='nas-key')
    received = []
    worker.signals.ready.connect(lambda result, error: received.append((result, error)))
    worker.run()
    result, error = received[0]
    assert error is None
    if nas_identity != 'nas':
        assert not result['current'] and not result['queue']
        assert result['queue_incomplete']
        return
    assert result['current'][0]['name'] == 'nas-live.txt'
    assert result['queue'][0]['name'] == 'nas-wait.txt'
    assert result['has_more']


def test_tray_single_click_opens_activity_double_click_opens_settings(monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon
    app = QApplication.instance() or QApplication([])
    window = Window()
    calls = []
    monkeypatch.setattr(window, 'show_activity', lambda: calls.append('activity'))
    monkeypatch.setattr(window, 'show_window', lambda: calls.append('settings'))
    window.tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    window.tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert calls == ['activity', 'settings']
    window.exiting = True
    window.close()


def test_activity_panel_preserves_filenames_and_clears_stale_rows_on_error():
    app = QApplication.instance() or QApplication([])
    window = Window()
    panel = window.activity_panel
    panel.display({'current': [{'name': '工作/报告.txt', 'status': '正在下载'}],
                   'recent': [], 'notices': []}, None)
    assert '工作/报告.txt' in panel.lists['current'].topLevelItem(0).text(0)
    panel.display(None, '读取失败')
    assert panel.lists['current'].topLevelItemCount() == 0
    assert panel.lists['notices'].topLevelItem(0).text(0) == '读取失败'
    window.exiting = True
    window.close()


def test_activity_panel_separates_live_transfer_from_queue():
    app = QApplication.instance() or QApplication([])
    window = Window()
    panel = window.activity_panel
    panel.display({'current': [], 'queue': [{'name': '待上传.txt', 'status': '等待上传至对端'}],
                   'recent': [], 'notices': []}, None)
    assert panel.tabs.tabText(0) == '正在传输'
    assert panel.tabs.tabText(1) == '等待队列'
    assert '没有正在下载文件' in panel.lists['current'].topLevelItem(0).text(0)
    assert '待上传.txt' in panel.lists['queue'].topLevelItem(0).text(0)
    window.exiting = True
    window.close()


def test_second_page_error_restores_back_button_and_clears_loading():
    app = QApplication.instance() or QApplication([])
    window = Window()
    panel = window.activity_panel
    panel.page = 2
    panel.previous_page.setEnabled(False)
    panel.page_label.setText('第 2 页（读取中）')
    panel.display(None, '读取失败')
    assert panel.previous_page.isEnabled()
    assert '读取中' not in panel.page_label.text()
    assert '第 2 页' in panel.page_label.text()
    window.exiting = True
    window.close()


def test_activity_response_for_previous_task_is_discarded():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.activity_context = (object(), 'previous')
    window.activity_ready({'current': [{'name': '旧任务.txt', 'status': '正在下载'}]}, None)
    assert window.activity_panel.lists['current'].topLevelItemCount() == 0
    window.exiting = True
    window.close()
