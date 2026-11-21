import pytest

from syncthing_bridge.overlays import file_state
from syncthing_bridge.overlays import selection_excludes
from syncthing_bridge.overlays import selection_partial
from syncthing_bridge.selection import compile_document


def test_deselected_local_paths_are_stopped_even_when_peer_offline():
    rules = compile_document([], ['Work/Keep'], entire_root=False)
    assert selection_excludes('Photos', rules, directory=True)
    assert selection_excludes('Photos/old.jpg', rules)
    assert not selection_excludes('Work', rules, directory=True)
    assert not selection_excludes('Work/direct.txt', rules)
    assert not selection_excludes('direct.txt', rules)
    assert selection_excludes('Work/Other/direct.txt', rules)
    assert not selection_excludes('Work/Keep/file.txt', rules)
    assert not selection_excludes('.', rules, directory=True)
    resumed = compile_document([], entire_root=True)
    assert not selection_excludes('Photos/old.jpg', resumed)


def test_nested_selection_and_foreign_rules_are_not_guessed():
    rules = compile_document([], ['Work', 'Work/Private/Keep'], ['Work/Private'], entire_root=False)
    assert not selection_excludes('Work/Private/file.txt', rules)
    assert selection_excludes('Work/Private/Other/file.txt', rules)
    assert not selection_excludes('Work/Private', rules, directory=True)
    assert not selection_excludes('Work/Private/Keep/file.txt', rules)
    assert not selection_excludes('Other', ['#include external.txt'])


def test_partially_selected_parent_is_not_treated_as_excluded():
    rules = compile_document([], ['Work/Keep'], entire_root=False)
    assert selection_partial('.', rules)
    assert selection_partial('Work', rules)
    assert not selection_partial('Work/Keep', rules)
    assert not selection_partial('Photos', rules)


def test_existing_deselected_file_and_directory_publish_red_without_peer(tmp_path):
    import sys
    import pytest
    if sys.platform != 'win32':
        pytest.skip('Windows overlay publisher')
    from syncthing_bridge.overlays import OverlayPublisher
    root = tmp_path / 'Drive'
    directory = root / 'Photos'
    directory.mkdir(parents=True)
    file = directory / 'local.txt'
    file.write_text('retained locally')
    publisher = OverlayPublisher('http://127.0.0.1:8384', 'test', cache=tmp_path / 'cache')
    (publisher.cache / 'cache').mkdir(parents=True)
    folder = {'id': 'drive', 'path': str(root), 'devices': [{'deviceID': 'nas'}],
              '_overlay_selection': compile_document([], ['Work'], entire_root=False)}
    for path in (directory, file):
        publisher.inspect(None, [folder], {}, 'pc', str(path))
        assert publisher.records[str(path)][0] == 'R'
    folder['_overlay_selection'] = compile_document([], entire_root=True)
    publisher.inspect(None, [folder], {}, 'pc', str(file))
    assert str(file) not in publisher.records  # Offline after reselecting is unknown, not stopped.
    assert file.read_text() == 'retained locally'


