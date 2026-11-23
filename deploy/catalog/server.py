"""Authenticated one-level directory listings for one NAS share."""
import hmac
import json
import os
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


MAX_ENTRIES = 10000


def list_children(root, prefix, directories_only):
    parts = prefix.split('/') if prefix else []
    if len(prefix) > 4096 or any(part in ('', '.', '..') or '\\' in part or ':' in part or
                                 any(ord(c) < 32 for c in part) for part in parts):
        raise ValueError('invalid relative path')

    def entries(directory):
        rows = []
        with os.scandir(directory) as children:
            for child in children:
                name = child.name
                if (name.casefold() in ('.stfolder', '.stignore', '.stversions') or
                        name.casefold().startswith(('.syncthing.', '~syncthing~')) or
                        name != name.strip() or '\\' in name or ':' in name or
                        any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in name)):
                    continue
                if child.is_dir(follow_symlinks=False) and not (
                        os.name == 'nt' and os.path.isjunction(child.path)):
                    kind = 'DIRECTORY'
                elif not directories_only and child.is_file(follow_symlinks=False):
                    kind = 'FILE'
                else:
                    continue
                rows.append({'name': name, 'type': 'FILE_INFO_TYPE_' + kind})
                if len(rows) > MAX_ENTRIES:
                    raise OverflowError()
        return sorted(rows, key=lambda row: row['name'].casefold())

    if os.name == 'nt':
        # Loopback testing on Windows. The NAS container uses descriptor-relative opens.
        directory = Path(root)
        for part in parts:
            directory /= part
            if directory.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise FileNotFoundError()
        return entries(directory)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return entries(descriptor)
    finally:
        os.close(descriptor)


class CatalogHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, format, *args):
        # Request targets contain private folder names.
        pass

    def do_GET(self):
        expected = ('Bearer ' + self.server.token).encode('ascii')
        if not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), expected):
            self.send_error(401)
            return
        try:
            url = urlsplit(self.path)
            args = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
            if url.path != '/v1/list' or args.get('folder') != [self.server.folder]:
                self.send_error(404)
                return
            if set(args) != {'folder', 'prefix', 'directories_only'} or len(args['prefix']) != 1 or (
                    args['directories_only'] not in (['0'], ['1'])):
                raise ValueError()
            prefix = args['prefix'][0]
            rows = list_children(self.server.root, prefix, args['directories_only'] == ['1'])
            payload = json.dumps({'folder': self.server.folder, 'prefix': prefix, 'entries': rows},
                                 ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if len(payload) > 2 << 20:
                raise OverflowError()
        except ValueError:
            self.send_error(400)
            return
        except OverflowError:
            self.send_error(413)
            return
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            self.send_error(404)
            return
        except OSError:
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)


def create_server(address, root, folder, token):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not folder or len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError('configure a directory, a folder ID and a random ASCII token of at least 32 characters')
    if os.name == 'nt' and address[0] != '127.0.0.1':
        raise ValueError('Windows testing requires a loopback listener')
    server = ThreadingHTTPServer(address, CatalogHandler)
    server.root, server.folder, server.token = str(root), folder, token
    return server


if __name__ == '__main__':
    with create_server(
            (os.environ.get('UGSYNC_CATALOG_BIND', '127.0.0.1'), 8788),
            os.environ['UGSYNC_CATALOG_ROOT'], os.environ['UGSYNC_CATALOG_FOLDER'],
            os.environ['UGSYNC_CATALOG_TOKEN']) as catalog:
        catalog.serve_forever()
