from syncthing_bridge import platform_paths


def test_linux_paths_use_xdg_and_existing_syncthing_config(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.sys, 'platform', 'linux')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.delenv('STCONFDIR', raising=False)
    monkeypatch.delenv('STHOMEDIR', raising=False)
    current = tmp_path / 'state' / 'syncthing' / 'config.xml'
    legacy = tmp_path / 'config' / 'syncthing' / 'config.xml'

    assert platform_paths.app_data_directory() == tmp_path / 'config' / 'ugsync'
    assert platform_paths.syncthing_config_path() == current
    legacy.parent.mkdir(parents=True)
    legacy.touch()
    assert platform_paths.syncthing_config_path() == legacy
    current.parent.mkdir(parents=True)
    current.touch()
    assert platform_paths.syncthing_config_path() == current

    monkeypatch.setenv('STHOMEDIR', str(tmp_path / 'alternate-home'))
    monkeypatch.setenv('STCONFDIR', str(tmp_path / 'alternate-config'))
    assert platform_paths.syncthing_config_path() == tmp_path / 'alternate-config' / 'config.xml'
