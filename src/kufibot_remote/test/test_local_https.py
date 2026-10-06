"""LAN certificate generation and TLS handshake with an explicitly trusted CA."""
import asyncio
import importlib.util
from pathlib import Path
import socket
import ssl
import stat

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from kufibot_remote.control import Control
from kufibot_remote.local_https import start_local_https
from kufibot_remote.server import Server

spec = importlib.util.spec_from_file_location('setup_local_https',
    Path(__file__).resolve().parents[3] / 'tools/setup_local_https.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_rejects_invalid_certificate_names():
    with pytest.raises(ValueError, match='Geçersiz adres'):
        setup.addresses(['robot\nDNS:other'])


def test_https_trusted_handshake_and_http_setup(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path/'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path/'knowledge'))
    directory = tmp_path/'tls'
    setup.generate(directory, setup.addresses(['127.0.0.1', 'robot.local']))
    ca = (directory/'root-ca.crt').read_bytes()
    setup.generate(directory, setup.addresses(['127.0.0.1', 'robot.local', '192.168.1.20']))
    assert (directory/'root-ca.crt').read_bytes() == ca
    assert stat.S_IMODE((directory/'root-ca.key').stat().st_mode) == 0o600
    assert stat.S_IMODE((directory/'server.key').stat().st_mode) == 0o600

    async def scenario():
        server = Server(Control(), lambda: {'mode': 'remote'})
        async with TestClient(TestServer(server.app)) as http:
            assert await start_local_https(http.server.runner, tmp_path/'absent', 8443) is None
            assert await start_local_https(http.server.runner, directory, 0) is None
            assert (await http.get('/kufibot-root-ca.crt')).status == 404
            info = await (await http.get('/api/pwa-setup')).json()
            assert info['https_port'] is None
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            await start_local_https(http.server.runner, directory, port)
            server.https_port, server.https_ca = port, directory/'root-ca.crt'
            info = await (await http.get('/api/pwa-setup')).json()
            assert info == {'https_port': port, 'certificate_available': True}
            assert await (await http.get('/kufibot-root-ca.crt')).read() == ca
            assert (await http.get('/pwa-setup')).status == 200
            for path in ('/root-ca.key', '/server.key', '/assets/root-ca.key'):
                assert (await http.get(path)).status == 404
            context = ssl.create_default_context(cafile=str(directory/'root-ca.crt'))
            async with ClientSession() as client:
                base = f'https://127.0.0.1:{port}'
                async with client.get(base+'/', ssl=context) as response:
                    assert response.status == 200
                async with client.get(base+'/manifest.webmanifest', ssl=context) as response:
                    assert response.status == 200
                async with client.ws_connect(base+'/control', ssl=context, origin=base) as ws:
                    assert (await ws.receive_json())['type'] == 'state'
    asyncio.run(scenario())
