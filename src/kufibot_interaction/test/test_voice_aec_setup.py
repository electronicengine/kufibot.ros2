from pathlib import Path
import runpy
import sys

import pytest


SETUP = runpy.run_path(str(Path(__file__).resolve().parents[3] / 'tools/setup_voice_aec.py'))


def test_managed_config_upgrade_and_customization_detection():
    assert SETUP['is_managed'](SETUP['LEGACY_CONFIG'])
    for delay in (0, 180, 500):
        content = SETUP['configuration'](delay)
        assert SETUP['is_managed'](content)
        assert not SETUP['is_managed'](content.replace('gain_control = false', 'gain_control = true'))
    assert not SETUP['is_managed'](SETUP['CONFIG'].replace('180/1000', '999/1000'))


def test_install_upgrade_reconfigure_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    path.parent.mkdir(parents=True)
    path.write_text(SETUP['LEGACY_CONFIG'])
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py'])
    SETUP['main']()
    assert path.read_text() == SETUP['CONFIG']
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py', '--play-delay-ms', '0'])
    SETUP['main']()
    assert path.read_text() == SETUP['configuration'](0)
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py', '--remove'])
    SETUP['main']()
    assert not path.exists()


@pytest.mark.parametrize('arguments', [[], ['--remove']])
def test_custom_config_is_preserved(tmp_path, monkeypatch, arguments):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py', *arguments])
    path = tmp_path / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    path.parent.mkdir(parents=True)
    path.write_text('custom configuration')
    with pytest.raises(SystemExit):
        SETUP['main']()
    assert path.read_text() == 'custom configuration'
