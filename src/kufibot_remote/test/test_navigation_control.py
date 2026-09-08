import pytest
from kufibot_remote.control import Control


def ready():
    c = Control()
    owner = object()
    c.command(owner, {'type': 'claim'})
    c.command(owner, {'type': 'mode', 'mode': 'ai'})
    c.navigation_provider, c.navigation_ready = 'verasist', True
    return c, owner


def test_navigation_only_owner_verasist_ready_and_ai():
    c, owner = ready()
    with pytest.raises(ValueError):
        c.command(object(), {'type': 'setNavigationEnabled', 'enabled': True})
    c.navigation_provider = 'local'
    with pytest.raises(ValueError):
        c.command(owner, {'type': 'setNavigationEnabled', 'enabled': True})
    c.navigation_provider = 'verasist'
    c.navigation_ready = False
    with pytest.raises(ValueError):
        c.command(owner, {'type': 'setNavigationEnabled', 'enabled': True})
    c.navigation_ready = True
    c.command(owner, {'type': 'setNavigationEnabled', 'enabled': True})
    assert c.navigation_enabled
    assert c.tick(.05, {}) == (0., 0.)  # enabling is not a movement command


@pytest.mark.parametrize('event', ['stop', 'release', 'timeout', 'mode'])
def test_every_control_loss_invalidates_enable_epoch(event):
    c, owner = ready()
    c.command(owner, {'type': 'setNavigationEnabled', 'enabled': True})
    epoch = c.navigation_epoch
    if event == 'release':
        c.release(owner)
    elif event == 'timeout':
        c.last_heartbeat -= 3
        c.tick(.05, {})
    elif event == 'mode':
        c.command(owner, {'type': 'mode', 'mode': 'remote'})
    else:
        c.command(owner, {'type': 'stop'})
    assert not c.navigation_enabled and c.navigation_epoch != epoch
