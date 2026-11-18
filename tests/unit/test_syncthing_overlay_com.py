import ctypes
from pathlib import Path
import sys
import uuid

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='native COM smoke test')
def test_native_com_factory_and_icon_resources():
    dll = Path('dist/explorer-overlays/UGSyncOverlay.dll').resolve()
    if not dll.is_file():
        pytest.skip('build overlays first')
    native = ctypes.WinDLL(str(dll))
    def guid(text):
        return ctypes.create_string_buffer(uuid.UUID(text).bytes_le, 16)
    create = native.DllGetClassObject
    create.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    def method(pointer, index, restype, *argtypes):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])
    for number, color in enumerate(('blue', 'green', 'red'), 1):
        factory = ctypes.c_void_p()
        assert create(guid('a22f082%d-2b78-49ce-b27c-1041d875b011' % number),
                      guid('00000001-0000-0000-c000-000000000046'), ctypes.byref(factory)) == 0
        overlay = ctypes.c_void_p()
        build = method(factory, 3, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        assert build(factory, None, guid('0c6c4200-c589-11d0-999a-00c04fd655e1'), ctypes.byref(overlay)) == 0
        path = ctypes.create_unicode_buffer(4096)
        index, flags = ctypes.c_int(), ctypes.c_ulong()
        get_icon = method(overlay, 4, ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong))
        assert get_icon(overlay, path, len(path), ctypes.byref(index), ctypes.byref(flags)) == 0
        assert Path(path.value) == dll.parent / (color + '.ico')
        assert Path(path.value).is_file() and flags.value == 3
        method(overlay, 2, ctypes.c_ulong)(overlay)
        method(factory, 2, ctypes.c_ulong)(factory)
    assert native.DllCanUnloadNow() == 0
