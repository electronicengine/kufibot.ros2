"""Optional second listener for trusted LAN PWA installation."""
from pathlib import Path
import ssl

from aiohttp import web


async def start_local_https(runner, directory, port):
    if not port:
        return None
    directory = Path(directory).expanduser()
    certificate, key = directory/'server.crt', directory/'server.key'
    if not certificate.exists() and not key.exists():
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    site = web.TCPSite(runner, '0.0.0.0', port, ssl_context=context)
    await site.start()
    return site
