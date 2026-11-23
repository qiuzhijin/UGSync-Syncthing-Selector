from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from syncthing_bridge.__main__ import Window
from syncthing_bridge.activity import ReadActivity


@pytest.fixture(autouse=True)
def isolate_saved_nas_credentials(monkeypatch):
    import syncthing_bridge.__main__ as ui

    monkeypatch.setattr(ui, 'NasCredentialStore', lambda **kwargs: SimpleNamespace(load=lambda endpoint: None))


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
    finally:
        window.exiting = True
        window.close()


def test_catalog_keeps_local_only_directories(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = Window()
    directory = 'FILE_INFO_TYPE_DIRECTORY'
    (tmp_path / 'LocalOnly').mkdir()
    window.catalog = SimpleNamespace(cached=lambda folder, prefix: None)
    window.api = SimpleNamespace(close=lambda: None, folders=lambda: [{'id': 'test', 'path': str(tmp_path)}])
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    callbacks = []
    monkeypatch.setattr(window, 'read_nas_directories', lambda *args, **kwargs:
                        callbacks.append(args[2]) or True)
    try:
        window.populate(None, '', False)
        callbacks.pop()({'': [{'name': 'NasOnly', 'type': directory}]}, None)
        assert {window.tree.topLevelItem(i).text(0) for i in range(window.tree.topLevelItemCount())} == {
            'LocalOnly', 'NasOnly'}
    finally:
        window.exiting = True
        window.close()


def test_slow_local_browse_is_dispatched_off_the_gui_thread(monkeypatch):
    from threading import Event
    from PySide6.QtTest import QTest
    import syncthing_bridge.__main__ as ui

    app = QApplication.instance() or QApplication([])
    window = Window()
    finished = Event()
    released = Event()

    class Engine:
        def browse(self, folder, prefix):
            released.wait(1)
            finished.set()
            return [{'name': 'Local', 'type': 'FILE_INFO_TYPE_DIRECTORY'}]

        def close(self):
            pass

    monkeypatch.setattr(ui, 'Syncthing', lambda *_: Engine())
    window.api = Engine()
    window.engine_credentials = ('http://127.0.0.1:8384', 'test')
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    try:
        window.populate(None, '', False)
        assert not finished.is_set()
        released.set()
        for _ in range(100):
            if window.tree.topLevelItemCount():
                break
            QTest.qWait(10)
        assert window.tree.topLevelItem(0).text(0) == 'Local'
    finally:
        released.set()
        window.exiting = True
        window.close()


def test_catalog_cache_refresh_keeps_directory_selection(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = Window()
    directory = 'FILE_INFO_TYPE_DIRECTORY'
    cached = [{'name': name, 'type': directory} for name in ['Keep', 'Removed']]
    fresh = [{'name': name, 'type': directory} for name in ['Keep', 'Added']]
    callbacks = []
    window.catalog = SimpleNamespace(cached=lambda folder, prefix: cached)
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    monkeypatch.setattr(window, 'read_nas_directories',
                        lambda folder, prefixes, callback, **kwargs: callbacks.append(callback) or True)
    try:
        window.populate(None, '', False)
        keep = window.tree.topLevelItem(0)
        assert keep.text(0) == 'Keep'
        keep.setCheckState(0, Qt.CheckState.Checked)
        callbacks.pop()({'': fresh}, None)
        assert window.tree.topLevelItem(0) is keep
        assert window.choices == {'Keep': True}
        assert window.tree.topLevelItem(1).text(0) == 'Added'

        cached = fresh
        window.populate(None, '', False)
        callbacks.pop()(None, None)
        assert window.tree.topLevelItem(0) is keep
        assert window.tree.topLevelItemCount() == 2
        assert '' in window.cached_directories
    finally:
        window.exiting = True
        window.close()


def test_catalog_automatically_refreshes_visible_tree_and_recovers(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = Window()
    directory = 'FILE_INFO_TYPE_DIRECTORY'
    work = {'name': 'Work', 'type': directory}
    callbacks = []
    window.api = SimpleNamespace(close=lambda: None)
    window.catalog = SimpleNamespace(cached=lambda folder, prefix: None)
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.populate_rows(None, '', [work])
    parent = window.tree.topLevelItem(0)
    parent.setData(0, Qt.ItemDataRole.UserRole + 1, True)
    parent.setExpanded(True)
    parent.setCheckState(0, Qt.CheckState.Checked)
    monkeypatch.setattr(window, 'read_nas_directories', lambda folder, prefixes, callback, **kwargs:
                        callbacks.append((prefixes, callback)) or True)

    try:
        window.catalog_refresh_due = 0
        window.poll_catalog(100)
        assert not callbacks
        window.show()
        window.poll_catalog(100)
        prefixes, complete = callbacks.pop()
        assert prefixes == ('',)
        window.poll_catalog(105)
        assert not callbacks
        complete({'': [work]}, None)

        window.poll_catalog(110)
        prefixes, complete = callbacks.pop()
        assert prefixes == ('Work',)
        complete({'Work': [{'name': 'Added', 'type': directory}]}, None)
        assert parent.child(0).text(0) == 'Added'
        assert window.choices == {'Work': True}
        window.poll_catalog(115)
        assert not callbacks

        for now in (125, 135, 145, 155):
            window.poll_catalog(now)
            assert not callbacks
        window.poll_catalog(160)
        prefixes, complete = callbacks.pop()
        assert prefixes == ('',)
        complete(None, None)
        assert window.tree.topLevelItem(0) is parent
        assert '' in window.cached_directories
        window.main_tabs.setCurrentIndex(1)
        window.poll_catalog(165)
        assert not callbacks
        window.main_tabs.setCurrentIndex(0)
        prefixes, complete = callbacks.pop()
        assert prefixes == ('',)
        complete({'': [work]}, None)
        assert '' not in window.cached_directories
    finally:
        window.exiting = True
        window.close()


def test_catalog_settings_only_persist_after_a_successful_connection(monkeypatch):
    import syncthing_bridge.__main__ as ui
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    window = Window()
    preferences, saved, attempts = {}, {}, []
    window.catalog_preferences = SimpleNamespace(setValue=preferences.__setitem__)
    window.catalog_credentials = SimpleNamespace(save=saved.__setitem__, delete=saved.pop)
    window.folders.blockSignals(True)
    window.folders.addItem('测试', 'test')
    window.folders.blockSignals(False)
    window.catalog_endpoint.setText('https://catalog.invalid')
    window.catalog_token.setText('t' * 32)

    def listings(catalog, folder, prefixes, *, directories_only):
        attempts.append((folder, prefixes, directories_only))
        if len(attempts) == 1:
            raise ui.SyncthingError('test failure')
        return {'': []}

    monkeypatch.setattr(ui.Catalog, 'listings', listings)
    try:
        assert window.catalog_token.echoMode() == QLineEdit.EchoMode.Password
        for _ in range(2):
            window.catalog_apply.click()
            assert not window.catalog_apply.isEnabled()
            for _ in range(100):
                if window.catalog_apply.isEnabled():
                    break
                QTest.qWait(10)
            assert window.catalog_apply.isEnabled()
            if len(attempts) == 1:
                assert not preferences and not saved
                assert '失败' in window.catalog_notice.text()
        assert attempts == [('test', ('',), True)] * 2
        assert preferences == {'endpoint': 'https://catalog.invalid'}
        assert saved == {'https://catalog.invalid': 't' * 32}
        window.set_catalog(None)
        assert not saved and preferences == {'endpoint': ''}
        assert window.catalog is None
    finally:
        window.exiting = True
        window.close()


def test_directory_read_result_is_discarded_after_switching_tasks(monkeypatch):
    from threading import Event
    from PySide6.QtTest import QTest
    import syncthing_bridge.__main__ as ui

    app = QApplication.instance() or QApplication([])
    window = Window()
    released = Event()

    class Engine:
        def browse(self, folder, prefix):
            if folder == 'old':
                released.wait(2)
            return [{'name': folder, 'type': 'FILE_INFO_TYPE_DIRECTORY'}]

        def folders(self):
            return [{'id': 'old'}, {'id': 'new'}]

        def ignores(self, folder):
            return []

        def close(self):
            pass

    monkeypatch.setattr(ui, 'Syncthing', lambda *_: Engine())
    monkeypatch.setattr(window, 'refresh_status', lambda: None)
    window.api = Engine()
    window.engine_credentials = ('http://127.0.0.1:8384', 'test')
    window.folders.blockSignals(True)
    window.folders.addItem('旧任务', 'old')
    window.folders.addItem('新任务', 'new')
    window.folders.blockSignals(False)
    try:
        window.populate(None, '', False)
        window.folders.setCurrentIndex(1)
        for _ in range(100):
            if window.tree.topLevelItemCount():
                break
            QTest.qWait(10)
        assert window.tree.topLevelItem(0).text(0) == 'new'
        released.set()
        for _ in range(100):
            if not window.selection_jobs:
                break
            QTest.qWait(10)
        assert not window.selection_jobs
        assert window.tree.topLevelItem(0).text(0) == 'new'
    finally:
        released.set()
        window.exiting = True
        window.close()


@pytest.mark.parametrize('local_available', [True, False])
def test_preview_nas_timeout_uses_local_index_and_preserves_saved_files(monkeypatch, tmp_path, local_available):
    import syncthing_bridge.__main__ as ui
    from PySide6.QtWidgets import QLabel
    from syncthing_bridge.selection import compile_document

    app = QApplication.instance() or QApplication([])
    window = Window()
    callbacks = []
    notices = []
    monkeypatch.setattr(ui.QMessageBox, 'warning', lambda *args: notices.append(args[-1]))
    monkeypatch.setattr(window, 'read_nas_directories',
                        lambda folder, parents, callback, **kwargs: callbacks.append(callback) or True)
    saved = []
    previews = []

    def accept(dialog):
        previews.append([label.text() for label in dialog.findChildren(QLabel)])
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, 'exec', accept)
    def browse(folder, prefix, **kwargs):
        if not local_available:
            raise ui.SyncthingTimeout('timeout')
        return [{'name': 'indexed.txt', 'type': 'FILE_INFO_TYPE_FILE'}] if prefix == 'Work' else []

    window.api = SimpleNamespace(
        close=lambda: None, folders=lambda: [{'id': 'test', 'path': str(tmp_path)}], browse=browse,
        save_selection=lambda folder, rules, previous: saved.extend(rules))
    window.nas_endpoint.setText('https://nas.invalid')
    window.nas_key.setText('test-key')
    window.escape = '|'
    window.previous = compile_document([], ['Work/Keep', 'Old/Keep'], direct_files=[
        'Work/saved[1].txt', 'Old/obsolete.txt'])
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
        assert window.preview_button.isEnabled()
        assert window.choices == {'Work/Keep': True}
        if not local_available:
            assert not previews and not saved
            assert '本机目录也无法读取' in notices[-1]
            return
        assert not notices
        assert len(previews) == 1 and any('未能核对 NAS 清单' in label for label in previews[0])
        assert '!/Work/indexed.txt' in saved
        assert '!/Work/saved|[1|].txt' in saved
        assert '!/Old/obsolete.txt' not in saved

        window.preview()
        window.nas_directory_jobs[worker] = (
            window.progress_display, window.nas_directory_credentials(), callbacks.pop())
        window.progress_display = ui.ProgressDisplay()
        window.nas_directories_ready(worker, ({'Work': []}, None))
        assert len(previews) == 1
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
    scheduled, workers, launches = [], [], []
    monkeypatch.setattr(ui, 'QThreadPool', SimpleNamespace(
        globalInstance=lambda: SimpleNamespace(start=workers.append)))
    monkeypatch.setattr(ui.QTimer, 'singleShot', lambda delay, callback: scheduled.append((delay, callback)))
    monkeypatch.setattr(ui.QMessageBox, 'warning', lambda *args: pytest.fail('Unexpected startup dialog'))
    monkeypatch.setattr(ui, 'launch_engine', lambda: launches.append(True) or SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(ui, 'Syncthing', lambda *args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(window, 'load_folder', lambda: pytest.fail('Hidden startup should not browse directories'))
    monkeypatch.setattr(window, 'change_overlays', lambda *args: None)
    monkeypatch.setattr(window, 'refresh_status', lambda: None)
    try:
        window.start_local_on_login()
        assert '准备' in window.tray.toolTip()
        for _ in range(4):
            workers.pop().signals.ready.emit(None, SyncthingTimeout('读取同步引擎超时'))
            assert window.startup_connection and '重试' in window.tray.toolTip()
            assert not launches and len(scheduled) == 1
            scheduled.pop()[1]()
        for _ in range(4):
            workers.pop().signals.ready.emit(None, None)
            scheduled.pop()[1]()
        assert launches == [True]
        workers.pop().signals.ready.emit(
            (('http://127.0.0.1:8384', 'test'), [{'id': 'drive', 'label': 'Drive'}]), None)
        assert not scheduled and not window.startup_connection
        assert window.folders.currentData() == 'drive' and window.selection_needs_load
        loads = []
        monkeypatch.setattr(window, 'load_folder', lambda: loads.append(True))
        window.show_window()
        assert loads == [True]
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
        contains=lambda key: False,
        setValue=lambda *args: None))
    window = Window()
    calls = []
    monkeypatch.setattr(ui.sys, 'platform', 'win32')
    monkeypatch.setattr(ui, 'Syncthing', lambda *args: SimpleNamespace(folders=list, close=lambda: None))
    monkeypatch.setattr(window, 'run_selection_read', lambda read, callback, *_: callback(read()))
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
        def contains(self, key):
            return False
        def setValue(self, key, value):
            self.values[key] = value
        def remove(self, key):
            self.values.pop(key, None)

    class Store:
        def __init__(self, **kwargs):
            pass
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


def test_activity_reader_aggregates_tasks_without_merging_same_name(monkeypatch):
    import syncthing_bridge.activity as activity_module
    from syncthing_bridge.api import Syncthing, SyncthingError

    class Engine(Syncthing):
        def __init__(self, endpoint, key):
            self.http = SimpleNamespace(timeout=None)

        def close(self):
            pass

        def folders(self):
            return [{'id': 'first', 'label': '工作'}, {'id': 'second', 'label': '备份'},
                    {'id': 'unavailable', 'label': '离线任务'}]

        def request(self, method, route, **kwargs):
            if route == '/rest/events':
                return [{'type': 'ItemFinished', 'time': '2026-11-23T12:00:00Z',
                         'data': {'folder': task, 'item': '报告.txt', 'type': 'file'}}
                        for task in ('first', 'second')]
            if route == '/rest/system/status':
                return {'myID': 'local'}
            if route == '/rest/system/connections':
                return {'total': {'inBytesTotal': 10, 'outBytesTotal': 4}}
            task = kwargs['params']['folder']
            if route == '/rest/db/need':
                return {'progress': [{'name': '报告.txt'}]} if task == 'first' else {}
            if route == '/rest/db/status':
                if task == 'unavailable':
                    raise SyncthingError('读取失败')
                return {'state': 'syncing' if task == 'first' else 'idle', 'needTotalItems': int(task == 'first')}
            raise AssertionError(route)

    monkeypatch.setattr(activity_module, 'Syncthing', Engine)
    worker = ReadActivity('http://127.0.0.1:8384', 'local', {}, {})
    received = []
    worker.signals.ready.connect(lambda result, error: received.append((result, error)))
    worker.run()
    result, error = received[0]
    assert error is None
    assert result['title'] == '正在同步'
    assert [(row['task'], row['name'], row['status']) for row in result['rows'][:2]] == [
        ('工作', '报告.txt', '正在下载到本机'), ('备份', '报告.txt', '已接收完成')]
    assert '1 个任务状态暂不可用' in result['detail']


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


def test_activity_panel_shows_one_list_and_clears_stale_rows_on_error():
    app = QApplication.instance() or QApplication([])
    window = Window()
    panel = window.activity_panel
    panel.display({'title': '正在同步', 'detail': '2 个任务',
                   'rows': [{'name': '报告.txt', 'task': '工作', 'status': '正在下载'},
                            {'name': '备份.zip', 'task': '备份', 'status': '等待下载'}]}, None)
    assert panel.heading.text() == '正在同步'
    assert panel.files.topLevelItemCount() == 2
    assert [panel.files.topLevelItem(0).text(i) for i in range(3)] == ['报告.txt', '工作', '正在下载']
    panel.display(None, '读取失败')
    assert panel.files.topLevelItemCount() == 0
    assert panel.detail.text() == '读取失败'
    window.exiting = True
    window.close()


def test_activity_response_for_previous_connection_is_discarded():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.activity_context = object()
    window.activity_ready({'rows': [{'name': '旧文件.txt', 'status': '正在下载'}]}, None)
    assert window.activity_panel.files.topLevelItemCount() == 0
    window.exiting = True
    window.close()
