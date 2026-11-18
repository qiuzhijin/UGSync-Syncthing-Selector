from base64 import b64encode

import pytest

from syncthing_bridge.winsparkle import read_channel


def test_channel_requires_https_and_a_raw_32_byte_key():
    channel = {
        'appcast': 'https://example.invalid/preview.xml',
        'build': 25,
        'public_key': b64encode(bytes(range(32))).decode('ascii'),
    }
    assert read_channel(channel) == (b'https://example.invalid/preview.xml', '25',
                                     channel['public_key'].encode('ascii'))
    for invalid in ({'appcast': 'http://example.invalid/'}, {'public_key': 'broken'},
                    {'build': True}, {'appcast': 'https://user@example.invalid/'}):
        with pytest.raises(ValueError):
            read_channel(channel | invalid)
