from syncthing_bridge.progress import ProgressDisplay


def snapshot(**local):
    return {'local': dict(inSyncBytes=75, needBytes=25, needFiles=1, needTotalItems=1,
                          state='syncing', **local),
            'peers': [{'connected': True, 'remoteState': 'valid', 'completion': 60, 'needItems': 1}],
            'traffic': {'inBytesTotal': 100, 'outBytesTotal': 200}}


def test_overall_progress_waits_for_slowest_side():
    percent, title, detail = ProgressDisplay().update(snapshot())
    assert percent == 600 and '60.0%' in title and '25.0 B' in detail


def test_idle_local_does_not_hide_outstanding_upload():
    value = snapshot()
    value['local'].update(needBytes=0, needTotalItems=0, needFiles=0, state='idle')
    assert ProgressDisplay().update(value)[0] == 600


def test_idle_local_upload_is_explicit_about_peer_queue():
    value = snapshot()
    value['local'].update(needBytes=0, needTotalItems=0, needFiles=0, state='idle')
    value['peers'][0].update(completion=12.66, needItems=1344919, needBytes=2874476051840)
    _, title, detail = ProgressDisplay().update(value)
    assert '正在上传' in title
    assert '对端待接收 1344919 项' in detail
    assert '2.6 TiB' in detail


def test_peer_outgoing_bytes_prevent_false_completion():
    value = snapshot()
    value['local'].update(needBytes=0, needTotalItems=0, needFiles=0, state='idle')
    value['peers'][0].update(completion=100, needItems=0, needBytes=1024)
    percent, title, detail = ProgressDisplay().update(value)
    assert percent == 999
    assert '正在上传' in title
    assert '1.0 KiB' in detail


def test_both_sides_complete():
    value = snapshot()
    value['local'].update(needBytes=0, needTotalItems=0, needFiles=0, state='idle')
    value['peers'][0].update(completion=100, needItems=0)
    assert ProgressDisplay().update(value)[0] == 1000


def test_scan_offline_and_pause_are_not_false_completion():
    display = ProgressDisplay()
    value = snapshot()
    value['local']['state'] = 'scanning'
    assert display.update(value)[0] is None
    value['local']['state'] = 'idle'
    value['peers'][0]['connected'] = False
    assert display.update(value)[0] is None
    assert display.update({'paused': True})[1] == '已暂停'


def test_stale_status_error_count_without_error_details_does_not_block_progress():
    value = snapshot(errors=1, pullErrors=1, folderErrors=[])
    percent, title, _ = ProgressDisplay().update(value)
    assert percent == 600
    assert title.startswith('正在上传和下载')


def test_speed_samples_and_counter_reset():
    now = [1.0]
    display = ProgressDisplay(clock=lambda: now[0])
    value = snapshot()
    display.update(value)
    now[0] = 3
    value['traffic'] = {'inBytesTotal': 2148, 'outBytesTotal': 1224}
    detail = display.update(value)[2]
    assert '下载 1.0 KiB/s' in detail and '上传 512.0 B/s' in detail
    now[0] = 4
    value['traffic'] = {'inBytesTotal': 0, 'outBytesTotal': 0}
    assert '正在采样' in display.update(value)[2]
