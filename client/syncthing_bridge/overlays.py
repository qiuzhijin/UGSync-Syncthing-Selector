"""Conservative Explorer states. Only this worker talks to Syncthing."""
import ctypes
from datetime import datetime
import hashlib
import os
from pathlib import Path
import threading
import time

from syncthing_bridge.api import Syncthing, SyncthingError
from syncthing_bridge.selection import DIRECT_FILES_MARKER, literal, parse_document


def selection_excludes(relative, rules, *, directory=False):
    """Only interpret our canonical choices, leaving foreign patterns to the engine."""
    if relative == '.':
        return False
    try:
        base, entire, choices, escape = parse_document(rules)
    except ValueError:
        return False
    relative = relative.casefold()
    choices = {name.casefold(): selected for name, selected in choices.items()}
    if directory and any(selected and name.startswith(relative + '/')
                         for name, selected in choices.items()):
        return False
    ancestors = [name for name in choices if relative == name or relative.startswith(name + '/')]
    ancestor = max(ancestors, key=len) if ancestors else None
    selected = choices[ancestor] if ancestor else entire
    if not selected and not directory:
        parent = relative.rpartition('/')[0]
        helper = '!/' + (literal(parent, escape) + '/' if parent else '') + '*'
        ordered = [line.casefold() for line in rules[len(base):]]
        if DIRECT_FILES_MARKER.casefold() in ordered:
            start = ordered.index(DIRECT_FILES_MARKER.casefold())
            stop = ordered.index(DIRECT_FILES_MARKER.casefold(), start + 1)
            return '!/' + literal(relative, escape) not in ordered[start:stop]
        if helper in ordered:
            if ancestor is None or ordered.index(helper) < ordered.index('/' + literal(ancestor, escape) + '/**'):
                return False
    return not selected


def selection_partial(relative, rules):
    """Whether a directory is only partly covered by canonical choices."""
    try:
        _, entire, choices, _ = parse_document(rules)
    except ValueError:
        return False
    relative = '' if relative == '.' else relative.casefold()
    choices = {name.casefold(): selected for name, selected in choices.items()}
    ancestors = [name for name in choices if relative == name or relative.startswith(name + '/')]
    selected = choices[max(ancestors, key=len)] if ancestors else entire
    prefix = relative + '/' if relative else ''
    return any(name.startswith(prefix) and choice != selected for name, choice in choices.items())


def _pending_entries(api, route, params, sections, count):
    if type(count) is not int or count < 0 or count >= 1024:
        return None
    if count == 0:
        return []
    entries = {}
    for page in range(1, 5):
        response = api.request('GET', route, params={**params, 'page': page, 'perpage': 256})
        rows = [item for section in sections for item in (response[section] or [])]
        for row in rows:
            path = row['name'].replace('\\', '/').casefold()
            if not path or path in entries:
                return None
            entries[path] = row.get('type') == 'FILE_INFO_TYPE_DIRECTORY'
        if len(rows) < 256:
            return list(entries.items()) if len(entries) >= count else None
    return None


def _pending_for_directories(api, folder, peers):
    status = api.status(folder)
    if status.get('state') not in ('idle', 'syncing') or status.get('errors') or status.get('pullErrors'):
        return None
    params = {'folder': folder}
    downloads = _pending_entries(api, '/rest/db/need', params,
                                 ('progress', 'queued', 'rest'), status.get('needTotalItems'))
    if downloads is None:
        return None
    uploads = []
    for peer in peers:
        remote = {**params, 'device': peer}
        completion = api.request('GET', '/rest/db/completion', params=remote)
        if completion.get('remoteState') != 'valid':
            return None
        counts = (completion.get('needItems'), completion.get('needDeletes'))
        if any(type(count) is not int or count < 0 for count in counts):
            return None
        entries = _pending_entries(api, '/rest/db/remoteneed', remote, ('files',), sum(counts))
        if entries is None:
            return None
        uploads.extend(entries)
    return downloads, uploads


def file_state(info, peers, size, modified):
    local, newest = info.get('local') or {}, info.get('global') or {}
    if local.get('ignored'):
        return 'R'
    if not local or local.get('deleted') or local.get('invalid'):
        return None
    if not peers:
        return None
    try:
        stamp = datetime.fromisoformat(local['modified'].replace('Z', '+00:00')).timestamp()
    except (KeyError, ValueError):
        return None
    if local.get('size') != size or abs(stamp - modified) > .001:
        return 'B'
    if not local.get('version') or local['version'] != newest.get('version'):
        return 'B'
    if newest.get('deleted') or newest.get('invalid'):
        return 'B'
    full = {a.get('id') for a in (info.get('availability') or []) if a.get('fromTemporary') is False}
    return 'G' if set(peers) <= full else 'B'


