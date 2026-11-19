"""Exercise ignore rules against an isolated official engine (no peers)."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

import httpx

from syncthing_bridge.selection import compile_document


def verify(executable, gui_exit=False):
    with tempfile.TemporaryDirectory(prefix='ugsync-syncthing-') as scratch:
        base = Path(scratch)
        home, data = base / 'config', base / 'files'
        home.mkdir()
        data.mkdir()
        expected = {'根目录.txt', '工作/正常.txt', '工作/缓存/直属.txt', '工作/缓存/保留[1]/需要.txt'}
        for name in sorted(expected | {'工作/忽略.tmp', '工作/缓存/未选/忽略.txt', '其他/忽略.txt'}):
            target = data / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('synthetic', encoding='utf-8')
        (data / '.stfolder').mkdir()
        (data / '.stignore').write_text('\n'.join(compile_document(
            ['*.tmp'], ['工作', '工作/缓存/保留[1]'], ['工作/缓存'], escape='|')) + '\n', encoding='utf-8')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.run([executable, 'generate', '--home', str(home)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        document = ET.parse(home / 'config.xml')
        root = document.getroot()
        for folder in root.findall('folder'):
            root.remove(folder)
        ET.SubElement(root, 'folder', {'id': 'selector-test', 'label': 'selector-test',
            'path': str(data), 'type': 'sendreceive', 'rescanIntervalS': '0',
            'fsWatcherEnabled': 'false', 'ignorePerms': 'true'})
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        gui = root.find('gui')
        gui.find('address').text = '127.0.0.1:' + str(port)
        key = gui.findtext('apikey')
        options = root.find('options')
        for name in ('globalAnnounceEnabled', 'localAnnounceEnabled', 'relaysEnabled', 'natEnabled', 'startBrowser'):
            element = options.find(name)
            if element is not None:
                element.text = 'false'
        for element in options.findall('listenAddress'):
            options.remove(element)
        ET.SubElement(options, 'listenAddress').text = 'tcp://127.0.0.1:0'
        document.write(home / 'config.xml', encoding='utf-8', xml_declaration=True)
        log = (base / 'engine.log').open('wb')
        process = subprocess.Popen([executable, 'serve', '--home', str(home), '--no-browser', '--no-restart', '--no-upgrade'],
                                   stdout=log, stderr=log, creationflags=flags)
        try:
            with httpx.Client(base_url='http://127.0.0.1:' + str(port),
                              headers={'X-API-Key': key}, timeout=2, trust_env=False) as client:
                deadline = time.monotonic() + 30
                value = {}
                while True:
                    try:
                        response = client.get('/rest/db/status', params={'folder': 'selector-test'})
                        value = response.json()
                        if response.status_code == 200 and value.get('state') == 'idle' and value.get('localFiles') == len(expected):
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError(f'isolated scan failed; exit={process.poll()}, '
                                           f'state={value.get("state")}, localFiles={value.get("localFiles")}')
                    time.sleep(.2)
                response = client.get('/rest/db/browse', params={'folder': 'selector-test'})
                response.raise_for_status()
                names = []
                def visit(rows, prefix=''):
                    for row in rows:
                        path = prefix + row['name']
                        if row['type'] == 'FILE_INFO_TYPE_FILE':
                            names.append(path)
                        visit(row.get('children', []), path + '/')
                visit(response.json())
                assert set(names) == expected, names
                if gui_exit:
                    from PySide6.QtWidgets import QApplication
                    import syncthing_bridge.__main__ as ui
                    app = QApplication.instance() or QApplication([])
                    original = ui.local_credentials
                    ui.local_credentials = lambda: ('http://127.0.0.1:' + str(port), key)
                    window = ui.Window()
                    exited = []
                    window.quit_interface = lambda: exited.append(True)
                    try:
                        window.exit_with_engine()
                        assert exited == [True]
                    finally:
                        ui.local_credentials = original
                        window.exiting = True
                        window.close()
                else:
                    client.post('/rest/system/shutdown').raise_for_status()
                process.wait(timeout=10)
                print(json.dumps({'status': 'passed_official_engine_scan', 'selected_files': sorted(names),
                                  'peers_configured': False, 'gui_engine_exit_verified': gui_exit}, ensure_ascii=False))
        finally:
            if process.poll() is None:
                try:
                    with httpx.Client(headers={'X-API-Key': key}, timeout=2, trust_env=False) as client:
                        client.post(f'http://127.0.0.1:{port}/rest/system/shutdown').raise_for_status()
                    process.wait(timeout=10)
                except (httpx.HTTPError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=10)
            log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('executable')
    parser.add_argument('--test-exit', action='store_true')
    args = parser.parse_args()
    verify(args.executable, args.test_exit)
