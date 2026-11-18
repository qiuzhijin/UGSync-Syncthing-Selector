import time
import uuid
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtWidgets import QApplication
from syncthing_bridge.instance import SingleInstance


def test_repeated_launch_activates_first_and_never_owns_its_lock(tmp_path):
    app = QApplication.instance() or QApplication([])
    calls = []
    name = 'ugsync-instance-test-' + uuid.uuid4().hex
    first = SingleInstance(lambda: calls.append('show'), directory=tmp_path, name=name)
    second = SingleInstance(lambda: calls.append('wrong'), directory=tmp_path, name=name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        second.release()
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            app.processEvents()
        assert calls == ['show'] and first.owned and not second.owned
        assert second.acquire() is False
    finally:
        first.release()
        second.release()


def test_new_launch_can_run_after_first_exits(tmp_path):
    app = QApplication.instance() or QApplication([])
    name = 'ugsync-instance-test-' + uuid.uuid4().hex
    first = SingleInstance(lambda: None, directory=tmp_path, name=name)
    second = SingleInstance(lambda: None, directory=tmp_path, name=name)
    try:
        assert first.acquire()
        first.release()
        assert second.acquire()
    finally:
        first.release()
        second.release()


def test_separate_process_cannot_create_another_primary(tmp_path):
    app = QApplication.instance() or QApplication([])
    calls = []
    name = 'ugsync-instance-test-' + uuid.uuid4().hex
    first = SingleInstance(lambda: calls.append('show'), directory=tmp_path, name=name)
    code = ('import sys; from PySide6.QtWidgets import QApplication; '
            'from syncthing_bridge.instance import SingleInstance; '
            'app=QApplication([]); i=SingleInstance(lambda:None,directory=sys.argv[1],name=sys.argv[2]); '
            'print(i.acquire()); i.release()')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                       PYTHONPATH=str(Path(__file__).resolve().parents[2]/'client'))
    try:
        assert first.acquire()
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path), name],
                                capture_output=True, text=True, timeout=10, env=environment)
        assert result.returncode == 0 and result.stdout.strip() == 'False', result.stderr
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            app.processEvents()
        assert calls == ['show'] and first.owned
    finally:
        first.release()


def test_crashed_process_does_not_block_next_launch(tmp_path):
    app = QApplication.instance() or QApplication([])
    name = 'ugsync-instance-test-' + uuid.uuid4().hex
    code = ('import sys,os; from PySide6.QtWidgets import QApplication; '
            'from syncthing_bridge.instance import SingleInstance; '
            'app=QApplication([]); i=SingleInstance(lambda:None,directory=sys.argv[1],name=sys.argv[2]); '
            'assert i.acquire(); os._exit(0)')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                       PYTHONPATH=str(Path(__file__).resolve().parents[2]/'client'))
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path), name],
                            capture_output=True, text=True, timeout=10, env=environment)
    assert result.returncode == 0, result.stderr
    recovered = SingleInstance(lambda: None, directory=tmp_path, name=name)
    try:
        assert recovered.acquire()
    finally:
        recovered.release()
