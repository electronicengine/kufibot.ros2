"""LAN discovery and bounded WebSocket streams for the mobile controller."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from kufibot_interaction.mimics import default_store, RevisionConflict
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription


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
    def __init__(self, control, status, video_track=None, tool_call=None, tool_image=None):
        self.control, self.status = control, status
        self.video_track = video_track
        self.tool_call, self.tool_image = tool_call, tool_image
        self.mimics = control.mimic_store or default_store()
        control.mimic_store = self.mimics
        self.clients = set()
        self.peers = set()
        self.app = web.Application(client_max_size=21 * 1024 * 1024)
        from .workflow_api import WorkflowAPI
        self.workflow_api = WorkflowAPI(self)
        self.app.add_routes([web.get('/', self.index),
                             web.get('/manifest.webmanifest', self.manifest),
                             web.get('/service-worker.js', self.service_worker),
                             web.get('/assets/{name}', self.asset),
                             web.get('/mimics', self.editor),
                             web.get('/api/mimics', self.list_mimics),
                             web.get('/api/mimics/{name}', self.get_mimic),
                             web.put('/api/mimics/{name}', self.save_mimic),
                             web.get('/model/{name}', self.model_asset),
                             web.get('/control', self.controller),
                             web.post('/offer', self.offer), web.get('/tool-image/{image_id}', self.image)])

    async def list_mimics(self, request):
        try:
            return web.json_response(list(self.mimics.all().values()))
        except (ValueError, OSError) as error:
            raise web.HTTPServiceUnavailable(text=str(error))

    async def get_mimic(self, request):
        try:
            return web.json_response(self.mimics.get(request.match_info['name']))
        except ValueError as error:
            raise web.HTTPNotFound(text=str(error))

    async def save_mimic(self, request):
        self.check_origin(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or value.get('id') != request.match_info['name']:
                raise ValueError('Mimik kimliği eşleşmiyor')
            return web.json_response(self.mimics.save(value))
        except RevisionConflict as error:
            raise web.HTTPConflict(text=str(error))
        except (ValueError, TypeError) as error:
            raise web.HTTPBadRequest(text=str(error))

    @staticmethod
    async def editor(request):
        return web.FileResponse(Path(__file__).with_name('web') / 'mimics.html')

    @staticmethod
    async def model_asset(request):
        from kufibot_interaction import robot_model
        name = request.match_info['name']
        if name not in {'robot.glb', 'rig.json'}:
            raise web.HTTPNotFound()
        return web.FileResponse(Path(robot_model.__file__).with_name('model') / name)

    async def image(self, request):
        image = self.tool_image(request.match_info['image_id']) if self.tool_image else None
        if not image:
            raise web.HTTPNotFound()
        return web.Response(body=image, content_type='image/jpeg')

    @staticmethod
    async def index(request):
        return web.FileResponse(Path(__file__).with_name('web') / 'index.html',
                                headers={'Cache-Control': 'no-cache'})

    @staticmethod
    async def manifest(request):
        return web.FileResponse(Path(__file__).with_name('web') / 'manifest.webmanifest',
                                headers={'Cache-Control': 'no-cache'})

    @staticmethod
    async def service_worker(request):
        return web.FileResponse(Path(__file__).with_name('web') / 'service-worker.js',
                                headers={'Cache-Control': 'no-cache',
                                         'Service-Worker-Allowed': '/'})

    @staticmethod
    async def asset(request):
        name = request.match_info['name']
        if name not in {'app.js', 'connection.js', 'style.css', 'mimics.js', 'mimic-math.js', 'mimics.css', 'three.module.js', 'three.core.js', 'GLTFLoader.js', 'OrbitControls.js', 'BufferGeometryUtils.js', 'icon.svg'}:
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
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=262144)
        await ws.prepare(request)
        self.clients.add(ws)

        async def telemetry():
            while True:
                status = self.status()
                settings = (status.get('aiConfig') or {}).get('settings', {})
                if settings and self.control.ai_settings_requested is None:
                    self.control.local_workflow_enabled = bool(settings.get('workflow_id') and settings.get('provider') == 'local')
                await asyncio.wait_for(ws.send_json({
                    'type': 'state', **status,
                    'owner': self.control.owner is ws,
                    'workflowToken': self.workflow_api.token(ws),
                    'mimic': self.control.mimic_status,
                }), timeout=1)
                await asyncio.sleep(0.2)

        async def guarded_telemetry():
            try:
                await telemetry()
            except (TimeoutError, ConnectionError, RuntimeError):
                await ws.close()

        tool_job = None

        async def execute_tool(data):
            try:
                result = await self.tool_call(data.get('name'), data.get('arguments'))
            except Exception as error:
                result = {'status': 'error', 'reason': str(error)}
            if not ws.closed:
                await ws.send_json({'type': 'toolResult', 'name': data.get('name'), 'result': result})

        sender = asyncio.create_task(guarded_telemetry())
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    command = None
                    try:
                        data = json.loads(msg.data)
                        command = data.get('type') if isinstance(data, dict) else None
                        if command == 'workflow':
                            try:
                                result = await self.workflow_api.command(ws, data)
                                await ws.send_json({'type': 'workflowResult', 'request_id': data.get('request_id'), 'result': result})
                            except (ValueError, KeyError, TypeError, OSError) as exc:
                                await ws.send_json({'type': 'workflowResult', 'request_id': data.get('request_id'), 'error': str(exc)})
                        elif command == 'tool':
                            if not self.tool_call:
                                raise ValueError('Araç çağrıları kullanılamıyor')
                            # A reconnect can race the initial claim/state
                            # packet. Tool mode is an explicit user action, so
                            # claim an otherwise idle controller here instead
                            # of rejecting it with a misleading manual-control
                            # error. Never take it from another client.
                            if self.control.owner is None:
                                self.control.command(ws, {'type': 'claim'})
                            if self.control.owner is not ws:
                                raise ValueError('Robot başka bir cihazdan kontrol ediliyor')
                            if self.control.mode != 'tools':
                                self.control.command(ws, {'type': 'mode', 'mode': 'tools'})
                            if tool_job is not None and not tool_job.done():
                                raise ValueError('Bir araç çağrısı zaten çalışıyor')
                            tool_job = asyncio.create_task(execute_tool(data))
                        else:
                            self.control.command(ws, data)
                            if command == 'stop':
                                await self.workflow_api.stop_test(ws)
                        await ws.send_json({'type': 'ack', 'command': command})
                    except (ValueError, TypeError) as error:
                        if command == 'playMimic':
                            self.control.mimic_status['error'] = str(error)
                        await ws.send_json({'type': 'error', 'command': command, 'message': str(error)})
        finally:
            self.control.release(ws)
            await self.workflow_api.disconnect(ws)
            if tool_job is not None:
                tool_job.cancel()
                await asyncio.gather(tool_job, return_exceptions=True)
            self.clients.discard(ws)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        return ws

    async def offer(self, request):
        """Accept browser SDP and return a LAN WebRTC video answer."""
        self.check_origin(request)
        if self.video_track is None or len(self.peers) >= 4:
            raise web.HTTPServiceUnavailable(text='WebRTC video unavailable')
        try:
            data = await request.json()
            offer = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
            if offer.type != 'offer':
                raise ValueError('Expected WebRTC offer')
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text='Invalid WebRTC offer')
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.peers.add(peer)
        peer.addTrack(self.video_track())

        @peer.on('connectionstatechange')
        async def connection_state_change():
            if peer.connectionState in ('closed', 'failed', 'disconnected'):
                self.peers.discard(peer)
                await peer.close()

        try:
            await peer.setRemoteDescription(offer)
            answer = await peer.createAnswer()
            await peer.setLocalDescription(answer)
            return web.json_response({'sdp': peer.localDescription.sdp,
                                      'type': peer.localDescription.type})
        except Exception:
            self.peers.discard(peer)
            await peer.close()
            raise

    async def close(self):
        self.workflow_api.knowledge.cancel.set()
        await asyncio.gather(*(ws.close() for ws in list(self.clients)))
        await asyncio.gather(*(peer.close() for peer in list(self.peers)))
