"""Bounded diagnostics that can be shared without sync data or credentials."""
from contextlib import contextmanager
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import time

from syncthing_bridge.platform_paths import app_data_directory, xdg_directory


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
logger.propagate = False


@contextmanager
def error_log(directory, *, fallback=None, build=None):
    handler = None
    path = None
    for candidate in dict.fromkeys((directory, fallback)):
        if candidate is None:
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            target = candidate / 'ugsync-errors.log'
            if any(p.is_symlink() or p.is_junction() for p in (candidate, target)):
                continue
            handler = RotatingFileHandler(target, maxBytes=1 << 20, backupCount=2, encoding='utf-8')
        except OSError:
            continue
        path = target
        stamp = logging.Formatter(f'%(asctime)sZ build={build or "source"} %(message)s', '%Y-%m-%dT%H:%M:%S')
        stamp.converter = time.gmtime
        handler.setFormatter(stamp)
        logger.addHandler(handler)
        break
    try:
        yield path
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()


def application_log():
    windows = sys.platform == 'win32'
    install = Path(sys.executable).parent / 'logs' if windows and getattr(sys, 'frozen', False) else None
    try:
        fallback = (app_data_directory() if windows else xdg_directory('XDG_STATE_HOME', '.local/state') / 'ugsync') / 'logs'
    except KeyError:
        fallback = None
    build = None
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        try:
            value = json.loads((Path(bundle) / 'winsparkle-channel.json').read_text(encoding='utf-8-sig'))['build']
            if type(value) is int:
                build = value
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return error_log(install, fallback=fallback, build=build)


def report_exception(operation, error):
    logger.error('operation=%s error=%s', operation, type(error).__name__)
