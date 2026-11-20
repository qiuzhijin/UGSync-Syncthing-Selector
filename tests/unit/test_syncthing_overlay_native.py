import base64
import ctypes
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from syncthing_bridge.overlays import OverlayPublisher, cache_key
from syncthing_bridge import overlay_install
from syncthing_bridge.overlay_install import overlay_extension_installed


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows elevation command')
def test_overlay_install_quotes_script_path_with_spaces(monkeypatch, tmp_path):
    payload = tmp_path / "Program Files" / "User's UGSync"
    payload.mkdir(parents=True)
    for name in overlay_install._PAYLOAD_FILES:
        (payload / name).touch()
    installed = iter((False, True))
    monkeypatch.setattr(overlay_install, '_payload_directory', lambda: payload)
    monkeypatch.setattr(overlay_install, 'overlay_extension_installed', lambda: next(installed))

    def launch(args, **kwargs):
        command = base64.b64decode(args[-1]).decode('utf-16-le')
        script = str(payload / 'install_overlays.ps1').replace("'", "''")
        assert f'-File "{script}" -Action Install' in command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(overlay_install.subprocess, 'run', launch)
    assert overlay_install.install_overlay_extension()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows registry')
@pytest.mark.parametrize('green', [None, '{INVALID}', '{A22F0822-2B78-49CE-B27C-1041D875B011}'])
def test_overlay_detection_requires_explorer_handlers_even_when_dll_exists(tmp_path, monkeypatch, green):
    import winreg

    dll = tmp_path / 'Overlay.dll'
    dll.touch()
    prefix = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers'
    ids = ('{A22F0821-2B78-49CE-B27C-1041D875B011}',
           '{A22F0822-2B78-49CE-B27C-1041D875B011}',
           '{A22F0823-2B78-49CE-B27C-1041D875B011}')
    values = {f'SOFTWARE\\Classes\\CLSID\\{clsid}\\InprocServer32': str(dll) for clsid in ids}
    for color, value in zip(('Blue', 'Green', 'Red'), (ids[0], green, ids[2])):
        if value is not None:
            values[f'{prefix}\\ 08UGSync {color}'] = value

    def open_key(root, subkey, *args):
        if subkey not in values:
            raise FileNotFoundError(subkey)
        return nullcontext(values[subkey])

    monkeypatch.setattr(winreg, 'OpenKey', open_key)
    monkeypatch.setattr(winreg, 'QueryValueEx', lambda key, _name: (key, winreg.REG_SZ))
    assert overlay_extension_installed() is (green == ids[1])


@pytest.mark.skipif(sys.platform != 'win32', reason='native Explorer extension')
def test_native_reader_checks_state_expiry_and_local_changes(tmp_path):
    dll = Path('dist/explorer-overlays/UGSyncOverlay.dll').resolve()
    if not dll.is_file():
        pytest.skip('build native overlays first')
    native = ctypes.WinDLL(str(dll))
    query = native.OverlayStateAt
    query.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    query.restype = ctypes.c_int
    base = tmp_path / 'state'
    (base / 'cache').mkdir(parents=True)
    (base / 'requests').mkdir()
    (base / 'roots.txt').write_bytes(str(tmp_path).encode('utf-16-le'))
    path = tmp_path / 'file.txt'
    path.write_text('data')
    publisher = OverlayPublisher('http://127.0.0.1:8384', 'synthetic', cache=base)
    stamp = path.stat().st_mtime_ns // 100 + 116444736000000000
    assert query(str(path), str(base)) == 0
    request = base / 'requests' / (cache_key(path) + '.txt')
    assert request.read_bytes().decode('utf-16-le') == str(path)
    for state, expected in [('B', 1), ('G', 2), ('R', 3)]:
        publisher.write(str(path), state, stamp, path.stat().st_size)
        assert query(str(path), str(base)) == expected
    publisher.write(str(path), 'G', stamp, path.stat().st_size)
    path.write_text('changed locally')
    assert query(str(path), str(base)) == 1
    record = base / 'cache' / (cache_key(path) + '.txt')
    record.write_text('U1 G 0 0 0')
    assert query(str(path), str(base)) == 0
    outside = tmp_path.parent / 'unrelated.txt'
    assert query(str(outside), str(base)) == 0
    assert not (base / 'requests' / (cache_key(outside) + '.txt')).exists()
