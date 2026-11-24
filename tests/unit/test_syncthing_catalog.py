from threading import Thread

import httpx
import pytest

from deploy.catalog.server import create_server, list_children
from syncthing_bridge.api import SyncthingError
from syncthing_bridge.catalog import Catalog


def test_catalog_reads_and_caches_one_level_with_authentication(tmp_path):
    root = tmp_path / 'share'
    (root / '学习' / 'Deep').mkdir(parents=True)
    (root / '学习' / 'Deep' / 'hidden.txt').touch()
    (root / 'root.txt').touch()
    (root / '.stfolder').mkdir()
    token = 'synthetic-test-token-for-catalog-only'
    with create_server(('127.0.0.1', 0), root, 'drive', token) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            address = f'http://127.0.0.1:{server.server_port}'
            source = Catalog(address, token, tmp_path / 'cache.sqlite')
            assert source.cached('drive', '') is None
            rows = source.listings('drive', ('',), directories_only=True)
            assert rows[''] == [{'name': '学习', 'type': 'FILE_INFO_TYPE_DIRECTORY'}]
            assert Catalog(address, token, source.cache).cached('drive', '') == rows['']
            names = source.listings('drive', ('', '学习'), directories_only=False)
            assert {row['name'] for row in names['']} == {'学习', 'root.txt'}
            assert [row['name'] for row in names['学习']] == ['Deep']
            assert source.cached('drive', '') == rows['']
            with httpx.Client(base_url=address, trust_env=False) as http:
                assert http.get('/v1/list').status_code == 401
                http.headers['Authorization'] = 'Bearer ' + token
                response = http.get('/v1/list', params={'folder': 'drive', 'prefix': '../',
                                                        'directories_only': '0'})
                assert response.status_code == 400
            with pytest.raises(SyncthingError):
                source.listings('other-drive', ('',), directories_only=True)
            another = Catalog(address, token + '-other', source.cache)
            assert another.cached('drive', '') is None
        finally:
            server.shutdown()
            worker.join()


def test_catalog_rejects_links_to_paths_outside_the_share(tmp_path):
    root = tmp_path / 'share'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (root / 'link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating directory symlinks requires Windows Developer Mode or administrator rights')
    assert list_children(root, '', False) == []
    with pytest.raises(OSError):
        list_children(root, 'link', False)


def test_catalog_requires_https_outside_loopback(tmp_path):
    with pytest.raises(ValueError, match='HTTPS'):
        Catalog('http://192.0.2.1:8788', 'synthetic-test-token-for-catalog-only', tmp_path / 'cache.sqlite')


@pytest.mark.parametrize('rows', [{}, [None], [{'name': '../bad', 'type': 'FILE_INFO_TYPE_DIRECTORY'}],
                                  [{'name': 'file', 'type': 'FILE_INFO_TYPE_FILE'}]])
def test_invalid_cached_listing_is_a_cache_miss(tmp_path, rows):
    source = Catalog('http://127.0.0.1:8788', 'synthetic-test-token-for-catalog-only', tmp_path / 'cache.sqlite')
    source.save_cache('drive', '', rows)
    assert source.cached('drive', '') is None
