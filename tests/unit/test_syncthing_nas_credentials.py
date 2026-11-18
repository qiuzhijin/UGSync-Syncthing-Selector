from syncthing_bridge.nas_credentials import NasCredentialStore


def test_nas_api_key_is_saved_per_endpoint_and_can_be_removed(tmp_path):
    store = NasCredentialStore(path=tmp_path / 'syncthing-nas.json')
    first = 'http://nas-a.invalid:8384'
    second = 'http://nas-b.invalid:8384'
    assert store.load(first) is None
    store.save(first, 'key-for-first')
    store.save(second, 'key-for-second')
    assert store.load(first) == 'key-for-first'
    assert store.load(second) == 'key-for-second'
    store.delete(first)
    assert store.load(first) is None
    assert store.load(second) == 'key-for-second'


def test_nas_credentials_do_not_store_empty_key(tmp_path):
    path = tmp_path / 'syncthing-nas.json'
    store = NasCredentialStore(path=path)
    endpoint = 'http://nas.invalid:8384'
    store.save(endpoint, 'key')
    store.save(endpoint, '')
    assert store.load(endpoint) is None
