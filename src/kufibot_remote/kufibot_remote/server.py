"""LAN discovery and bounded WebSocket streams for the mobile controller."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, port, name):
        self.port, self.name = port, name

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data == b'KUFIBOT_DISCOVER_V1':
            self.transport.sendto(json.dumps({
                'service': 'kufibot', 'version': 1,
                'name': self.name, 'port': self.port,
            }).encode(), addr)


class Server:
    def __init__(self, control, status, frame):
        self.control, self.status, self.frame = control, status, frame
        self.clients = set()
        self.app = web.Application()
        self.app.add_routes([web.get('/', self.index),
                             web.get('/assets/{name}', self.asset),
                             web.get('/control', self.controller),
                             web.get('/video', self.video)])

    @staticmethod
    async def index(request):
        return web.FileResponse(Path(__file__).with_name('web') / 'index.html',
                                headers={'Cache-Control': 'no-cache'})

    @staticmethod
    async def asset(request):
        name = request.match_info['name']
        if name not in {'app.js', 'connection.js', 'style.css'}:
            raise web.HTTPNotFound()
        return web.FileResponse(Path(__file__).with_name('web') / name,
                                headers={'Cache-Control': 'no-cache',
                                         'X-Content-Type-Options': 'nosniff'})

    @staticmethod
    def check_origin(request):
        # Native mobile clients do not send Origin. Browser clients must load
        # the controller from this server, not an unrelated website.
        origin = request.headers.get('Origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {'http', 'https'} or parsed.netloc != request.host:
                raise web.HTTPForbidden(text='Cross-origin WebSocket rejected')

    async def controller(self, request):
        self.check_origin(request)
        if len(self.clients) >= 4:
            raise web.HTTPServiceUnavailable(text='Too many clients')
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=4096)
        await ws.prepare(request)
        self.clients.add(ws)

        async def telemetry():
            while True:
                await asyncio.wait_for(ws.send_json({
                    'type': 'state', **self.status(),
                    'owner': self.control.owner is ws,
                }), timeout=1)
                await asyncio.sleep(0.2)

        async def guarded_telemetry():
            try:
                await telemetry()
            except (TimeoutError, ConnectionError, RuntimeError):
                await ws.close()

        sender = asyncio.create_task(guarded_telemetry())
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    command = None
                    try:
                        data = json.loads(msg.data)
                        command = data.get('type') if isinstance(data, dict) else None
                        self.control.command(ws, data)
                        await ws.send_json({'type': 'ack', 'command': command})
                    except (ValueError, TypeError) as error:
                        await ws.send_json({'type': 'error', 'command': command, 'message': str(error)})
        finally:
            self.control.release(ws)
            self.clients.discard(ws)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        return ws

    async def video(self, request):
        self.check_origin(request)
        if len(self.clients) >= 8:
            raise web.HTTPServiceUnavailable(text='Too many clients')
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=128)
        await ws.prepare(request)
        self.clients.add(ws)
        last = None

        async def frames():
            nonlocal last
            while True:
                frame = self.frame()
                if frame is not None and frame is not last:
                    last = frame
                    # Separate socket and no frame queue: slow clients drop frames.
                    await asyncio.wait_for(ws.send_str(frame), timeout=1)
                await asyncio.sleep(0.1)

        async def guarded_frames():
            try:
                await frames()
            except (TimeoutError, ConnectionError, RuntimeError):
                await ws.close()

        sender = asyncio.create_task(guarded_frames())
        try:
            async for _ in ws:
                pass
        finally:
            self.clients.discard(ws)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        return ws

    async def close(self):
        await asyncio.gather(*(ws.close() for ws in list(self.clients)))
