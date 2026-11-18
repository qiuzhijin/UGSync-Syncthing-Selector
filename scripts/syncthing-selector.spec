from pathlib import Path
import os
import sys

project = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(project))
from scripts.windows_metadata import validate_system_icu, system_icu_binaries

validate_system_icu()
update_channel_file = os.environ.get('UGSYNC_UPDATE_CHANNEL_FILE')
update_channel_data = []
if update_channel_file:
    channel = Path(update_channel_file).resolve(strict=True)
    if channel.name != 'update-channel.json':
        raise ValueError('UGSYNC_UPDATE_CHANNEL_FILE must name update-channel.json')
    update_channel_data.append((str(channel), '.'))
a = Analysis([str(project/'client/syncthing_bridge/__main__.py')],
             pathex=[str(project/'client')], binaries=[],
             datas=[(str(project/'.tools/syncthing-2.1.7/syncthing-windows-amd64-v2.1.7/syncthing.exe'), 'engine'),
                    (str(project/'.tools/syncthing-2.1.7/syncthing-windows-amd64-v2.1.7/LICENSE.txt'), 'engine'),
                    (str(project/'dist/explorer-overlays/UGSyncOverlay.dll'), 'overlays'),
                    (str(project/'dist/explorer-overlays/blue.ico'), 'overlays'),
                    (str(project/'dist/explorer-overlays/green.ico'), 'overlays'),
                    (str(project/'dist/explorer-overlays/red.ico'), 'overlays'),
                    (str(project/'dist/explorer-overlays/overlay-manifest.json'), 'overlays'),
                    (str(project/'scripts/install_overlays.ps1'), 'overlays')] + update_channel_data, hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
a.binaries = system_icu_binaries(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='UGSync-Syncthing-Selector',
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='UGSync-Syncthing-Selector')
