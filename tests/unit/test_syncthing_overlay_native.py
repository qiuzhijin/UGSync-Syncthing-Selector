import ctypes
import os
from pathlib import Path
import sys
import time

import pytest

from syncthing_bridge.overlays import OverlayPublisher, cache_key


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
