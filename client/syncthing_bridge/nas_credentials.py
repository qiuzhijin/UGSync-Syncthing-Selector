"""Local, non-synced persistence for optional NAS activity credentials."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


class NasCredentialError(RuntimeError):
    pass


class NasCredentialStore:
    """Keep NAS API keys in the current user's local application data folder."""

    def __init__(self, *, path=None):
        if path is None:
            local_app_data = os.environ.get('LOCALAPPDATA')
            if not local_app_data:
                raise NasCredentialError('无法确定本机配置目录')
            path = Path(local_app_data) / 'UGSync' / 'syncthing-nas.json'
        self.path = Path(path)

    @staticmethod
    def _endpoint_key(endpoint):
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ('', '/')):
            raise NasCredentialError('NAS Syncthing 地址无效')
        normalized = parsed.scheme + '://' + parsed.netloc.lower()
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    def _read(self):
        try:
            with self.path.open('r', encoding='utf-8') as source:
                value = json.load(source)
            if not isinstance(value, dict) or value.get('schema') != 1:
                raise ValueError()
            credentials = value.get('credentials')
            if not isinstance(credentials, dict) or any(not isinstance(key, str) or not isinstance(item, str)
                                                       for key, item in credentials.items()):
                raise ValueError()
            return credentials
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise NasCredentialError('NAS 本机凭据文件无法读取') from None

    def _write(self, credentials):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + '.new')
        payload = json.dumps({'schema': 1, 'credentials': credentials}, separators=(',', ':'))
        try:
            with temporary.open('w', encoding='utf-8', newline='\n') as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise NasCredentialError('NAS 本机凭据文件无法保存') from None

    def load(self, endpoint):
        if not endpoint:
            return None
        return self._read().get(self._endpoint_key(endpoint))

    def save(self, endpoint, key):
        if key == '':
            self.delete(endpoint)
            return
        if not isinstance(key, str) or any(ord(character) < 33 for character in key):
            raise NasCredentialError('NAS API 密钥无效')
        credentials = self._read()
        credentials[self._endpoint_key(endpoint)] = key
        self._write(credentials)

    def delete(self, endpoint):
        if not endpoint:
            return
        credentials = self._read()
        if credentials.pop(self._endpoint_key(endpoint), None) is not None:
            self._write(credentials)
