"""Build-only metadata and integrity checks; never enumerate user task data."""

import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import stat
import sys


def validate_system_icu():
    import ctypes
    import pefile
    directory = ctypes.create_unicode_buffer(32768)
    size = ctypes.WinDLL('kernel32', use_last_error=True).GetSystemDirectoryW(directory, len(directory))
    if not 0 < size < len(directory):raise ValueError('Windows system library directory unavailable')
    source = metadata.distribution('PySide6_Essentials').locate_file('PySide6/Qt6Core.dll')
    with pefile.PE(str(source), fast_load=False) as qt:
        imports = {row.name for group in qt.DIRECTORY_ENTRY_IMPORT if group.dll.lower() == b'icuuc.dll' for row in group.imports}
    with pefile.PE(str(Path(directory.value)/'icuuc.dll'), fast_load=False) as system:
        exports = {row.name for row in system.DIRECTORY_ENTRY_EXPORT.symbols}
    if not imports or not imports <= exports:raise ValueError('system ICU does not satisfy this Qt build')
    return len(imports)


def system_icu_binaries(binaries):
    # Qt's unversioned Windows ICU API must not be shadowed by an unrelated
    # application's versioned ICU found on the build machine's PATH.
    return [row for row in binaries if not re.fullmatch(r'icu(?:uc|dt[0-9]+)\.dll', Path(row[0]).name, re.I)]


def file_manifest(root):
    root = Path(root)
    result = []
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('links cannot be shipped')
        if not path.is_file():continue
        relative = path.relative_to(root).as_posix()
        if relative == 'bundle-manifest.json':continue
        if path.suffix.lower() in ('.sqlite', '.sqlite3', '.db', '.key') or path.name == '.env':
            raise ValueError('user state cannot be shipped')
        with path.open('rb') as stream:digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        result.append(dict(path=relative, size=info.st_size, sha256=digest))
    return result


def verify_manifest(root, expected):
    if file_manifest(root) != expected:raise ValueError('bundle content differs from manifest')


def build_metadata(root):
    from packaging.requirements import Requirement
    import pefile
    root = Path(root)
    exe = root/'UGSync.exe'
    with pefile.PE(str(exe), fast_load=True) as image:
        if image.FILE_HEADER.Machine != 0x8664:raise ValueError('Windows x64 executable required')
    if not list(root.rglob('qwindows.dll')):raise ValueError('native Qt Windows plugin required')
    pending = [('ugsync-client', frozenset({'ui'}))]
    visited = set();dependencies = {};notices = []
    while pending:
        name, extras = pending.pop()
        if (name, extras) in visited:continue
        visited.add((name, extras))
        distribution = metadata.distribution(name)
        canonical = distribution.metadata['Name']
        if canonical not in dependencies:
            dependencies[canonical] = distribution.version
            if canonical != 'ugsync-client':
                notices.append(canonical+' '+distribution.version+'\nDeclared license: '+str(
                    distribution.metadata.get('License-Expression') or distribution.metadata.get('License') or 'see package notices'))
                for file in distribution.files or ():
                    if Path(file).name.lower().startswith(('license', 'licence', 'copying', 'notice')):
                        license_file = Path(distribution.locate_file(file))
                        if license_file.is_file():notices.append(license_file.name+'\n'+license_file.read_text(encoding='utf-8', errors='replace'))
        for raw in distribution.requires or ():
            required = Requirement(raw)
            if required.marker is None or any(required.marker.evaluate({'extra': extra}) for extra in {'', *extras}):
                pending.append((required.name, frozenset(required.extras)))
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file():notices.append('Python license\n'+python_license.read_text(encoding='utf-8'))
    (root/'THIRD-PARTY-NOTICES.txt').write_text('\n\n'.join(notices), encoding='utf-8')
    manifest = dict(schema_version=1, channel='preview', platform='windows-amd64', signed=False,
                    version=metadata.version('ugsync-client'), python_version=sys.version.split()[0],
                    pyinstaller_version=metadata.version('pyinstaller'), dependencies=dependencies,
                    files=file_manifest(root))
    (root/'bundle-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    verify_manifest(root, manifest['files'])
    return manifest


if __name__ == '__main__':build_metadata(sys.argv[1])
