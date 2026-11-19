import httpx
import pytest

from syncthing_bridge.api import Syncthing


def test_progress_snapshot_checks_folder_error_details_when_status_has_a_count():
    def respond(request):
        values = {
            '/rest/config/folders': [{'id': 'drive', 'devices': [{'deviceID': 'pc'}]}],
            '/rest/db/status': {'state': 'idle', 'errors': 1, 'pullErrors': 1},
            '/rest/folder/errors': {'errors': None},
            '/rest/system/status': {'myID': 'pc'},
            '/rest/system/connections': {'connections': {}},
        }
        return httpx.Response(200, json=values[request.url.path])
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    snapshot = api.progress_snapshot('drive')
    assert snapshot['local']['folderErrors'] == []
    api.close()


def test_local_file_and_directory_deletions_are_visible_without_claiming_remote_completion():
    def respond(request):
        if request.url.path == '/rest/events':
            assert 'LocalChangeDetected' in request.url.params['events'].split(',')
            return httpx.Response(200, json=[
                {'type': 'LocalChangeDetected', 'time': '2026-11-14T10:00:00Z',
                 'data': {'folder': folder, 'path': name, 'type': kind, 'action': action}}
                for folder, name, kind, action in [
                    ('drive', 'removed.txt', 'file', 'deleted'),
                    ('drive', 'removed-directory', 'dir', 'deleted'),
                    ('drive', 'changed.txt', 'file', 'modified'),
                    ('other', 'unrelated.txt', 'file', 'deleted')]])
        return httpx.Response(200, json={'/rest/config/folders': [{'id': 'drive', 'paused': True}]}[request.url.path])
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    result = api.activity('drive')
    assert [r['name'] for r in result['recent']] == ['removed-directory', 'removed.txt']
    assert all(r['status'] == '本地已删除（未核对对端）' for r in result['recent'])
    assert not result['uploads']
    api.close()


def test_remote_download_activity_separates_nas_progress_from_queue():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'progress': [{'name': 'NAS-正在下载.txt'}],
            'queued': [{'name': 'NAS-等待下载.txt'}], 'rest': [{'name': 'NAS-稍后下载.txt'}]})

    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    result = api.download_activity('drive', page=2)
    assert [row['status'] for row in result['current']] == ['NAS 正在下载']
    assert [row['status'] for row in result['queue']] == ['NAS 等待下载', 'NAS 等待下载']
    assert all(row['direction'] == '来源由 NAS Syncthing 选择' for row in result['current'] + result['queue'])
    assert calls[0].url.path == '/rest/db/need'
    assert calls[0].url.params['page'] == '2'
    api.close()


def test_timed_out_peer_does_not_hide_local_activity_or_other_peer_queue():
    failed = True

    def respond(request):
        if request.url.path == '/rest/db/remoteneed':
            peer = request.url.params['device']
            if peer == 'slow-peer' and failed:
                raise httpx.ReadTimeout('Synthetic timeout', request=request)
            return httpx.Response(200, json={'files': [{'name': f'{peer}.txt'}]})
        values = {
            '/rest/config/folders': [{'id': 'drive', 'devices': [
                {'deviceID': 'slow-peer'}, {'deviceID': 'good-peer'}]}],
            '/rest/events': [{'data': {'folder': 'drive', 'item': 'finished.txt', 'type': 'file'}}],
            '/rest/db/need': {'progress': [{'name': 'downloading.txt'}],
                              'queued': [{'name': 'queued.txt'}]},
            '/rest/system/status': {'myID': 'pc'},
            '/rest/system/connections': {'connections': {
                'slow-peer': {'connected': True}, 'good-peer': {'connected': True}}},
            '/rest/db/file': {'global': {'version': ['pc:1']}, 'local': {'version': ['pc:1']},
                              'availability': [{'id': 'slow-peer', 'fromTemporary': False}]},
        }
        return httpx.Response(200, json=values[request.url.path])

    tracked = {'name': 'prior.txt', 'device': 'slow-peer', 'version': ['pc:1']}
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    try:
        result = api.activity('drive', tracked=[tracked])
        assert [row['name'] for row in result['current']] == ['downloading.txt']
        assert [row['name'] for row in result['queue']] == ['queued.txt', 'good-peer.txt']
        assert [row['name'] for row in result['recent']] == ['finished.txt']
        assert result['queue_incomplete']
        assert '超时' in result['notices'][0]['status']
        assert not result['uploads']
        assert tracked in result['tracked']

        failed = False
        result = api.activity('drive', tracked=result['tracked'])
        assert not result.get('queue_incomplete')
        assert [row['name'] for row in result['queue']] == ['queued.txt', 'slow-peer.txt', 'good-peer.txt']
        assert [row['name'] for row in result['uploads']] == ['prior.txt']
    finally:
        api.close()


