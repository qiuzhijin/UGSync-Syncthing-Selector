from syncthing_bridge.overlays import file_state
from syncthing_bridge.overlays import selection_excludes
from syncthing_bridge.overlays import selection_partial
from syncthing_bridge.selection import compile_document


def test_deselected_local_paths_are_stopped_even_when_peer_offline():
    rules = compile_document([], ['Work/Keep'], entire_root=False)
    assert selection_excludes('Photos', rules, directory=True)
    assert selection_excludes('Photos/old.jpg', rules)
    assert not selection_excludes('Work', rules, directory=True)
    assert not selection_excludes('Work/Keep/file.txt', rules)
    assert not selection_excludes('.', rules, directory=True)
    resumed = compile_document([], entire_root=True)
    assert not selection_excludes('Photos/old.jpg', resumed)


def test_nested_selection_and_foreign_rules_are_not_guessed():
    rules = compile_document([], ['Work', 'Work/Private/Keep'], ['Work/Private'], entire_root=False)
    assert selection_excludes('Work/Private/file.txt', rules)
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


def test_parent_with_selected_descendant_publishes_blue_not_red(tmp_path):
    import sys
    import pytest
    if sys.platform != 'win32':
        pytest.skip('Windows overlay publisher')
    from syncthing_bridge.overlays import OverlayPublisher

    class Api:
        def request(self, method, route, **kwargs):
            assert (method, route) == ('GET', '/rest/db/file')
            return {'local': {'ignored': True}}

    root = tmp_path / 'Drive'
    parent = root / 'Work'
    (parent / 'Keep').mkdir(parents=True)
    publisher = OverlayPublisher('http://127.0.0.1:8384', 'test', cache=tmp_path / 'cache')
    (publisher.cache / 'cache').mkdir(parents=True)
    folder = {'id': 'drive', 'path': str(root), 'devices': [{'deviceID': 'nas'}],
              '_overlay_selection': compile_document([], ['Work/Keep'], entire_root=False)}
    publisher.inspect(Api(), [folder], {'nas': {'connected': True}}, 'pc', str(parent))
    assert publisher.records[str(parent)][0] == 'B'


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
