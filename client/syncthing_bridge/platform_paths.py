"""Per-user locations for the selector and the local Syncthing engine."""
import os
from pathlib import Path
import sys


def xdg_directory(variable, default):
    value = Path(os.environ.get(variable, ''))
    return value if value.is_absolute() else Path.home() / default


def app_data_directory():
    if sys.platform == 'win32':
        return Path(os.environ['LOCALAPPDATA']) / 'UGSync'
    return xdg_directory('XDG_CONFIG_HOME', '.config') / 'ugsync'


def syncthing_config_path():
    if sys.platform == 'win32':
        return Path(os.environ['LOCALAPPDATA']) / 'Syncthing' / 'config.xml'
    explicit = os.environ.get('STCONFDIR') or os.environ.get('STHOMEDIR')
    if explicit:
        return Path(explicit) / 'config.xml'
    current = xdg_directory('XDG_STATE_HOME', '.local/state') / 'syncthing' / 'config.xml'
    legacy = xdg_directory('XDG_CONFIG_HOME', '.config') / 'syncthing' / 'config.xml'
    return legacy if not current.is_file() and legacy.is_file() else current