@pytest.mark.parametrize('file_response', [
    {'global': {'version': ['pc:1']}, 'local': {'version': ['pc:1']}, 'availability': None},
    {'global': None, 'local': None, 'availability': None},
])
def test_second_page_survives_null_file_availability(file_response):
    def respond(request):
        values = {'/rest/config/folders': [{'id': 'drive', 'devices': [{'deviceID': 'nas'}]}],
            '/rest/events': [], '/rest/db/need': {}, '/rest/system/status': {'myID': 'pc'},
            '/rest/system/connections': {'connections': {'nas': {'connected': True}}},
            '/rest/db/remoteneed': {'files': [{'name': 'page-two.txt'}]},
            '/rest/db/file': file_response}
        return httpx.Response(200, json=values[request.url.path])
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    result = api.activity('drive', page=2, tracked=[{'name': 'page-one.txt', 'device': 'nas', 'version': ['pc:1']}])
    assert result['queue'][0]['name'] == 'page-two.txt'
    assert not result['uploads']
    api.close()


def test_activity_separates_active_queued_completed_and_errors():
    calls = []
    def respond(request):
        calls.append(request)
        route = request.url.path
        data = {
            '/rest/config/folders': [{'id': 'drive', 'devices': [{'deviceID': 'nas'}]}],
            '/rest/system/status': {'myID': 'pc'},
            '/rest/system/connections': {'connections': {'nas': {'connected': True}}},
            '/rest/db/need': {'progress': [{'name': '正在.txt'}], 'queued': [{'name': '稍后.txt'}]},
            '/rest/db/remoteneed': {'files': [{'name': '上传.txt'}]},
            '/rest/events': [
                {'id': 1, 'time': '2026-11-13T10:00:00+08:00', 'data': {'folder': 'other', 'item': '别的.txt'}},
                {'id': 2, 'time': '2026-11-13T10:01:00+08:00', 'data': {'folder': 'drive', 'item': '完成.txt', 'type': 'file', 'action': 'update'}},
                {'id': 3, 'time': '2026-11-13T10:02:00+08:00', 'data': {'folder': 'drive', 'item': '错误.txt', 'type': 'file', 'error': 'locked'}},
            ],
        }[route]
        return httpx.Response(200, json=data)
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    result = api.activity('drive')
    assert [row['status'] for row in result['current']] == ['正在下载到本机']
    assert [row['status'] for row in result['queue']] == ['等待下载到本机', '等待上传至对端']
    assert [row['name'] for row in result['recent']] == ['完成.txt']
    assert result['notices'][0]['name'] == '错误.txt'
    assert all(r.url.params.get('timeout') == '0' for r in calls if r.url.path == '/rest/events')
    assert all(r.url.params.get('perpage') == '30' for r in calls if r.url.path in ('/rest/db/need', '/rest/db/remoteneed'))
    api.close()


def test_activity_pagination_and_offline_device_are_explicit():
    requests = []
    def respond(request):
        requests.append(request)
        values = {'/rest/config/folders': [{'id': 'drive', 'devices': [{'deviceID': 'nas'}]}],
            '/rest/events': [], '/rest/db/need': {}, '/rest/system/status': {'myID': 'pc'},
            '/rest/system/connections': {'connections': {}}}
        return httpx.Response(200, json=values[request.url.path])
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    result = api.activity('drive', page=2)
    assert result['page'] == 2
    assert result['notices'] and '离线' in result['notices'][0]['status']
    assert next(r for r in requests if r.url.path == '/rest/db/need').url.params['page'] == '2'
    api.close()


def test_queue_disappearance_with_changed_version_is_not_completion():
    def respond(request):
        values = {'/rest/config/folders': [{'id': 'drive', 'devices': [{'deviceID': 'nas'}]}],
            '/rest/events': [], '/rest/db/need': {}, '/rest/system/status': {'myID': 'pc'},
            '/rest/system/connections': {'connections': {'nas': {'connected': True}}},
            '/rest/db/remoteneed': {'files': []}, '/rest/db/file': {
                'global': {'version': ['pc:2']}, 'local': {'version': ['pc:2']},
                'availability': [{'id': 'nas', 'fromTemporary': False}]}}
        return httpx.Response(200, json=values[request.url.path])
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    result = api.activity('drive', tracked=[{'name': 'changed.txt', 'device': 'nas', 'version': ['pc:1']}])
    assert not result['uploads']
    api.close()


def test_upload_completion_requires_same_version_and_full_remote_copy():
    def respond(request):
        if request.url.path == '/rest/db/file':
            temporary = request.url.params['file'] == 'partial.txt'
            return httpx.Response(200, json={'global': {'version': ['pc:1']},
                'local': {'version': ['pc:1']}, 'availability': [{'id': 'nas', 'fromTemporary': temporary}]})
        values = {'/rest/config/folders': [{'id': 'drive', 'devices': [{'deviceID': 'nas'}]}],
            '/rest/events': [], '/rest/db/need': {}, '/rest/system/status': {'myID': 'pc'},
            '/rest/system/connections': {'connections': {'nas': {'connected': True}}},
            '/rest/db/remoteneed': {'files': []}}
        return httpx.Response(200, json=values[request.url.path])
    api = Syncthing('http://127.0.0.1:8384', 'synthetic', transport=httpx.MockTransport(respond))
    tracked = [{'name': name, 'device': 'nas', 'version': ['pc:1']} for name in ('done.txt', 'partial.txt')]
    result = api.activity('drive', tracked=tracked)
    assert [row['name'] for row in result['uploads']] == ['done.txt']
    assert result['uploads'][0]['status'] == '已确认对端接收'
    assert [row['name'] for row in result['tracked']] == ['partial.txt']
    api.close()
