"""Release selection and integrity checks for Windows installers."""
from dataclasses import dataclass
import hashlib
import hmac
import re

import httpx


_VERSION = re.compile(r'v(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})(?:-preview\.([1-9][0-9]{0,8}))?')
MAX_INSTALLER_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    size: int
    sha256: str


def _version(tag):
    match = _VERSION.fullmatch(tag) if isinstance(tag, str) else None
    if not match:
        return None
    major, minor, patch, preview = match.groups()
    return int(major), int(minor), int(patch), preview is None, int(preview or 0)


def select_release(releases, repository, installed, *, previews):
    current = _version(installed)
    if current is None or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid release configuration')
    newest = None
    for item in releases:
        tag = item.get('tag_name')
        version = _version(tag)
        if (version is None or version <= current or item.get('draft') is not False
                or item.get('prerelease') is not (not version[3])
                or (not previews and not version[3])):
            continue
        name = f'UGSync-Setup-{tag}-x64.exe'
        url = f'https://github.com/{repository}/releases/download/{tag}/{name}'
        for asset in item.get('assets', []):
            size = asset.get('size')
            digest = asset.get('digest')
            if (asset.get('name') == name and asset.get('state') == 'uploaded'
                    and asset.get('browser_download_url') == url
                    and type(size) is int and 0 < size <= MAX_INSTALLER_BYTES
                    and isinstance(digest, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', digest)):
                newest = Release(tag, url, size, digest[7:])
                current = version
                break
    return newest


def check_release(repository, installed, *, previews):
    if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid release repository')
    response = httpx.get(f'https://api.github.com/repos/{repository}/releases',
                         params={'per_page': 100}, timeout=10,
                         headers={'Accept': 'application/vnd.github+json'})
    response.raise_for_status()
    return select_release(response.json(), repository, installed, previews=previews)


def copy_verified(chunks, output, release):
    measured = hashlib.sha256()
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > release.size:
            raise ValueError('Installer exceeds expected size')
        measured.update(chunk)
        output.write(chunk)
    if total != release.size or not hmac.compare_digest(measured.hexdigest(), release.sha256):
        raise ValueError('Installer integrity check failed')
