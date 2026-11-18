import httpx
import pytest

from syncthing_bridge.selection import compile_selection, parse_selection
from syncthing_bridge.api import Syncthing, SyncthingError


def test_preserved_rules_keep_precedence_and_roundtrip():
    from syncthing_bridge.selection import parse_document, compile_document
    old = ['/UGSync-Test', '*.tmp', '(?d).*', 'Icon\\r']
    base, entire, choices, escape = parse_document(old)
    assert entire and not choices and base == old and escape == '|'
    rules = compile_document(base, ['工作/保留[1]'], ['工作/临时'], escape=escape)
    assert rules[1:5] == old
    assert rules.index('*.tmp') < rules.index('!/工作/保留|[1|]')
    saved, entire, choices, escape = parse_document(rules)
    assert not entire and choices == {'工作/保留[1]': True, '工作/临时': False}
    assert compile_document(saved, [p for p,v in choices.items() if v],
                            [p for p,v in choices.items() if not v], escape=escape) == rules


@pytest.mark.parametrize('rules', [['!important', '*'], ['#include user.rules']])
def test_preservation_refuses_rules_that_can_override_tree(rules):
    from syncthing_bridge.selection import parse_document
    with pytest.raises(ValueError):
        parse_document(rules)


def test_local_credentials_never_forward_key_to_remote_host(tmp_path):
    from syncthing_bridge.api import local_credentials
    path = tmp_path/'config.xml'
    path.write_text('<configuration><gui><address>127.0.0.1:8384</address><apikey>synthetic</apikey></gui></configuration>')
    assert local_credentials(path) == ('http://127.0.0.1:8384', 'synthetic')
    path.write_text('<configuration><gui><address>remote.example:8384</address><apikey>synthetic</apikey></gui></configuration>')
    with pytest.raises(SyncthingError):
        local_credentials(path)


def test_specific_exclusion_precedes_parent_inclusion():
    rules = compile_selection(['工作'], ['工作/临时'])
    assert rules.index('/工作/临时/**') < rules.index('!/工作/**')
    assert rules[-1] == '*'
    assert compile_selection()[-1] == '*'
    assert '*' not in compile_selection(entire_root=True)


def test_nested_reinclusion_and_literal_globs():
    rules = compile_selection(['A', 'A/cache/保留[1]'], ['A/cache'])
    assert rules.index('!/A/cache/保留\\[1\\]/**') < rules.index('/A/cache/**')
    assert rules[0] == '#escape=\\'


def test_selected_descendant_also_includes_direct_files_of_each_parent():
    rules = compile_selection(['Work/Keep'], entire_root=False)
    assert '!/*' in rules
    assert '!/Work/*' in rules
    assert '!/Work/Keep/**' in rules
    assert parse_selection(rules) == (False, {'Work/Keep': True})


@pytest.mark.parametrize('path', ['../x', '/x', 'a//b', 'a\\b', 'a\nb', ' a', 'a/..'])
def test_reject_invalid_paths(path):
    with pytest.raises(ValueError):
        compile_selection([path])


@pytest.mark.parametrize('paused,changed', [(False, False), (True, True), (True, False)])
def test_save_requires_paused_unchanged_rules(paused, changed):
    writes = []
    rules = compile_selection(['工作'])
    def handler(request):
        nonlocal paused
        if request.url.path == '/rest/config/folders':
            return httpx.Response(200, json=[{'id': 'test', 'paused': paused}])
        if request.method == 'PATCH':
            paused = True
            return httpx.Response(200)
        if request.method == 'GET':
            return httpx.Response(200, json={'ignore': ['changed'] if changed else []})
        writes.append(request)
        return httpx.Response(200, json={'ignore': rules})
    api = Syncthing('http://127.0.0.1:8384', 'test-key', transport=httpx.MockTransport(handler))
    try:
        if not changed:
            api.save_selection('test', rules, [])
            assert len(writes) == 1
        else:
            with pytest.raises(SyncthingError):
                api.save_selection('test', rules, [])
            assert not writes
    finally:
        api.close()


def test_selection_restoration_preserves_unloaded_descendant_overrides():
    rules = compile_selection(['工作', '工作/缓存/保留[1]'], ['工作/缓存'])
    entire, choices = parse_selection(rules)
    assert entire is False
    assert choices == {'工作': True, '工作/缓存': False, '工作/缓存/保留[1]': True}
    assert parse_selection([]) == (True, {})
    assert parse_selection(['custom-pattern']) is None


def test_new_folder_without_ignore_file_is_empty_rules():
    api = Syncthing('http://127.0.0.1:8384', 'test-key', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={'ignore': None, 'error': None})))
    try:
        assert api.ignores('test') == []
    finally:
        api.close()
