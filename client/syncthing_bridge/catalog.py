"""Optional HTTPS directory source and bounded local display cache."""
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from time import time
from urllib.parse import urlsplit

import httpx

from syncthing_bridge.api import SyncthingError
from syncthing_bridge.platform_paths import app_data_directory
from syncthing_bridge.selection import normalize


MAX_RESPONSE = 2 << 20
ENTRY_TYPES = {'FILE_INFO_TYPE_DIRECTORY', 'FILE_INFO_TYPE_FILE'}


class Catalog:
    def __init__(self, endpoint, token, cache):
        address = urlsplit(endpoint)
        loopback = address.hostname in ('127.0.0.1', '::1', 'localhost')
        if (not (address.scheme == 'https' or (address.scheme == 'http' and loopback))
                or not address.hostname or address.username or address.password or address.query
                or address.fragment or address.path not in ('', '/')):
            raise ValueError('目录服务需要 HTTPS 地址')
        if len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError('目录服务令牌无效')
        self.endpoint = endpoint.rstrip('/')
        self.token = token
        self.cache = Path(cache)

    @classmethod
    def from_environment(cls):
        endpoint = os.environ.get('UGSYNC_CATALOG_URL', '').strip()
        if not endpoint:
            return None
        return cls(endpoint, os.environ.get('UGSYNC_CATALOG_TOKEN', ''), cls.cache_path())

    @staticmethod
    def cache_path():
        return app_data_directory() / 'catalog-cache.sqlite'

    def cache_key(self, folder, prefix):
        data = json.dumps([self.endpoint, self.token, folder, prefix])
        return sha256(data.encode('utf-8')).hexdigest()

    def cached(self, folder, prefix):
        if not self.cache.is_file():
            return None
        try:
            with closing(sqlite3.connect(self.cache, timeout=.1)) as db:
                row = db.execute('SELECT entries FROM listings WHERE key=?',
                                 (self.cache_key(folder, prefix),)).fetchone()
            return json.loads(row[0]) if row is not None else None
        except (sqlite3.Error, ValueError):
            return None

    def save_cache(self, folder, prefix, rows):
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.cache, timeout=.1)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS listings '
                           '(key TEXT PRIMARY KEY, entries TEXT NOT NULL, updated REAL NOT NULL)')
                db.execute('INSERT OR REPLACE INTO listings VALUES (?, ?, ?)',
                           (self.cache_key(folder, prefix), json.dumps(rows, ensure_ascii=False), time()))
                db.execute('DELETE FROM listings WHERE key IN '
                           '(SELECT key FROM listings ORDER BY updated DESC, key LIMIT -1 OFFSET 64)')
        except (OSError, sqlite3.Error):
            pass

    def listings(self, folder, prefixes, *, directories_only):
        with httpx.Client(base_url=self.endpoint, headers={'Authorization': 'Bearer ' + self.token},
                          timeout=httpx.Timeout(8, connect=4), follow_redirects=False, trust_env=False) as http:
            return {prefix: self.read(http, folder, prefix, directories_only) for prefix in prefixes}

    def read(self, http, folder, prefix, directories_only):
        try:
            with http.stream('GET', '/v1/list', params={'folder': folder, 'prefix': prefix,
                             'directories_only': int(directories_only)}) as response:
                response.raise_for_status()
                payload = bytearray()
                for chunk in response.iter_bytes():
                    if len(payload) + len(chunk) > MAX_RESPONSE:
                        raise ValueError()
                    payload.extend(chunk)
            data = json.loads(payload)
            if data['folder'] != folder or data['prefix'] != prefix or not isinstance(data['entries'], list):
                raise ValueError()
            rows = data['entries']
            for row in rows:
                name, kind = row['name'], row['type']
                if ('/' in normalize(name) or ':' in name or kind not in ENTRY_TYPES or
                        (directories_only and kind != 'FILE_INFO_TYPE_DIRECTORY')):
                    raise ValueError()
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise SyncthingError('NAS 目录服务暂时无法读取') from None
        if directories_only:
            self.save_cache(folder, prefix, rows)
        return rows
