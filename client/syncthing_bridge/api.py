"""Small bounded REST adapter. Keys are provided by the caller, never stored."""
from urllib.parse import urlsplit

import httpx


def local_credentials(config_path=None):
    import os
    from pathlib import Path
    import xml.etree.ElementTree as ET
    try:
        path = Path(config_path) if config_path is not None else Path(os.environ['LOCALAPPDATA']) / 'Syncthing/config.xml'
        if path.stat().st_size > 2 << 20:
            raise ValueError()
        gui = ET.parse(path).getroot().find('gui')
        address = gui.findtext('address')
        scheme = 'https' if gui.get('tls', 'false') == 'true' else 'http'
        endpoint = scheme + '://' + address
        if urlsplit(endpoint).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError()
        key = gui.findtext('apikey')
        if not key:
            raise ValueError()
        return endpoint, key
    except (OSError, KeyError, ValueError, TypeError, AttributeError, ET.ParseError):
        raise SyncthingError('无法读取本机引擎配置，请手动输入地址和密钥') from None


class SyncthingError(RuntimeError):
    pass


class Syncthing:
    def __init__(self, endpoint, key, *, transport=None):
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ('', '/')):
            raise ValueError('请输入 Syncthing 管理地址')
        if not key or any(ord(c) < 33 for c in key):
            raise ValueError('请输入 API 密钥')
        self.http = httpx.Client(base_url=endpoint.rstrip('/'), headers={'X-API-Key': key},
                                 timeout=20, follow_redirects=False, trust_env=False,
                                 transport=transport)

    def close(self):
        self.http.close()

    def request(self, method, route, **kwargs):
        try:
            with self.http.stream(method, route, **kwargs) as response:
                if response.status_code not in (200, 204):
                    raise SyncthingError('同步引擎拒绝请求（HTTP %s）' % response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 4 << 20:
                        raise SyncthingError('目录或配置过大，请缩小范围')
                if not data:
                    return None
                import json
                return json.loads(data)
        except httpx.TimeoutException:
            raise SyncthingError('读取同步引擎超时') from None
        except (httpx.HTTPError, ValueError):
            raise SyncthingError('无法读取同步引擎响应，请检查地址与密钥') from None

    def folders(self):
        value = self.request('GET', '/rest/config/folders')
        if not isinstance(value, list) or any(not isinstance(row, dict) or
                not isinstance(row.get('id'), str) for row in value):
            raise SyncthingError('文件夹配置格式不受支持')
        return value

    def set_paused(self, folder, paused):
        from urllib.parse import quote
        if type(paused) is not bool:
            raise ValueError('暂停状态无效')
        self.request('PATCH', '/rest/config/folders/' + quote(folder, safe=''), json={'paused': paused})

    def status(self, folder):
        return self.request('GET', '/rest/db/status', params={'folder': folder})

    def download_activity(self, folder, *, page=1):
        """Return a bounded remote device download view without inferring file sources."""
        if type(page) is not int or page < 1:
            raise ValueError('页码无效')
        need = self.request('GET', '/rest/db/need', params={'folder': folder, 'page': page, 'perpage': 30})
        result = {'current': [], 'queue': [], 'has_more':
                  sum(len(need.get(key, [])) for key in ('progress', 'queued', 'rest')) >= 30}
        for item in need.get('progress', []):
            result['current'].append({'name': item['name'], 'status': 'NAS 正在下载',
                                      'direction': '来源由 NAS Syncthing 选择'})
        for section in ('queued', 'rest'):
            for item in need.get(section, []):
                result['queue'].append({'name': item['name'], 'status': 'NAS 等待下载',
                                        'direction': '来源由 NAS Syncthing 选择'})
        return result

    def progress_snapshot(self, folder):
        current = next(row for row in self.folders() if row['id'] == folder)
        if current.get('paused'):
            return {'paused': True}
        if not hasattr(self, '_device_id'):
            self._device_id = self.request('GET', '/rest/system/status')['myID']
        connections = self.request('GET', '/rest/system/connections')
        peers = []
        for device in current.get('devices', []):
            identity = device['deviceID']
            if identity == self._device_id:
                continue
            connection = connections['connections'].get(identity, {})
            peer = {'connected': bool(connection.get('connected'))}
            if peer['connected']:
                peer.update(self.request('GET', '/rest/db/completion', params={'folder': folder, 'device': identity}))
            peers.append(peer)
        local = self.status(folder)
        if local.get('errors') or local.get('pullErrors'):
            detail = self.request('GET', '/rest/folder/errors', params={'folder': folder})
            local['folderErrors'] = detail.get('errors') or [] if isinstance(detail, dict) else []
        return {'paused': False, 'local': local, 'peers': peers,
                'traffic': connections.get('total', {})}

    def browse(self, folder, prefix=''):
        value = self.request('GET', '/rest/db/browse', params={
            'folder': folder, 'prefix': prefix, 'levels': 1, 'dirsonly': 'true'})
        if not isinstance(value, list):
            raise SyncthingError('目录树格式不受支持')
        return value

    def activity(self, folder, *, tracked=(), page=1):
        """Read a bounded sample; remote need is a queue, not proof of transfer."""
        if type(page) is not int or page < 1:
            raise ValueError('页码无效')
        current = next(row for row in self.folders() if row['id'] == folder)
        result = {'current': [], 'queue': [], 'recent': [], 'notices': [], 'tracked': list(tracked),
                  'uploads': [], 'page': page, 'has_more': False}
        events = self.request('GET', '/rest/events', params={
            'events': 'ItemFinished,LocalChangeDetected', 'since': 0, 'limit': 100, 'timeout': 0})
        for event in reversed(events or []):
            data = event.get('data', {})
            if data.get('folder') != folder:
                continue
            if event.get('type') == 'LocalChangeDetected':
                # A scanned tombstone proves local deletion, not peer acknowledgement.
                if data.get('action') == 'deleted' and data.get('type') in ('file', 'dir'):
                    result['recent'].append({'name': data.get('path', ''),
                        'time': event.get('time', ''), 'status': '本地已删除（未核对对端）'})
                continue
            if data.get('type') != 'file':
                continue
            error = data.get('error')
            row = {'name': data.get('item', ''), 'time': event.get('time', ''),
                   'status': '同步失败' if error else {
                       'delete': '已同步删除', 'metadata': '已更新属性'}.get(data.get('action'), '已接收完成')}
            if error:
                row['status'] += '：' + str(error)
            result['notices' if error else 'recent'].append(row)
        if current.get('paused'):
            result['notices'].append({'name': '任务已暂停', 'status': '队列暂不可读取，不能据空列表判断已完成'})
            return result
        need = self.request('GET', '/rest/db/need', params={'folder': folder, 'page': page, 'perpage': 30})
        result['has_more'] = sum(len(need.get(k, [])) for k in ('progress', 'queued', 'rest')) >= 30
        for item in need.get('progress', []):
            result['current'].append({'name': item['name'], 'status': '正在下载到本机', 'direction': '对端 → 本机'})
        for section in ('queued', 'rest'):
            for item in need.get(section, []):
                result['queue'].append({'name': item['name'], 'status': '等待下载到本机', 'direction': '对端 → 本机'})
        identity = self.request('GET', '/rest/system/status')['myID']
        connections = self.request('GET', '/rest/system/connections').get('connections', {})
        peers = [d['deviceID'] for d in current.get('devices', [])
                 if d['deviceID'] != identity and connections.get(d['deviceID'], {}).get('connected')]
        for device in current.get('devices', []):
            peer = device['deviceID']
            if peer != identity and peer not in peers:
                result['notices'].append({'name': peer[:7], 'status': '设备离线，未读取其待接收清单'})
        if len(peers) > 4:
            result['notices'].append({'name': '设备数量限制', 'status': '仅查询前 4 台已连接设备，清单不代表全部设备'})
        pending = {}
        queried = set()
        for device in peers[:4]:
            try:
                need = self.request('GET', '/rest/db/remoteneed', params={
                    'folder': folder, 'device': device, 'page': page, 'perpage': 30})
            except SyncthingError as error:
                result['queue_incomplete'] = True
                result['notices'].append({'name': device[:7],
                    'status': f'对端待接收队列暂不可用：{error}'})
                continue
            queried.add(device)
            result['has_more'] |= len(need.get('files', [])) >= 30
            for item in need.get('files', []):
                result['queue'].append({'name': item['name'], 'status': '等待上传至对端',
                    'device': device, 'direction': '本机 → 对端 ' + device[:7]})
                if item.get('version') and not item.get('deleted') and not item.get('type', '').endswith('DIRECTORY'):
                    pending[(device, item['name'])] = {'name': item['name'], 'device': device, 'version': item['version']}
        checked = 0
        retained = []
        retry = []
        for row in tracked:
            key = (row['device'], row['name'])
            if key in pending:
                continue
            if row['device'] not in queried or checked >= 5:
                retained.append(row)
                continue
            checked += 1
            try:
                info = self.request('GET', '/rest/db/file', params={'folder': folder, 'file': row['name']})
            except SyncthingError:
                retry.append(row)
                result['notices'].append({'name': row['name'], 'status': '接收结果暂未能核对，待处理清单仍可查看'})
                continue
            global_file = info.get('global') or {}
            local_file = info.get('local') or {}
            version = global_file.get('version')
            if version != row['version'] or global_file.get('deleted') or global_file.get('invalid'):
                continue
            full_copy = any(a.get('id') == row['device'] and a.get('fromTemporary') is False
                            for a in (info.get('availability') or []))
            if (full_copy and local_file.get('version') == version and
                    not local_file.get('invalid') and not local_file.get('deleted')):
                from datetime import datetime, timezone
                result['uploads'].append({'name': row['name'], 'status': '已确认对端接收',
                                          'time': datetime.now(timezone.utc).isoformat()})
            else:
                # Retry later; disappearance from a paginated queue alone proves nothing.
                retry.append(row)
        result['tracked'] = (list(pending.values()) + retained + retry)[-120:]
        return result

    def ignores(self, folder):
        value = self.request('GET', '/rest/db/ignores', params={'folder': folder})
        if isinstance(value, dict) and value.get('ignore') is None and not value.get('error'):
            return []
        if not isinstance(value, dict) or not isinstance(value.get('ignore'), list):
            raise SyncthingError('选择规则格式不受支持')
        return value['ignore']

    def save_selection(self, folder, rules, expected):
        # The tree is available only while running. Pause before publication
        # and leave it paused even if publication fails.
        current = next((row for row in self.folders() if row['id'] == folder), None)
        if current is None:
            raise SyncthingError('同步任务已不存在，请重新连接')
        if self.ignores(folder) != expected:
            raise SyncthingError('选择规则已被其他窗口修改，请重新连接')
        if current.get('paused') is not True:
            from urllib.parse import quote
            self.request('PATCH', '/rest/config/folders/' + quote(folder, safe=''), json={'paused': True})
        current = next((row for row in self.folders() if row['id'] == folder), None)
        if current is None or current.get('paused') is not True or self.ignores(folder) != expected:
            raise SyncthingError('任务暂停或规则核对未通过，请重新连接')
        result = self.request('POST', '/rest/db/ignores', params={'folder': folder},
                              json={'ignore': rules})
        if not isinstance(result, dict) or result.get('error') or result.get('ignore') != rules:
            raise SyncthingError('规则保存结果需要核对，请保持文件夹暂停')
        return result