def cache_key(path):
    value = ctypes.create_unicode_buffer(os.path.normpath(os.path.abspath(path)))
    ctypes.windll.user32.CharLowerBuffW(value, len(value.value))
    return hashlib.sha256(value.value.encode('utf-16-le')).hexdigest()


class OverlayPublisher:
    def __init__(self, endpoint, key, *, cache=None):
        self.endpoint, self.key = endpoint, key
        self.cache = Path(cache or Path(os.environ['LOCALAPPDATA']) / 'UGSync/overlays')
        self.stop_event = threading.Event()
        self.records = {}
        self.roots = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, name='Explorer-status', daemon=True)

    def start(self):
        for name in ('cache', 'requests'):
            (self.cache / name).mkdir(parents=True, exist_ok=True)
        for path in (self.cache / 'cache').glob('*.txt'):
            path.unlink(missing_ok=True)
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        self.thread.start()

    def stop(self, *, stopped=False):
        self.stop_event.set()
        with self.lock:
            for path, record in list(self.records.items()):
                state, stamp, size = record
                self.write(path, 'R' if stopped else None, stamp, size)
                self.notify(path)

    def write(self, path, state, stamp, size):
        destination = self.cache / 'cache' / (cache_key(path) + '.txt')
        if state is None:
            destination.unlink(missing_ok=True)
            return
        ttl = 30 * 86400 if state == 'R' and self.stop_event.is_set() else 20
        expires = int((time.time() + ttl + 11644473600) * 10_000_000)
        temporary = destination.with_suffix('.tmp')
        temporary.write_text('U1 %s %s %s %s' % (state, expires, stamp, size), encoding='ascii')
        os.replace(temporary, destination)

    def notify(self, path):
        # Queue a lightweight shell refresh; do not restart Explorer.
        ctypes.windll.shell32.SHChangeNotify(0x2000, 0x2005, ctypes.c_wchar_p(path), None)

    def notify_directory(self, path):
        ctypes.windll.shell32.SHChangeNotify(0x1000, 0x2005, ctypes.c_wchar_p(path), None)

    def publish_roots(self, roots):
        if len('\n'.join(roots)) > 30000:
            roots = []
        if roots == self.roots:
            return
        previous = self.roots or []
        temporary = self.cache / 'roots.tmp'
        temporary.write_bytes('\n'.join(roots).encode('utf-16-le'))
        os.replace(temporary, self.cache / 'roots.txt')
        self.roots = list(roots)
        for root in dict.fromkeys(previous + roots):
            self.notify_directory(root)

    def invalidate(self, path=None):
        with self.lock:
            if self.stop_event.is_set():
                return
            for name, (state, stamp, size) in list(self.records.items()):
                if state and (path is None or name == path):
                    self.write(name, None, stamp, size)
                    self.records[name] = (None, stamp, size)
                    self.notify(name)

    def wait_for_work(self, *, wake_on_request):
        if not wake_on_request:
            self.stop_event.wait(2)
            return
        for _ in range(10):
            if self.stop_event.wait(.2):
                return
            try:
                with os.scandir(self.cache / 'requests') as requests:
                    if any(entry.name.lower().endswith('.txt') for entry in requests):
                        return
            except OSError:
                # A missing or unavailable request directory should not cause a busy loop.
                continue

    def run(self):
        api = Syncthing(self.endpoint, self.key)
        api.http.timeout = 3
        index = 0
        try:
            while not self.stop_event.is_set():
                wake_on_request = False
                try:
                    folders = api.folders()
                    for folder in folders[:16]:
                        try:
                            folder['_overlay_selection'] = api.ignores(folder['id'])
                        except Exception:
                            folder['_overlay_selection'] = None
                    roots = [os.path.abspath(os.path.expandvars(os.path.expanduser(f['path']))) for f in folders[:16]]
                    self.publish_roots(roots)
                    identity = api.request('GET', '/rest/system/status')['myID']
                    connections = api.request('GET', '/rest/system/connections').get('connections', {})
                    paths = []
                    for request in list((self.cache / 'requests').glob('*.txt'))[:32]:
                        try:
                            if request.is_symlink() or request.stat().st_size > 8192:
                                continue
                            path = request.read_bytes().decode('utf-16-le')
                            if len(path) <= 4096 and cache_key(path) == request.stem:
                                paths.append(path)
                        finally:
                            request.unlink(missing_ok=True)
                    requested = set(paths)
                    with self.lock:
                        known = list(self.records)
                    if known:
                        paths += (known + known)[index:index + 16]
                        index = (index + 16) % len(known)
                    changed_directories = set()
                    pending_by_folder = {}
                    for path in dict.fromkeys(paths):
                        if self.stop_event.is_set():
                            break
                        try:
                            changed = self.inspect(api, folders, connections, identity, path,
                                                   requested=path in requested, pending_by_folder=pending_by_folder)
                            if changed:
                                changed_directories.add(os.path.dirname(path))
                        except Exception:
                            self.invalidate(path)
                    for directory in changed_directories:
                        self.notify_directory(directory)
                    wake_on_request = True
                except Exception:
                    # Cached green expires. An unreachable engine is not proof of pause.
                    self.invalidate()
                self.wait_for_work(wake_on_request=wake_on_request)
        finally:
            api.close()

    def inspect(self, api, folders, connections, identity, path, *, requested=False, pending_by_folder=None):
        path = os.path.abspath(path)
        matches = []
        for folder in folders:
            root = os.path.abspath(os.path.expandvars(os.path.expanduser(folder['path'])))
            try:
                if os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root):
                    matches.append((folder, root))
            except ValueError:
                pass
        if len(matches) != 1 or os.path.islink(path):
            return
        folder, root = matches[0]
        relative = os.path.relpath(path, root).replace('\\', '/')
        if any(p.startswith('.st') or p == '.ugsync-protected' for p in relative.split('/')):
            return
        try:
            before = os.stat(path)
            stamp = (before.st_mtime_ns // 100) + 116444736000000000
            peers = [d['deviceID'] for d in folder.get('devices', []) if d['deviceID'] != identity]
            rules = folder.get('_overlay_selection')
            if rules is not None and selection_excludes(relative, rules, directory=os.path.isdir(path)):
                state = 'R'
            elif folder.get('paused'):
                with self.lock:
                    known = path in self.records
                if not known:
                    return
                state = 'R'
            elif os.path.isdir(path):
                partial = rules is not None and selection_partial(relative, rules)
                state = 'B' if partial else None
                if peers and all(connections.get(p, {}).get('connected') for p in peers):
                    local = None
                    if not partial and relative != '.':
                        info = api.request('GET', '/rest/db/file', params={'folder': folder['id'], 'file': relative})
                        local = info.get('local') or {}
                    if local and local.get('ignored'):
                        state = 'R'
                    elif local is None or (local.get('name') and not local.get('deleted') and not local.get('invalid')):
                        state = self.directory_state(api, folder['id'], peers, relative, rules, pending_by_folder)
            elif not peers or any(not connections.get(p, {}).get('connected') for p in peers):
                state = None
            else:
                info = api.request('GET', '/rest/db/file', params={'folder': folder['id'], 'file': relative})
                state = file_state(info, peers, before.st_size, before.st_mtime)
            after = os.stat(path)
            if state != 'R' and (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                state = 'B'
            with self.lock:
                if self.stop_event.is_set():
                    return
                old = self.records.get(path)
                self.write(path, state, stamp, before.st_size)
                if state:
                    if len(self.records) >= 1024 and path not in self.records:
                        self.records.pop(next(iter(self.records)))
                    self.records[path] = (state, stamp, before.st_size)
                else:
                    self.records.pop(path, None)
            if old != self.records.get(path) or (requested and state):
                self.notify(path)
                return True
        except OSError:
            self.invalidate(path)

    def directory_state(self, api, folder, peers, relative, rules, pending_by_folder):
        if pending_by_folder is None:
            pending_by_folder = {}
        if folder not in pending_by_folder:
            pending_by_folder[folder] = None
            try:
                pending_by_folder[folder] = _pending_for_directories(api, folder, peers)
            except SyncthingError:
                pass
        pending = pending_by_folder[folder]
        if pending is None:
            return 'B'
        path = '' if relative == '.' else relative.casefold()
        prefix = path + '/' if path else ''
        downloads, uploads = pending
        waiting = any(name == path or name.startswith(prefix) for name, _ in downloads)
        waiting |= any((name == path or name.startswith(prefix)) and
                       (rules is None or not selection_excludes(name, rules, directory=directory))
                       for name, directory in uploads)
        return 'B' if waiting else 'G'
