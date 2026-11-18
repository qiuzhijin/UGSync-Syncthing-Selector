import hashlib
import io

import pytest

from syncthing_bridge.release import select_release, copy_verified


def release(tag, *, prerelease=True, draft=False):
    filename = f'UGSync-Setup-{tag}-x64.exe'
    return {
        'tag_name': tag, 'draft': draft, 'prerelease': prerelease,
        'assets': [{
            'name': filename, 'state': 'uploaded', 'size': 3,
            'digest': 'sha256:' + hashlib.sha256(b'abc').hexdigest(),
            'browser_download_url': f'https://github.com/example/UGSync/releases/download/{tag}/{filename}',
        }],
    }


def test_selects_newest_eligible_preview_numerically():
    update = select_release(
        [release('v1.0.0-preview.9'), release('v1.0.0-preview.11'),
         release('v1.0.0-preview.12', draft=True)], 'example/UGSync', 'v1.0.0-preview.8',
        previews=True,
    )
    assert update.tag == 'v1.0.0-preview.11'


def test_rejects_unexpected_download_origin_and_preview_on_stable_channel():
    stable = release('v1.0.0', prerelease=False)
    stable['assets'][0]['browser_download_url'] = 'https://example.org/setup.exe'
    assert select_release([stable, release('v2.0.0-preview.1')], 'example/UGSync',
                          'v0.1.0', previews=False) is None


def test_rejects_downgrade():
    assert select_release([release('v1.0.0', prerelease=False)], 'example/UGSync',
                          'v1.1.0', previews=True) is None


def test_download_checks_exact_size_and_digest():
    update = select_release([release('v1.0.0', prerelease=False)],
                            'example/UGSync', 'v0.1.0', previews=False)
    target = io.BytesIO()
    copy_verified([b'a', b'bc'], target, update)
    assert target.getvalue() == b'abc'
    for body in (b'ab', b'abcd', b'bad'):
        with pytest.raises(ValueError):
            copy_verified([body], io.BytesIO(), update)
