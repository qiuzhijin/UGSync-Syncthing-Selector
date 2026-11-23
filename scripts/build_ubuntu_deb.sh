#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 || ! $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo 'Usage: bash scripts/build_ubuntu_deb.sh VERSION OUTPUT_DIRECTORY' >&2
    exit 2
fi
version=$1
mkdir -p -- "$2"
output=$(realpath -- "$2")
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
source_root=$PWD
python=${PYTHON:-python3}
if [[ $(dpkg --print-architecture) != amd64 ]]; then
    echo 'This builder targets Ubuntu amd64.' >&2
    exit 2
fi
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
archive=syncthing-linux-amd64-v2.1.7.tar.gz
curl -fLsS --retry 3 --max-time 180 \
    -o "$tmp/$archive" "https://github.com/syncthing/syncthing/releases/download/v2.1.7/$archive"
(cd "$tmp" && printf '%s  %s\n' 2edc416e7ed136b7c858ed5f3f02a3b8316589605e54c380228ceea7acbf0142 "$archive" | sha256sum -c -)
tar -xzf "$tmp/$archive" -C "$tmp"
engine="$tmp/${archive%.tar.gz}"
"$python" -m PyInstaller --noconfirm --log-level WARN --onedir --windowed \
    --name ugsync-selector --paths "$source_root/client" --exclude-module PySide6.QtPdf --exclude-module PySide6.QtQuick \
    --add-binary "$engine/syncthing:engine" --specpath "$tmp" --workpath "$tmp/work" \
    --distpath "$tmp/dist" "$source_root/client/syncthing_bridge/__main__.py"

root="$tmp/package"
doc="$root/usr/share/doc/ugsync-selector"
mkdir -p "$root/DEBIAN" "$root/usr/lib" "$root/usr/bin" "$root/usr/share/applications" "$doc/licenses"
cp -a "$tmp/dist/ugsync-selector" "$root/usr/lib/"
ln -s ../lib/ugsync-selector/ugsync-selector "$root/usr/bin/ugsync-selector"
install -m 644 LICENSE "$doc/copyright"
install -m 644 "$engine/LICENSE.txt" "$doc/licenses/Syncthing.txt"
install -m 644 docs/ubuntu.zh-CN.md "$doc/ubuntu.zh-CN.md"
"$python" - "$doc/licenses" <<'PY'
from importlib.metadata import distribution
from pathlib import Path
from shutil import copyfile
import sys

destination = Path(sys.argv[1])
for name in ('PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'pyinstaller',
             'httpx', 'httpcore', 'anyio', 'certifi', 'h11', 'idna', 'typing_extensions'):
    package = distribution(name)
    for file in package.files or ():
        if '.dist-info/' in str(file) and file.name.lower().startswith(('license', 'copying', 'notice', 'lgpl', 'gpl')):
            copyfile(package.locate_file(file), destination / f'{name}-{file.name}')
PY
cat > "$root/DEBIAN/control" <<EOF
Package: ugsync-selector
Version: $version
Architecture: amd64
Maintainer: UGSync Contributors <ugsync@users.noreply.github.com>
Section: utils
Priority: optional
Depends: libc6 (>= 2.39), libstdc++6, libgcc-s1, libglib2.0-0t64, libdbus-1-3, libegl1, libgl1, libfontconfig1, libfreetype6, libsm6, libice6, libx11-6, libx11-xcb1, libxext6, libxrender1, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-sync1, libxcb-xfixes0, libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0
Recommends: fonts-noto-cjk
Description: Desktop selector for Syncthing folders
 Choose which NAS directories sync to this machine and view sync activity.
EOF
cat > "$root/usr/share/applications/ugsync-selector.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=UGSync Syncthing Selector
Name[zh_CN]=UGSync 文件夹同步
Exec=ugsync-selector
Icon=folder-sync
Terminal=false
Categories=Utility;FileTools;
EOF
package="ugsync-selector_${version}_amd64.deb"
dpkg-deb --root-owner-group --build "$root" "$output/$package"
(cd "$output" && sha256sum "$package" > "$package.sha256")
