import httpx
import pytest

from syncthing_bridge import diagnostics
from syncthing_bridge.api import Syncthing, SyncthingError


def test_error_log_records_browse_failure_without_private_details(tmp_path):
    with diagnostics.error_log(tmp_path, build=32) as path:
        api = Syncthing('http://example.invalid:8384', 'private-api-key', transport=httpx.MockTransport(
            lambda _: httpx.Response(500, text="could not find child 'Private' for path 'Private/file.txt'")))
        try:
            with pytest.raises(SyncthingError):
                api.browse('private-task', 'Private/subfolder')
        finally:
            api.close()
        content = path.read_text(encoding='utf-8')
    for value in ('build=32', 'GET /rest/db/browse', 'HTTP 500', 'missing_directory_parent'):
        assert value in content
    for value in ('private-api-key', 'example.invalid', 'private-task', 'Private'):
        assert value not in content


def test_error_log_falls_back_when_install_log_directory_is_unavailable(tmp_path):
    target = tmp_path / 'blocked'
    target.write_text('not a directory')
    fallback = tmp_path / 'fallback'
    with diagnostics.error_log(target, fallback=fallback) as path:
        assert path.parent == fallback
        assert path.is_file()


def test_error_log_omits_exception_message(tmp_path):
    with diagnostics.error_log(tmp_path) as path:
        diagnostics.report_exception('expand_directory', ValueError('Private/file.txt'))
        content = path.read_text(encoding='utf-8')
    assert 'operation=expand_directory error=ValueError' in content
    assert 'Private/file.txt' not in content