@pytest.mark.parametrize('downloads,uploads,reported,expected', [
    ([], [], 0, 'G'),
    (['Elsewhere/file.txt'], ['Work/Skipped/file.txt', 'Workshop/file.txt'], 1, 'G'),
    (['Work/Keep/file.txt'], [], 1, 'B'),
    (['Work'], [], 1, 'B'),
    (['Work/direct.txt'], [], 1, 'B'),
    ([], ['Work/direct.txt'], 0, 'B'),
    ([], ['Work/Keep/file.txt'], 0, 'B'),
    ([], [], 1, 'B'),
    ([], [], 1024, 'B'),
    ([f'Elsewhere/{index}.txt' for index in range(256)] + ['Work/Keep/later.txt'], [], 257, 'B'),
])
def test_partial_parent_state_uses_only_selected_content(tmp_path, downloads, uploads, reported, expected):
    import sys
    import pytest
    if sys.platform != 'win32':
        pytest.skip('Windows overlay publisher')
    from syncthing_bridge.overlays import OverlayPublisher

    class Api:
        def status(self, folder):
            return {'state': 'syncing' if reported else 'idle', 'needTotalItems': reported}

        def request(self, method, route, **kwargs):
            if route == '/rest/db/file':
                return {'local': {'name': 'Work'}}
            if route == '/rest/db/need':
                params = kwargs['params']
                size = params['perpage']
                start = (params['page'] - 1) * size
                return {'queued': [{'name': name} for name in downloads[start:start + size]],
                        'progress': [], 'rest': []}
            if route == '/rest/db/remoteneed':
                return {'files': [{'name': name.replace('/', '\\')} for name in uploads]}
            assert route == '/rest/db/completion'
            return {'remoteState': 'valid', 'needItems': len(uploads), 'needDeletes': 0}

    root = tmp_path / 'Drive'
    parent = root / 'Work'
    (parent / 'Keep').mkdir(parents=True)
    publisher = OverlayPublisher('http://127.0.0.1:8384', 'test', cache=tmp_path / 'cache')
    (publisher.cache / 'cache').mkdir(parents=True)
    folder = {'id': 'drive', 'path': str(root), 'devices': [{'deviceID': 'nas'}],
              '_overlay_selection': compile_document([], ['Work/Keep'], direct_files=['Work/direct.txt'])}
    publisher.inspect(Api(), [folder], {'nas': {'connected': True}}, 'pc', str(parent))
    assert publisher.records[str(parent)][0] == expected

    folder['_overlay_selection'] = compile_document([], ['Work'], direct_files=[])
    publisher.inspect(Api(), [folder], {'nas': {'connected': True}}, 'pc', str(parent))
    assert publisher.records[str(parent)][0] == ('B' if 'Work/Skipped/file.txt' in uploads else expected)


def test_green_requires_matching_version_and_full_remote_copy():
    info = {'local': {'version': ['pc:1'], 'size': 10, 'modified': '2026-01-01T00:00:00Z'},
            'global': {'version': ['pc:1']}, 'availability': [{'id': 'nas', 'fromTemporary': False}]}
    assert file_state(info, ['nas'], 10, 1767225600) == 'G'
    info['availability'] = None
    assert file_state(info, ['nas'], 10, 1767225600) == 'B'
    info['availability'] = [{'id': 'nas', 'fromTemporary': True}]
    assert file_state(info, ['nas'], 10, 1767225600) == 'B'


def test_changed_unscanned_file_never_green():
    info = {'local': {'version': ['pc:1'], 'size': 10, 'modified': '2026-01-01T00:00:00Z'},
            'global': {'version': ['pc:1']}, 'availability': [{'id': 'nas', 'fromTemporary': False}]}
    assert file_state(info, ['nas'], 11, 1767225600) == 'B'
    assert file_state(info, ['nas'], 10, 1767225602) == 'B'
    info['local']['ignored'] = True
    assert file_state(info, ['nas'], 10, 1767225600) == 'R'


def test_unknown_or_offline_has_no_green():
    assert file_state({'local': None}, [], 0, 0) is None


def test_root_listing_notifies_explorer_after_becoming_available(tmp_path):
    from syncthing_bridge.overlays import OverlayPublisher

    publisher = OverlayPublisher('http://127.0.0.1:8384', 'test', cache=tmp_path)
    root = str(tmp_path / 'Drive')
    notifications = []

    def notify(path):
        assert (tmp_path / 'roots.txt').read_bytes().decode('utf-16-le') == root
        notifications.append(path)

    publisher.notify_directory = notify
    publisher.publish_roots([root])
    publisher.publish_roots([root])
    assert notifications == [root]


def test_new_explorer_request_wakes_publisher_before_periodic_refresh(tmp_path, monkeypatch):
    from syncthing_bridge.overlays import OverlayPublisher

    publisher = OverlayPublisher('http://127.0.0.1:8384', 'test', cache=tmp_path)
    requests = tmp_path / 'requests'
    requests.mkdir()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            (requests / 'new.txt').touch()
        return False

    monkeypatch.setattr(publisher.stop_event, 'wait', wait)
    publisher.wait_for_work(wake_on_request=True)
    assert sum(waits) < 2

    (requests / 'new.txt').unlink()
    waits.clear()
    publisher.wait_for_work(wake_on_request=False)
    assert sum(waits) == 2
