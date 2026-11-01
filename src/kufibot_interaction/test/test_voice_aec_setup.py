from pathlib import Path
import sys

import pytest
from kufibot_interaction import voice_aec


SETUP = vars(voice_aec)


def test_managed_config_upgrade_and_customization_detection():
    assert SETUP['is_managed'](SETUP['LEGACY_CONFIG'])
    for delay in (0, 180, 500):
        content = SETUP['configuration'](delay)
        assert SETUP['is_managed'](content)
        assert SETUP['is_managed'](content.replace('gain_control = false', 'gain_control = true'))
        assert not SETUP['is_managed'](content.replace('gain_control = false', 'gain_control = invalid'))
    assert not SETUP['is_managed'](SETUP['CONFIG'].replace('180/1000', '999/1000'))


def test_install_upgrade_reconfigure_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    path.parent.mkdir(parents=True)
    path.write_text(SETUP['LEGACY_CONFIG'])
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py'])
    SETUP['main']()
    assert path.read_text() == SETUP['CONFIG']


def test_unchanged_restart_does_not_disrupt_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    path.parent.mkdir(parents=True)
    path.write_text(SETUP['CONFIG'])
    restarted = []
    monkeypatch.setattr(SETUP['subprocess'], 'run', lambda *args, **kwargs: restarted.append(args))
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py', '--restart'])
    SETUP['main']()
    assert restarted == []
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


def test_tuned_latency_survives_session_aec_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = tmp_path / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    path.parent.mkdir(parents=True)
    tuned = SETUP['configuration'](300).replace('480/48000', '1920/48000')
    path.write_text(tuned)
    restarted = []
    monkeypatch.setattr(SETUP['subprocess'], 'run', lambda *a, **kw: restarted.append(a))
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py', '--restart',
                                    '--play-delay-ms', '300'])
    SETUP['main']()
    assert path.read_text() == tuned
    assert restarted == []
    monkeypatch.setattr(sys, 'argv', ['setup_voice_aec.py', '--restart',
                                    '--play-delay-ms', '180', '--no-noise-suppression'])
    SETUP['main']()
    assert path.read_text() == SETUP['configuration'](
        180, noise_suppression=False, node_latency='1920/48000')
    assert len(restarted) == 1


def test_latency_support_still_rejects_other_customizations():
    content = SETUP['configuration'](300, node_latency='1920/48000')
    assert SETUP['is_managed'](content)
    assert not SETUP['is_managed'](content.replace('audio.rate = 48000', 'audio.rate = 16000'))
    assert not SETUP['is_managed'](content.replace('1920/48000', '0/48000'))


def test_session_aec_setup_works_from_installed_package(tmp_path, monkeypatch):
    """A copied ROS install has no workspace tools/ directory beside the node."""
    import asyncio
    import shutil
    from types import SimpleNamespace
    from kufibot_interaction import voice_aec
    from kufibot_interaction.voice_agent_node import AEC_MODULE_DEFAULTS, VoiceAgentNode

    installed = tmp_path / 'install/lib/python3/site-packages'
    package = installed / 'kufibot_interaction'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    shutil.copyfile(voice_aec.__file__, package / 'voice_aec.py')
    config = tmp_path / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    config.parent.mkdir(parents=True)
    config.write_text(voice_aec.CONFIG)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', str(installed))
    monkeypatch.chdir(tmp_path)
    node = SimpleNamespace()

    asyncio.run(VoiceAgentNode._ensure_aec_module(node, {}))

    assert node._applied_aec_module_settings == AEC_MODULE_DEFAULTS
    assert config.read_text() == voice_aec.CONFIG
