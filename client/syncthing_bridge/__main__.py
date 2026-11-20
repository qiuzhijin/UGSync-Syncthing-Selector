"""Run with: python -m syncthing_bridge."""
import os
import sys
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThreadPool, Slot, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QSystemTrayIcon, QMenu, QStyle,
    QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QProgressBar,
    QMessageBox, QPushButton, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from syncthing_bridge.api import Syncthing, SyncthingError, SyncthingTimeout, local_credentials
from syncthing_bridge.appearance import apply_appearance
from syncthing_bridge.selection import normalize, parse_document, compile_document, direct_file_parents
from syncthing_bridge.desktop import startup_enabled, set_startup, engine_available, launch_engine
from syncthing_bridge.diagnostics import application_log, report_exception
from syncthing_bridge.instance import SingleInstance
from syncthing_bridge.progress import ProgressDisplay
from syncthing_bridge.activity import ActivityPanel, ReadActivity
from syncthing_bridge.overlays import OverlayPublisher
from syncthing_bridge.nas_credentials import NasCredentialError, NasCredentialStore
from syncthing_bridge.overlay_install import OverlayInstallError, install_overlay_extension, overlay_extension_installed
from syncthing_bridge.winsparkle import configure_updates


class Window(QMainWindow):
    def __init__(self, *, log_path=None):
        super().__init__()
        self.api = None
        self.previous = []
        self.choices = {}
        self.base_rules = []
        self.escape = '\\'
        self.updating = False
        self.exiting = False
        self.starting = False
        self.startup_connection = False
        self.launch_checks = 0
        self.progress_display = ProgressDisplay()
        self.activity_worker = None
        self.activity_context = None
        self.activity_tracked = []
        self.activity_uploads = []
        self.activity_panel = ActivityPanel(self)
        self.overlay_publisher = None
        self.overlay_preferences = QSettings('UGSync', 'ExplorerOverlays')
        self.nas_preferences = QSettings('UGSync', 'SyncthingNasActivity')
        self.nas_credentials = NasCredentialStore()
        self.setWindowTitle('文件夹同步 · Syncthing')
        self.resize(900, 720)
        body = QWidget()
        body.setObjectName('selectorSurface')
        apply_appearance(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(9)
        title = QLabel('文件夹同步')
        title.setProperty('role', 'title')
        layout.addWidget(title)
        connection = QFrame()
        connection.setProperty('card', True)
        connection_layout = QVBoxLayout(connection)
        connection_layout.setContentsMargins(14, 10, 14, 10)
        connection_title = QLabel('连接设置')
        connection_title.setProperty('role', 'section')
        connection_layout.addWidget(connection_title)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.endpoint = QLineEdit('http://127.0.0.1:8384')
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.nas_endpoint = QLineEdit()
        self.nas_endpoint.setPlaceholderText('NAS Syncthing 地址（可选，例如 http://nas:8384）')
        self.nas_key = QLineEdit()
        self.nas_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('本机 Syncthing 地址', self.endpoint)
        form.addRow('API 密钥（仅本次使用）', self.key)
        form.addRow('NAS Syncthing 地址（可选）', self.nas_endpoint)
        form.addRow('NAS API 密钥（保存在本机配置）', self.nas_key)
        connection_layout.addLayout(form)
        self.restore_nas_connection()
        self.nas_endpoint.editingFinished.connect(self.change_nas_endpoint)
        self.nas_key.editingFinished.connect(self.save_nas_key)
        connect = QPushButton('连接并读取同步任务')
        connect.clicked.connect(lambda: self.run(self.connect_engine))
        local = QPushButton('连接本机（自动读取配置）')
        local.clicked.connect(lambda: self.run(self.connect_local))
        connection_actions = QHBoxLayout()
        connection_actions.addWidget(local)
        connection_actions.addWidget(connect)
        connection_actions.addStretch()
        connection_layout.addLayout(connection_actions)
        layout.addWidget(connection)
        self.autostart = QCheckBox('登录 Windows 后自动启动并收起到右下角托盘')
        try:
            self.autostart.setChecked(startup_enabled())
        except (OSError, ImportError):
            self.autostart.setEnabled(False)
        self.autostart.toggled.connect(self.change_startup)
        layout.addWidget(self.autostart)
        self.overlay_enabled = QCheckBox('资源管理器同步角标（首次启用会请求管理员权限安装）')
        self.overlay_enabled.setChecked(self.overlay_preferences.value('enabled', False, type=bool))
        self.overlay_enabled.toggled.connect(self.change_overlays)
        layout.addWidget(self.overlay_enabled)
        self.folders = QComboBox()
        self.folders.currentIndexChanged.connect(lambda: self.run(self.load_folder))
        task_row = QHBoxLayout()
        task_label = QLabel('同步任务')
        task_label.setProperty('role', 'section')
        task_label.setBuddy(self.folders)
        task_row.addWidget(task_label)
        task_row.addWidget(self.folders, 1)
        layout.addLayout(task_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat('等待连接')
        layout.addWidget(self.progress_bar)
        self.progress_detail = QLabel('同步进度将在连接后显示')
        self.progress_detail.setProperty('role', 'muted')
        self.progress_detail.setWordWrap(True)
        self.progress_detail.setToolTip('完成度按引擎索引统计，大文件可能在处理完成后更新。速度为本机引擎总传输量，包含协议开销。')
        layout.addWidget(self.progress_detail)
        self.all = QCheckBox('同步整个根目录（包含以后新增的目录）')
        self.all.toggled.connect(self.select_all)
        layout.addWidget(self.all)
        self.tree = QTreeWidget()
        self.tree.setStyleSheet('QTreeWidget::indicator:indeterminate { background: #1689ed; '
                               'border: 1px solid #1689ed; border-radius: 2px; image: none; }')
        self.tree.setHeaderLabels(['目录选择（展开后加载，仍受现有排除规则约束）'])
        self.tree.setToolTip('取消勾选后保留现有本地文件。')
        self.tree.itemExpanded.connect(lambda item: self.run(lambda: self.expand(item), 'expand_directory'))
        self.tree.itemChanged.connect(self.changed)
        layout.addWidget(self.tree, 1)
        row = QHBoxLayout()
        preview = QPushButton('预览并保存选择')
        preview.setProperty('primary', True)
        preview.clicked.connect(lambda: self.run(self.preview))
        row.addWidget(preview)
        pause = QPushButton('暂停')
        pause.clicked.connect(lambda: self.run(lambda: self.pause_current(True), 'pause_sync'))
        resume = QPushButton('继续同步')
        resume.clicked.connect(lambda: self.run(lambda: self.pause_current(False), 'resume_sync'))
        refresh = QPushButton('刷新状态')
        refresh.clicked.connect(lambda: self.run(self.refresh_status))
        for button in (pause, resume, refresh):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel('请先启动本机 Syncthing，并在其设置中取得 API 密钥。')
        self.status.setProperty('role', 'muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setCentralWidget(body)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_status)
        self.timer.start(5000)
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.tray.setToolTip('文件夹同步 · Syncthing')
        menu = QMenu(self)
        menu.addAction('打开同步窗口', self.show_window)
        if log_path is not None:
            menu.addAction('打开日志文件夹', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path.parent))))
        try:
            self.software_updates = configure_updates(self, menu)
        except (OSError, ValueError, AttributeError):
            if '--startup-check' in sys.argv:
                raise
            self.status.setText('软件更新组件暂时不可用。')
        menu.addAction('退出（同时停止同步）', lambda: self.run(self.exit_with_engine))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_activity()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.activity_panel.hide()
            self.show_window()

    def restore_nas_connection(self):
        endpoint = self.nas_preferences.value('endpoint', '', type=str).strip()
        if not endpoint:
            return
        try:
            key = self.nas_credentials.load(endpoint)
        except NasCredentialError:
            return
        self.nas_endpoint.setText(endpoint)
        self.nas_key.setText(key or '')

    def change_nas_endpoint(self):
        endpoint = self.nas_endpoint.text().strip()
        if not endpoint:
            self.nas_preferences.remove('endpoint')
            self.nas_key.clear()
            return
        try:
            key = self.nas_credentials.load(endpoint)
        except NasCredentialError as error:
            self.status.setText(str(error))
            return
        self.nas_preferences.setValue('endpoint', endpoint)
        self.nas_key.setText(key or '')

    def save_nas_key(self):
        endpoint = self.nas_endpoint.text().strip()
        if not endpoint:
            self.status.setText('请先填写 NAS Syncthing 地址，再保存 API 密钥。')
            return
        try:
            self.nas_credentials.save(endpoint, self.nas_key.text().strip())
        except NasCredentialError as error:
            self.status.setText(str(error))
            return
        self.nas_preferences.setValue('endpoint', endpoint)

    def show_activity(self):
        self.activity_panel.show_at(self.tray)
        self.refresh_activity()

    def refresh_activity(self):
        folder = self.folders.currentData()
        if not self.api or not folder:
            self.activity_panel.display(None, '尚未连接同步任务，请打开设置连接本机。')
            return
        if self.activity_worker is not None:
            return
        if self.activity_context != (self.api, folder):
            self.activity_tracked = []
            self.activity_uploads = []
            self.activity_panel.page = 1
        worker = ReadActivity(self.endpoint.text().strip(), self.key.text().strip(), folder,
                              list(self.activity_tracked), self.activity_panel.page,
                              self.nas_endpoint.text(), self.nas_key.text())
        self.activity_worker = worker
        self.activity_context = (self.api, folder)
        self.activity_page = self.activity_panel.page
        worker.signals.ready.connect(self.activity_ready)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def activity_ready(self, result, error):
        api, folder = self.activity_context
        self.activity_worker = None
        # Discard responses from a previous task or connection.
        if (not self.exiting and self.api is api and self.folders.currentData() == folder and
                self.activity_panel.page == getattr(self, 'activity_page', 1)):
            if result is not None:
                self.activity_tracked = result.get('tracked', [])
                self.activity_uploads = (result.get('uploads', []) + self.activity_uploads)[:100]
                result['recent'] = sorted(result['recent'] + self.activity_uploads,
                                          key=lambda row: row.get('time', ''), reverse=True)[:100]
            self.activity_panel.display(result, error)

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def change_overlays(self, enabled):
        if self.overlay_publisher:
            self.overlay_publisher.stop()
            self.overlay_publisher = None
        if not enabled:
            self.overlay_preferences.setValue('enabled', False)
            return
        if not self.api or sys.platform != 'win32':
            self.overlay_preferences.setValue('enabled', True)
            return
        from urllib.parse import urlsplit
        if urlsplit(self.endpoint.text()).hostname not in ('127.0.0.1', 'localhost', '::1'):
            self.disable_overlays('资源管理器角标仅支持本机 Syncthing。')
            return
        try:
            if not overlay_extension_installed() and not install_overlay_extension():
                raise OverlayInstallError('角标扩展未安装。')
        except OverlayInstallError as error:
            report_exception('install_explorer_overlay', error)
            self.disable_overlays(str(error))
            return
        self.overlay_preferences.setValue('enabled', True)
        self.overlay_publisher = OverlayPublisher(self.endpoint.text().strip(), self.key.text().strip())
        self.overlay_publisher.start()
        self.status.setText('资源管理器同步角标已启用；若仍未显示，请注销 Windows 后重新登录。')

    def disable_overlays(self, message):
        self.overlay_preferences.setValue('enabled', False)
        self.overlay_enabled.blockSignals(True)
        self.overlay_enabled.setChecked(False)
        self.overlay_enabled.blockSignals(False)
        self.status.setText(message)

    def quit_interface(self):
        self.exiting = True
        self.close()
        QApplication.instance().quit()

    def exit_with_engine(self):
        endpoint, key = local_credentials()
        if not engine_available(endpoint):
            if self.starting:
                raise RuntimeError('引擎正在启动，请稍后再退出')
            self.quit_interface()
            return
        api = Syncthing(endpoint, key)
        try:
            api.request('POST', '/rest/system/shutdown')
        finally:
            api.close()
        self.quit_interface()

    def change_startup(self, enabled):
        try:
            set_startup(enabled)
            self.status.setText('已启用当前用户自启动。请将程序保留在当前位置。' if enabled else '已取消当前用户自启动。')
        except OSError:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not enabled)
            self.autostart.blockSignals(False)
            QMessageBox.warning(self, '设置未保存', '无法修改当前用户自启动设置')

    def connect_local(self):
        if self.starting:
            return
        try:
            endpoint, _ = local_credentials()
        except Exception:
            endpoint = 'http://127.0.0.1:8384'
        if not engine_available(endpoint):
            self.engine_process = launch_engine()
            self.starting = True
            self.launch_checks = 0
            self.status.setText('正在启动同步引擎…')
            QTimer.singleShot(500, self.wait_for_engine)
            return
        self.finish_local_connection()

    def wait_for_engine(self):
        if self.exiting:
            return
        self.launch_checks += 1
        try:
            endpoint, _ = local_credentials()
            ready = engine_available(endpoint)
        except Exception:
            ready = False
        if ready:
            self.starting = False
            if self.startup_connection:
                self.try_startup_connection(self.finish_local_connection)
            else:
                self.run(self.finish_local_connection)
        elif self.launch_checks < 30 and self.engine_process.poll() is None:
            QTimer.singleShot(500, self.wait_for_engine)
        else:
            self.starting = False
            self.startup_connection = False
            self.status.setText('同步引擎未能启动，请检查配置或端口占用。')

    def start_local_on_login(self):
        self.startup_connection = True
        self.try_startup_connection(self.connect_local)

    def try_startup_connection(self, action, retries=2):
        if self.exiting or not self.startup_connection:
            return
        try:
            action()
        except Exception as error:
            report_exception('login_startup', error)
            if isinstance(error, SyncthingTimeout) and retries:
                self.status.setText('同步引擎响应超时，稍后自动重试…')
                QTimer.singleShot(5000, lambda: self.try_startup_connection(action, retries - 1))
                return
            self.status.setText(f'{error}。可点击“连接本机”重试。')
        if not self.starting:
            self.startup_connection = False

    def finish_local_connection(self):
        endpoint, key = local_credentials()
        self.endpoint.setText(endpoint)
        self.key.setText(key)
        self.connect_engine()

    def pause_current(self, paused):
        folder = self.folders.currentData()
        if not self.api or not folder:
            raise ValueError('请先连接并选择任务')
        self.api.set_paused(folder, paused)
        self.load_folder()

    def refresh_status(self):
        folder = self.folders.currentData()
        if not self.api or not folder:
            return
        percent, title, detail = self.progress_display.update(self.api.progress_snapshot(folder))
        self.progress_bar.setRange(0, 1000 if percent is not None else 0)
        if percent is not None:
            self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(title)
        self.progress_detail.setText(detail)
        self.status.setText(title)
        self.tray.setToolTip('文件夹同步 · ' + title)
        self.activity_panel.overview(percent, title, detail, self.folders.currentText())

    def poll_status(self):
        try:
            self.refresh_status()
        except Exception:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat('状态暂时不可用')
            self.progress_detail.setText('暂时无法读取同步进度')
            self.status.setText('暂时无法读取状态，请检查引擎是否运行。')
            self.activity_panel.overview(0, '状态暂时不可用', '请检查同步引擎连接', self.folders.currentText())
        if self.activity_panel.isVisible():
            self.refresh_activity()

    def run(self, action, operation=None):
        try:
            action()
        except Exception as error:
            report_exception(operation or getattr(action, '__name__', 'ui_action'), error)
            QMessageBox.warning(self, '操作未完成', str(error))

    def connect_engine(self):
        api = Syncthing(self.endpoint.text().strip(), self.key.text().strip())
        try:
            rows = api.folders()
        except Exception:
            api.close()
            raise
        if self.api:
            self.api.close()
        self.api = api
        self.folders.blockSignals(True)
        self.folders.clear()
        for row in rows:
            self.folders.addItem(row.get('label') or row['id'], row['id'])
        self.folders.blockSignals(False)
        self.load_folder()
        if self.overlay_enabled.isChecked():
            self.change_overlays(True)

    def load_folder(self):
        self.tree.clear()
        self.progress_display = ProgressDisplay()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat('正在读取进度…')
        folder = self.folders.currentData()
        if not self.api or not folder:
            return
        self.previous = self.api.ignores(folder)
        self.base_rules, entire, self.choices, self.escape = parse_document(self.previous)
        self.all.blockSignals(True)
        self.all.setChecked(entire)
        self.all.blockSignals(False)
        current = next(row for row in self.api.folders() if row['id'] == folder)
        if current.get('paused'):
            self.status.setText('此任务已暂停，Syncthing 暂不提供目录树。恢复任务后重新连接可编辑已有范围；保存选择时会自动暂停。')
            return
        self.populate(None, '', entire)
        self.status.setText('已恢复目录选择，现有排除规则会保留。保存后可点击“继续同步”。')
        self.refresh_status()

    def selection_rows(self, folder, prefix, *, directories_only=True):
        local_error = None
        try:
            local_rows = self.api.browse(folder, prefix) if directories_only else self.api.browse(
                folder, prefix, directories_only=False)
        except SyncthingError as error:
            local_error = error
            local_rows = []

        nas_rows = []
        endpoint, key = self.nas_endpoint.text().strip(), self.nas_key.text().strip()
        if endpoint and key:
            try:
                task = next((row for row in self.api.folders() if row['id'] == folder), None)
                peers = {device['deviceID'] for device in (task or {}).get('devices', [])}
                if peers:
                    with closing(Syncthing(endpoint, key)) as nas:
                        nas.http.timeout = 2.0
                        identity = nas.request('GET', '/rest/system/status').get('myID')
                        if identity in peers:
                            nas_rows = nas.browse(folder, prefix, directories_only=directories_only)
                            local_error = None
            except (SyncthingError, ValueError) as error:
                report_exception('browse_nas_selection', error)
        if local_error is not None:
            raise local_error

        rows = {(row['name'].casefold(), row.get('type')): row for row in nas_rows}
        rows.update({(row['name'].casefold(), row.get('type')): row for row in local_rows})
        return [rows[key] for key in sorted(rows, key=lambda item: item[0])]

    def populate(self, parent, prefix, checked):
        rows = self.selection_rows(self.folders.currentData(), prefix)
        self.updating = True
        try:
            for row in rows:
                if row.get('type') != 'FILE_INFO_TYPE_DIRECTORY':
                    continue
                name = row.get('name')
                if not isinstance(name, str) or '/' in name:
                    raise ValueError('引擎返回了不支持的目录名称')
                path = normalize(prefix + '/' + name if prefix else name)
                item = QTreeWidgetItem([name])
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, self.selection_state(path))
                for rule in self.base_rules:
                    if rule and not rule.startswith(('//', '#escape=')) and not any(c in rule for c in '*?[]{}()\\'):
                        blocked = rule.strip('/')
                        matches = (path.casefold() == blocked.casefold() or path.casefold().startswith(blocked.casefold() + '/')) if rule.startswith('/') else blocked.casefold() in path.casefold().split('/')
                        if matches:
                            item.setCheckState(0, Qt.CheckState.Unchecked)
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                            item.setToolTip(0, '此目录由现有排除规则排除')
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
        finally:
            self.updating = False

    def expand(self, item):
        if item.data(0, Qt.ItemDataRole.UserRole + 1):
            return
        self.populate(item, item.data(0, Qt.ItemDataRole.UserRole),
                      item.checkState(0) == Qt.CheckState.Checked)
        self.updating = True
        try:
            item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
        finally:
            self.updating = False

    def selection_state(self, path):
        selected = self.all.isChecked()
        for choice in sorted(self.choices, key=len):
            if path == choice or path.startswith(choice + '/'):
                selected = self.choices[choice]
        # Use saved rules rather than loaded children: unopened siblings and
        # future children must not silently become selected.
        if any(value != selected and name.startswith(path + '/')
               for name, value in self.choices.items()):
            return Qt.CheckState.PartiallyChecked
        return Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked

    def refresh_checks(self):
        def visit(item):
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(0, self.selection_state(item.data(0, Qt.ItemDataRole.UserRole)))
            for index in range(item.childCount()):
                visit(item.child(index))
        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def set_children(self, item, state):
        if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        item.setCheckState(0, state)
        for index in range(item.childCount()):
            self.set_children(item.child(index), state)

    def changed(self, item, column):
        if self.updating:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        self.choices = {p: selected for p, selected in self.choices.items() if not p.startswith(path + '/')}
        self.choices[path] = item.checkState(0) == Qt.CheckState.Checked
        self.updating = True
        try:
            self.refresh_checks()
        finally:
            self.updating = False

    def select_all(self, checked):
        self.choices = {}
        self.updating = True
        try:
            for index in range(self.tree.topLevelItemCount()):
                self.set_children(self.tree.topLevelItem(index),
                                  Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self.updating = False

    def preview(self):
        if not self.api or not self.folders.currentData():
            raise ValueError('请先连接并选择同步任务')
        if not self.tree.topLevelItemCount():
            raise ValueError('请先恢复任务并加载目录树，再编辑选择')
        included = [p for p, selected in self.choices.items() if selected]
        excluded = [p for p, selected in self.choices.items() if not selected]
        folder = self.folders.currentData()
        parents = direct_file_parents(included, excluded, self.all.isChecked())
        direct_files = []
        if parents:
            config = next(row for row in self.api.folders() if row['id'] == folder)
            root = Path(os.path.expandvars(config['path'])).expanduser()
            for parent in parents:
                local = root / parent
                rows = self.selection_rows(folder, parent, directories_only=False)
                directory_names = {row['name'].casefold() for row in rows
                                   if row.get('type') == 'FILE_INFO_TYPE_DIRECTORY'}
                names = {row['name'] for row in rows if row.get('type') == 'FILE_INFO_TYPE_FILE'}
                if local.is_dir() and not local.is_symlink():
                    for entry in local.iterdir():
                        if entry.is_dir():
                            directory_names.add(entry.name.casefold())
                        elif entry.is_file() and not entry.is_symlink():
                            names.add(entry.name)
                direct_files.extend('/'.join(filter(None, (parent, name))) for name in names
                                    if name.casefold() not in directory_names
                                    and name.casefold() not in {'.stignore', '.stfolder', '.stversions'}
                                    and not name.casefold().startswith(('.syncthing.', '~syncthing~')))
        rules = compile_document(self.base_rules, included, excluded,
                                 entire_root=self.all.isChecked(), escape=self.escape,
                                 direct_files=direct_files)
        dialog = QDialog(self)
        dialog.setWindowTitle('确认选择范围')
        dialog.resize(700, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('保存时自动暂停此任务，再替换本机忽略规则。保存后保持暂停。\n'
                               '父目录新增直属文件后，请重新保存选择以纳入同步。'))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText('当前规则：\n' + '\n'.join(self.previous) + '\n\n新规则：\n' + '\n'.join(rules))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.api.save_selection(self.folders.currentData(), rules, self.previous)
            self.previous = rules
            self.status.setText('选择已保存，任务已暂停。点击“继续同步”后生效。')

    def closeEvent(self, event):
        if not self.exiting and self.tray.isVisible():
            self.hide()
            event.ignore()
            return
        self.timer.stop()
        if self.overlay_publisher:
            self.overlay_publisher.stop(stopped=True)
            self.overlay_publisher = None
        self.activity_panel.hide()
        self.tray.hide()
        if self.api:
            self.api.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    guard = None
    active_window = []
    if '--startup-check' not in sys.argv:
        guard = SingleInstance(lambda: active_window[0].show_window() if active_window else None)
        try:
            if not guard.acquire():
                return 0
        except RuntimeError as error:
            QMessageBox.information(None, '文件夹同步', str(error))
            return 1
        app.aboutToQuit.connect(guard.release)
    with application_log() as log_path:
        try:
            window = Window(log_path=log_path)
            active_window.append(window)
            window.show()
            if '--startup-check' in sys.argv:
                QTimer.singleShot(200, app.quit)
            elif '--autostart' in sys.argv:
                if window.tray.isVisible():
                    window.hide()
                QTimer.singleShot(0, window.start_local_on_login)
            else:
                QTimer.singleShot(0, lambda: window.run(window.connect_local))
            return app.exec()
        except Exception as error:
            report_exception('application', error)
            raise


if __name__ == '__main__':
    sys.exit(main())
