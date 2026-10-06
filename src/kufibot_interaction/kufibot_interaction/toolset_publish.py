"""Publish robot tool schemas without opening or changing a voice session."""
import asyncio
import json
import os
import uuid
from concurrent.futures import Future

from std_msgs.msg import String

from .navigation_tools import wait_ros


class ToolsetPublisher:
    def __init__(self, node):
        self.node = node
        self.busy = False
        self.results = node.create_publisher(String, 'voice_session/toolset_result', 10)
        node.create_subscription(String, 'voice_session/publish_toolset', self.receive, 10)

    def receive(self, message):
        try:
            request = json.loads(message.data)
            request_id = str(uuid.UUID(request['request_id']))
            workflow_uuid = str(uuid.UUID(request['workflow_uuid']))
        except (ValueError, KeyError, TypeError, AttributeError):
            return
        asyncio.run_coroutine_threadsafe(self.publish(request_id, workflow_uuid), self.node.loop)

    async def publish(self, request_id, workflow_uuid):
        result = {'request_id': request_id, 'workflow_uuid': workflow_uuid}
        if self.busy:
            result['error'] = 'Bir araç yayını zaten sürüyor'
        else:
            self.busy = True
            try:
                from verasist_sdk import LiveSession, VerasistClient
                key = os.environ.get('VERASIST_API_TOKEN') or os.environ.get('VERASIST_API_KEY')
                if not key:
                    raise ValueError('Robot üzerinde VERASIST_API_KEY veya VERASIST_API_TOKEN ayarlanmalı')
                with VerasistClient(base_url=self.node.endpoint, api_key=key, timeout=25) as client:
                    session = LiveSession(client)
                    self.node._register_tools(session)
                    self.node.navigation.register_tools(session)
                    toolset = await session.publish_toolset(workflow_uuid)
                    result['count'] = len(toolset.tools)
            except Exception as exc:
                # API errors may contain server internals; keep credentials and raw bodies off the UI.
                if getattr(exc, 'status_code', None) is not None:
                    result['error'] = f'Verasist araç yayını başarısız (HTTP {exc.status_code}). Workflow UUID ve API anahtarı yetkisini kontrol edin.'
                elif isinstance(exc, ValueError):
                    result['error'] = str(exc)
                else:
                    result['error'] = 'Verasist araç yayını başarısız. Sunucu bağlantısını kontrol edin.'
            finally:
                self.busy = False
        self.results.publish(String(data=json.dumps(result)))


class ToolsetPublishClient:
    def __init__(self, node):
        self.pending = {}
        self.publisher = node.create_publisher(String, 'voice_session/publish_toolset', 10)
        node.create_subscription(String, 'voice_session/toolset_result', self.receive, 10)

    def receive(self, message):
        try:
            result = json.loads(message.data)
            future = self.pending.get(result['request_id'])
            if future is not None and not future.done():
                future.set_result(result)
        except (ValueError, KeyError, TypeError, AttributeError):
            pass

    async def publish(self, workflow_uuid):
        workflow_uuid = str(uuid.UUID(workflow_uuid))
        if self.pending:
            raise ValueError('Bir araç yayını zaten sürüyor')
        request_id = str(uuid.uuid4())
        future = self.pending[request_id] = Future()
        try:
            self.publisher.publish(String(data=json.dumps({
                'request_id': request_id, 'workflow_uuid': workflow_uuid})))
            try:
                result = await wait_ros(future, 35)
            except TimeoutError:
                raise ValueError('Ses ajanından yayın sonucu alınamadı. Yayın gerçekleşmiş olabilir; workflow kataloğunu kontrol edin.') from None
            if result.get('error'):
                raise ValueError(result['error'])
            return {'workflow_uuid': workflow_uuid, 'count': result['count']}
        finally:
            self.pending.pop(request_id, None)
