"""Present Syncthing completion separately from transport byte counters."""
import time


def size_text(value):
    value = max(0, float(value))
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return ('%.1f ' % value) + unit
        value /= 1024


class ProgressDisplay:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.previous = None

    def speed(self, total):
        now = self.clock()
        counters = (total.get('inBytesTotal', 0), total.get('outBytesTotal', 0))
        speed = '引擎总速度：正在采样'
        if self.previous:
            previous_time, previous = self.previous
            elapsed = now - previous_time
            if elapsed > 0 and all(a >= b for a,b in zip(counters, previous)):
                speed = '引擎总速度：下载 %s/s · 上传 %s/s' % tuple(
                    size_text((a-b)/elapsed) for a,b in zip(counters, previous))
        self.previous = (now, counters)
        return speed

    def update(self, snapshot):
        if snapshot.get('paused'):
            self.previous = None
            return 0, '已暂停', '点击“继续同步”恢复任务'
        local = snapshot['local']
        peers = [peer for peer in snapshot['peers'] if peer.get('connected')]
        offline = len(snapshot['peers']) - len(peers)
        total = snapshot.get('traffic', {})
        speed = self.speed(total)
        need = max(0, local.get('needBytes', 0))
        total_bytes = max(0, local.get('inSyncBytes', 0)) + need
        local_percent = 100 * (total_bytes - need) / total_bytes if total_bytes else 100
        metadata_pending = local.get('needTotalItems', local.get('needFiles', 0)) > 0 and not need
        waiting = sum(max(0, p.get('needItems', 0), p.get('needDeletes', 0)) for p in peers)
        outgoing = sum(max(0, p.get('needBytes', 0)) for p in peers)
        downloading = bool(need or local.get('needTotalItems', local.get('needFiles', 0)))
        uploading = bool(waiting or outgoing)
        detail = '本机待下载 %s 项 / %s · 对端待接收 %s 项 / %s\n%s' % (
            local.get('needFiles', 0), size_text(need), waiting, size_text(outgoing), speed)
        if offline:
            detail += f' · 离线 {offline} 台'
        if local.get('state') == 'error' or local.get('folderErrors'):
            return None, '同步出错，请检查引擎错误列表', detail
        if local.get('state', '').startswith('scan'):
            return None, '正在扫描文件…', detail
        if not peers:
            return None, '等待设备连接（部分设备可能离线）', detail
        if metadata_pending or any(p.get('remoteState') != 'valid' for p in peers):
            return None, '正在核对文件索引…', detail
        percent = min([local_percent] + [max(0, min(100, p.get('completion', 0))) for p in peers])
        pending = local.get('needTotalItems', local.get('needFiles', 0)) or waiting or outgoing
        if percent >= 100 and not pending and local.get('state') == 'idle':
            if offline:
                return 1000, '已连接设备已同步 · 部分设备离线', detail
            return 1000, '已同步 · 两端完成 100%', detail
        percent = min(percent, 99.9)
        if uploading and downloading:
            title = '正在上传和下载 · %.1f%%' % percent
        elif uploading:
            title = '正在上传 · %.1f%%' % percent
        elif downloading:
            title = '正在下载 · %.1f%%' % percent
        else:
            title = '正在同步 · %.1f%%' % percent
        return int(percent * 10), title, detail
