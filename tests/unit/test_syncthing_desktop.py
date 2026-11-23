from contextlib import nullcontext
import os
from types import SimpleNamespace

from syncthing_bridge import desktop
import httpx
import pytest


@pytest.mark.parametrize('error', [httpx.ConnectError, httpx.ConnectTimeout])
def test_absent_engine_can_trigger_start_for_both_windows_socket_failures(monkeypatch, error):
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url):
            raise error('synthetic')
    monkeypatch.setattr(desktop.httpx, 'Client', Client)
    assert desktop.engine_available('http://127.0.0.1:8384') is False


def test_unresponsive_existing_listener_has_readable_error(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url):
            raise httpx.ReadTimeout('timed out')
    monkeypatch.setattr(desktop.httpx, 'Client', Client)
    with pytest.raises(RuntimeError, match='本机同步引擎响应超时'):
        desktop.engine_available('http://127.0.0.1:8384')


def test_user_startup_enable_disable_and_other_entries_preserved():
    values = {'UnrelatedApp': ('other', 1)}
    def query(key, name):
        if name not in values:
            raise FileNotFoundError()
        return values[name]
    def delete(key, name):
        if name not in values:
            raise FileNotFoundError()
        del values[name]
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, REG_SZ=1, KEY_SET_VALUE=2,
        OpenKey=lambda *args: nullcontext(None), CreateKeyEx=lambda *args: nullcontext(None),
        QueryValueEx=query, SetValueEx=lambda key,name,reserved,kind,value: values.update({name:(value,kind)}),
        DeleteValue=delete)
    assert not desktop.startup_enabled(registry)
    desktop.set_startup(True, registry)
    assert desktop.startup_enabled(registry)
    assert '--autostart' in values[desktop.RUN_VALUE][0]
    desktop.set_startup(False, registry)
    desktop.set_startup(False, registry)
    assert values == {'UnrelatedApp': ('other', 1)}


def test_bundle_engine_launch_does_not_change_config(tmp_path, monkeypatch):
    exe = tmp_path / 'engine' / ('syncthing.exe' if os.name == 'nt' else 'syncthing')
    exe.parent.mkdir()
    exe.write_bytes(b'synthetic')
    monkeypatch.setattr(desktop.sys, 'executable', str(tmp_path/'selector.exe'))
    called = []
    monkeypatch.setattr(desktop.subprocess, 'Popen', lambda args, **kwargs: called.append((args,kwargs)))
    desktop.launch_engine()
    args, options = called[0]
    assert args == [str(exe), 'serve', '--no-browser', '--no-upgrade']
    assert '--home' not in args and '--config' not in args
    assert options['stdin'] == desktop.subprocess.DEVNULL
    assert options['creationflags'] == (desktop.subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def test_startup_command_quotes_install_path(monkeypatch):
    monkeypatch.setattr(desktop.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(desktop.sys, 'executable', r'D:\Program Files\UGSync\selector.exe')
    assert desktop.startup_command() == '"D:\\Program Files\\UGSync\\selector.exe" --autostart'
