"""Workflow transport using the existing controller's ownership lease."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import wave
import uuid

from aiohttp import web
from kufibot_interaction.ai_settings import workflow_voice_settings, catalog, DEFAULT, validate
from kufibot_interaction.workflows import WorkflowStore, validate_graph, TOOLS, NODE_TYPES, MOTION_TOOLS
from kufibot_interaction.knowledge import KnowledgeStore, MAX_BYTES
from kufibot_interaction.local_voice_settings import VOICE_FIELDS
from kufibot_interaction.local_recording import recording_root, recording_waveform


class WorkflowAPI:
    def __init__(self, server):
        self.server = server
        self.workflows = WorkflowStore()
        self.knowledge = KnowledgeStore()
        self.knowledge.busy = lambda: bool((server.status().get('voiceStatus') or {}).get('active'))
        self.tokens = {}
        self.tests = {}
        self.test_channels = {}
        self.live_tests = set()
        self.live_starts = {}
        for collection in self.knowledge.collections():
            if collection['state'] in ('queued', 'indexing'):
                self.knowledge.queue(collection['id'])
        server.app.add_routes([
            web.post('/api/verasist/toolset', self.publish_toolset),
            web.get('/workflows', self.editor),
            web.get('/recordings', self.archive),
            web.get('/recordings/', self.archive),
            web.get('/workflow-assets/{name}', self.asset),
            web.get('/api/workflow-capabilities', self.capabilities),
            web.get('/api/workflows', self.list_workflows),
            web.get('/api/workflows/{id}', self.get_workflow),
            web.get('/api/knowledge/collections', self.collections),
            web.get('/api/knowledge/{id}/documents', self.documents),
            web.get('/api/knowledge/{id}/documents/{document}/preview', self.preview),
            web.post('/api/knowledge/{id}/upload', self.upload),
            web.get('/api/knowledge/{id}/sources/{chunk}', self.source),
            web.get('/api/recordings', self.recordings),
            web.get('/api/recordings/{id}', self.recording),
            web.get('/api/recordings/{id}/details', self.recording_details),
            web.get('/api/recordings/{id}/waveform', self.waveform),
        ])

    async def publish_toolset(self, request):
        self.authorize(request)
        if self.server.publish_toolset is None:
            return web.json_response({'error': 'Ses ajanı araç yayını kullanılamıyor'}, status=503)
        try:
            data = await request.json()
            workflow_uuid = str(uuid.UUID(data['workflow_uuid']))
        except (ValueError, KeyError, TypeError, AttributeError):
            return web.json_response({'error': 'Geçerli bir Workflow UUID girin'}, status=400)
        try:
            return web.json_response(await self.server.publish_toolset(workflow_uuid))
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400)

    def token(self, ws):
        if self.server.control.owner is not ws:
            return ''
        old = self.tokens.get(ws)
        if old is None or old[1] - time.monotonic() < 30:
            old = (secrets.token_urlsafe(32), time.monotonic() + 300)
            self.tokens[ws] = old
        return old[0]

    def authorize(self, request):
        self.server.check_origin(request)
        token = (request.headers.get('Authorization', '').removeprefix('Bearer ')
                 or request.query.get('token', ''))
        ws = self.server.control.owner
        expected, expiry = self.tokens.get(ws, ('', 0))
        if not token or not secrets.compare_digest(token, expected) or expiry < time.monotonic():
            raise web.HTTPForbidden(text='Kumanda sahipliği ve geçerli yükleme yetkisi gerekli')
        return ws

    @staticmethod
    def page(filename):
        root = Path(__file__).with_name('web') / 'workflow'
        content = (root / filename).read_text()
        for name in ('editor.js', 'editor.css', 'archive.js', 'archive.css'):
            asset = f'/workflow-assets/{name}'
            if asset in content:
                version = hashlib.sha256((root / name).read_bytes()).hexdigest()[:12]
                content = content.replace(asset, f'{asset}?v={version}')
        return web.Response(text=content, content_type='text/html', headers={'Cache-Control': 'no-store'})

    async def editor(self, request):
        return self.page('index.html')

    async def archive(self, request):
        return self.page('recordings.html')

    async def asset(self, request):
        name = request.match_info['name']
        if name not in ('editor.js', 'editor.css', 'archive.js', 'archive.css'):
            raise web.HTTPNotFound()
        return web.FileResponse(Path(__file__).with_name('web') / 'workflow' / name,
                                headers={'Cache-Control': 'no-cache'})

    async def capabilities(self, request):
        return web.json_response({'nodes': NODE_TYPES, 'tools': TOOLS, 'models': catalog(),
                                  'model_notes': {'ufakzeka-1-q8_0': 'Türkçe sohbet modeli. Yerel testte otomatik araç seçimi 0/2; güvenilir araç çalıştırmak için sabit Araç Adımı kullanın.'},
                                  'defaults': DEFAULT, 'voice_fields': VOICE_FIELDS, 'max_upload_bytes': MAX_BYTES})

    async def list_workflows(self, request):
        return web.json_response(self.workflows.all())

    async def get_workflow(self, request):
        try:
            return web.json_response(self.workflows.get(request.match_info['id']))
        except (ValueError, FileNotFoundError):
            raise web.HTTPNotFound()

    async def collections(self, request):
        return web.json_response(self.knowledge.collections())

    async def documents(self, request):
        try:
            return web.json_response(self.knowledge.documents(request.match_info['id']))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))

    async def source(self, request):
        self.authorize(request)
        with self.knowledge.connect() as db:
            row = db.execute('''SELECT chunks.text,chunks.location,documents.name FROM chunks JOIN documents
                ON documents.id=chunks.document_id WHERE chunks.id=? AND documents.collection_id=?''',
                (request.match_info['chunk'], request.match_info['id'])).fetchone()
        if row is None:
            raise web.HTTPNotFound()
        return web.json_response(dict(row))

    def _recording(self, key):
        if not key or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for ch in key):
            raise ValueError('Geçersiz kayıt kimliği')
        root = recording_root()
        meta = root / f'{key}.json'
        audio = root / f'{key}.wav'
        if not meta.is_file() or not audio.is_file():
            raise FileNotFoundError(key)
        return json.loads(meta.read_text()), audio

    async def recordings(self, request):
        self.authorize(request)
        root = recording_root()
        result = []
        for path in root.glob('*.json'):
            try:
                item, _ = self._recording(path.stem)
                result.append({key: item[key] for key in ('id', 'created_at', 'duration_sec', 'channels') if key in item}
                              | {'workflow_name': item.get('workflow', {}).get('name', 'Yerel görüşme')})
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        return web.json_response(sorted(result, key=lambda item: item['created_at'], reverse=True),
                                 headers={'Cache-Control': 'no-store'})

    async def recording_details(self, request):
        self.authorize(request)
        try:
            info, _ = self._recording(request.match_info['id'])
            return web.json_response(info, headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError):
            raise web.HTTPNotFound()

    async def waveform(self, request):
        self.authorize(request)
        try:
            _, path = self._recording(request.match_info['id'])
            data = await asyncio.to_thread(self._waveform, path)
            return web.json_response(data, headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError, EOFError, wave.Error):
            raise web.HTTPNotFound()

    @staticmethod
    def _waveform(path):
        cache = path.with_suffix('.peaks')
        if cache.is_file() and cache.stat().st_mtime_ns >= path.stat().st_mtime_ns:
            try:
                return json.loads(cache.read_text())
            except (ValueError, OSError):
                pass
        data = recording_waveform(path)
        # Atomic publication; concurrent readers never see a partial cache.
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output)
            temporary.replace(cache)
        except OSError:
            if 'temporary' in locals():
                temporary.unlink(missing_ok=True)
        return data

    async def recording(self, request):
        self.authorize(request)
        try:
            _, path = self._recording(request.match_info['id'])
            return web.FileResponse(path, headers={'Content-Type': 'audio/wav', 'Cache-Control': 'no-store'})
        except (ValueError, FileNotFoundError):
            raise web.HTTPNotFound()

    async def preview(self, request):
        self.authorize(request)
        try:
            return web.json_response(await asyncio.to_thread(self.knowledge.preview,
                request.match_info['id'], request.match_info['document']))
        except (ValueError, OSError) as exc:
            raise web.HTTPBadRequest(text=str(exc))

    async def upload(self, request):
        owner = self.authorize(request)
        fd, name = tempfile.mkstemp(dir=self.knowledge.root, prefix='.upload-')
        stream = os.fdopen(fd, 'wb')
        try:
            reader = await request.multipart()
            part = await reader.next()
            if part is None or not part.filename:
                raise ValueError('Dosya gerekli')
            size = 0
            with stream:
                while True:
                    chunk = await part.read_chunk(size=65536)
                    if not chunk:
                        break
                    if self.server.control.owner is not owner:
                        raise ValueError('Kontrol bağlantısı kesildi')
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('Dosya 20 MiB sınırını aşıyor')
                    stream.write(chunk)
            if self.authorize(request) is not owner:
                raise ValueError('Kontrol değişti')
            result = await asyncio.to_thread(self.knowledge.add, request.match_info['id'], name,
                                             part.filename, request.query.get('delimiter'))
            return web.json_response(result)
        except (ValueError, UnicodeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        finally:
            stream.close()
            if os.path.exists(name):
                os.unlink(name)

    async def command(self, ws, data):
        if self.server.control.owner is not ws:
            raise ValueError('Önce kumandayı devralın')
        action = data.get('action')
        if action == 'liveStart':
            workflow = data['workflow']
            errors = self.validate(workflow)
            if errors:
                raise ValueError('; '.join(errors))
            if data.get('real') is not True and any(n.get('data', {}).get('tool') in MOTION_TOOLS for n in workflow['nodes']):
                raise ValueError('Sesli akış hareket araçları içeriyor. Gerçek robot hareketlerini onaylayın veya metin testini kullanın.')
            await self.stop_test(ws)
            workflow = self.workflows.save(workflow)
            self.workflows.publish(workflow)
            settings = validate({**DEFAULT, **workflow_voice_settings(workflow), 'provider': 'local',
                                 'workflow_id': workflow['id']},
                                workflow_llm_enabled=workflow.get('settings', {}).get('llm_enabled', True))
            self.server.control.command(ws, {'type': 'mode', 'mode': 'remote'})
            settings['activation'] = data.get('activation') or (self.server.status().get('aiConfig') or {}).get('settings', {}).get('activation', DEFAULT['activation'])
            self.server.control.command(ws, {'type': 'setAiSettings', 'settings': settings})
            self.server.control.voice_test_active = True
            self.live_tests.add(ws)
            self.live_starts[ws] = asyncio.create_task(self.start_live_when_ready(ws, settings))
            return {'status': 'starting', 'workflow': workflow}
        if action == 'save':
            return self.workflows.save(data['workflow'])
        if action == 'validate':
            return {'errors': self.validate(data['workflow'])}
        if action == 'activate':
            workflow = self.workflows.get(data['id'])
            errors = self.validate(workflow)
            if errors:
                raise ValueError('; '.join(errors))
            settings = validate({**DEFAULT, **workflow_voice_settings(workflow), 'provider': 'local', 'workflow_id': ''},
                                workflow_llm_enabled=workflow.get('settings', {}).get('llm_enabled', True))
            self.workflows.publish(workflow)
            settings['workflow_id'] = workflow['id']
            settings['activation'] = data.get('activation') or (self.server.status().get('aiConfig') or {}).get('settings', {}).get('activation', DEFAULT['activation'])
            self.server.control.command(ws, {'type': 'setAiSettings', 'settings': settings})
            return {'status': 'requested', 'workflow_id': workflow['id']}
        if action == 'collectionCreate':
            return self.knowledge.create(data['name'], data['model'])
        if action == 'reindex':
            self.knowledge.collection(data['id'])
            self.knowledge.queue(data['id'])
            return {'status': 'queued'}
        if action == 'collectionModel':
            return self.knowledge.set_model(data['id'], data['model'])
        if action == 'documentDelete':
            self.knowledge.delete_document(data['id'], data['document_id'])
            return {'status': 'deleted', 'recoverable': True}
        if action == 'collectionDelete':
            self.knowledge.delete_collection(data['id'])
            return {'status': 'deleted', 'recoverable': True}
        if action in ('testStop', 'liveStop'):
            await self.stop_test(ws, stop_local=action == 'liveStop')
            return {'status': 'stopped'}
        if action == 'test':
            status = self.server.status()
            active = (status.get('voiceStatus') or {}).get('active')
            if not active and status.get('appliedMode') == 'remote' and ws not in self.live_starts:
                self.live_tests.discard(ws)
            if self.server.control.mode == 'ai' or ws in self.live_tests or ws in self.live_starts or active:
                raise ValueError('Testten önce sesli görüşmeyi durdurun')
            if ws in self.tests:
                channel = self.test_channels.get(ws)
                if not channel or not channel['ready']:
                    raise ValueError('Test yanıtı hazırlanıyor')
                if channel['workflow'] != data.get('workflow') or channel['real'] != (data.get('real') is True):
                    raise ValueError('Akış değişti; testi durdurup yeniden başlatın')
                text = data.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 1500:
                    raise ValueError('Test metni 1–1500 karakter olmalı')
                channel['ready'] = False
                channel['process'].stdin.write((json.dumps({'text': text}) + '\n').encode())
                await channel['process'].stdin.drain()
                return {'status': 'continued'}
            workflow = data['workflow']
            errors = self.validate(workflow)
            if errors:
                raise ValueError('; '.join(errors))
            text = data.get('text', '')
            if not isinstance(text, str) or not text.strip() or len(text) > 1500:
                raise ValueError('Test metni 1–1500 karakter olmalı')
            self.server.control.voice_test_active = True
            task = asyncio.create_task(self.test(ws, workflow, text, data.get('real') is True))
            self.tests[ws] = task
            return {'status': 'started'}
        raise ValueError('Bilinmeyen workflow işlemi')

    def validate(self, workflow):
        errors = validate_graph(workflow)
        try:
            validate({**DEFAULT, **workflow_voice_settings(workflow), 'provider': 'local', 'workflow_id': ''},
                     workflow_llm_enabled=workflow.get('settings', {}).get('llm_enabled', True))
            for node in workflow.get('nodes', []):
                data = node.get('data', {})
                keys = ([data['collection_id']] if node.get('type') == 'knowledge' else data.get('collection_ids', []))
                for key in keys:
                    if not self.knowledge.collection(key)['generation']:
                        errors.append('Bağlı koleksiyon henüz hazır değil')
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
        return errors

    async def test(self, ws, workflow, text, real):
        process = None
        try:
            models = {m['id']: m for m in catalog()}
            config = {'workflow': workflow, 'text': text}
            if workflow.get('settings', {}).get('llm_enabled', True):
                config.update(llm=models[workflow['settings']['llm']]['path'],
                              embedding=models[workflow['settings']['embedding']]['path'])
            process = await asyncio.create_subprocess_exec(sys.executable, '-m',
                'kufibot_interaction.workflow_test_worker', stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, limit=1024*1024)
            process.stdin.write((json.dumps(config)+'\n').encode())
            await process.stdin.drain()
            self.test_channels[ws] = {'process': process, 'ready': False, 'workflow': workflow, 'real': real}
            async def read():
                while line := await process.stdout.readline():
                    if self.server.control.owner is not ws:
                        raise ValueError('Kontrol kaybedildi')
                    event = json.loads(line)
                    if event.get('type') == 'workflow_tool':
                        try:
                            result = await asyncio.wait_for(self.test_tool(ws, event['name'], event['arguments'], real), 9)
                        except Exception as exc:
                            result = {'status': 'error', 'error': str(exc)}
                        process.stdin.write((json.dumps({**event, 'result': result})+'\n').encode())
                        await process.stdin.drain()
                    else:
                        if event.get('type') == 'answer':
                            self.test_channels[ws]['ready'] = not event.get('ended')
                        await ws.send_json({'type': 'workflowEvent', 'event': {**event, 'test_mode': 'text', 'workflow_id': workflow.get('id')}})
            await asyncio.wait_for(read(), 120)
        except Exception as exc:
            if not ws.closed:
                await ws.send_json({'type': 'workflowEvent', 'event': {'type': 'error', 'test_mode': 'text', 'workflow_id': workflow.get('id'), 'message': str(exc)}})
        finally:
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            self.tests.pop(ws, None)
            self.test_channels.pop(ws, None)
            self.server.control.voice_test_active = bool(self.tests or self.live_tests)
            if not ws.closed:
                await ws.send_json({'type': 'workflowEvent', 'event': {'type': 'test_session', 'test_mode': 'text',
                    'workflow_id': workflow.get('id'), 'status': 'stopped'}})

    async def test_tool(self, ws, name, args, real):
        if self.server.control.owner is not ws or name not in TOOLS:
            raise ValueError('Araç yetkisi yok')
        if name == 'search_documents':
            return await asyncio.to_thread(self.knowledge.search, **args)
        if name in MOTION_TOOLS and not real:
            return {'status': 'ok', 'simulated': True, 'name': name, 'arguments': args}
        state = self.server.status()
        if name == 'get_robot_status':
            return state
        if name == 'get_sensor_data':
            return {'status': 'ok', 'sensors': state.get('sensors', {})}
        if name == 'get_joint_positions':
            return {'positions': state.get('joints', {})}
        if name == 'list_mimics':
            return {'mimics': list(self.server.mimics.all())}
        if self.server.control.mode != 'remote':
            raise ValueError('Gerçek hareket testi için kumanda modu gerekli')
        if name == 'play_mimic':
            motion = self.server.mimics.get(args['id'])
            self.server.control.command(ws, {'type': 'playMimic', 'id': args['id'], 'revision': motion['revision']})
        elif name == 'stop_mimic':
            self.server.control.command(ws, {'type': 'stopMimic'})
        elif name == 'stop_joint_motion':
            self.server.control.command(ws, {'type': 'stop'})
        elif name == 'set_joint_positions':
            from kufibot_interaction.joint_limits import validate_joint_targets
            angles, _ = validate_joint_targets(args['names'], args['angles_deg'])
            for joint, angle in zip(args['names'], angles):
                self.server.control.command(ws, {'type': 'joint', 'name': joint, 'value': angle})
        return {'status': 'ok'}

    async def start_live_when_ready(self, ws, settings):
        async def report(status, message=''):
            if not ws.closed:
                await ws.send_json({'type': 'workflowEvent', 'event': {'type': 'test_session',
                    'test_mode': 'live', 'workflow_id': settings['workflow_id'], 'status': status, 'message': message}})
        try:
            await report('starting', 'Ses ajanı ayarları uygulanıyor…')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and self.server.control.owner is ws:
                status = self.server.status()
                reported = (status.get('aiConfig') or {}).get('settings', {})
                if (self.server.control.ai_settings_requested is None
                        and all(reported.get(k) == v for k, v in settings.items())
                        and status.get('appliedMode') == 'remote'
                        and not (status.get('voiceStatus') or {}).get('active')):
                    self.server.control.command(ws, {'type': 'mode', 'mode': 'ai'})
                    break
                await asyncio.sleep(.1)
            else:
                raise ValueError('Ses ajanı ayarları onaylamadı. ROS ses düğümünü ve bağlantısını kontrol edin.')
            await report('starting', 'Yerel ses oturumu başlatılıyor…')
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and self.server.control.owner is ws:
                voice = self.server.status().get('voiceStatus') or {}
                if voice.get('active'):
                    await report('running')
                    return
                if voice.get('state') == 'error':
                    raise ValueError(voice.get('detail') or 'Ses oturumu başlatılamadı')
                await asyncio.sleep(.1)
            raise ValueError('Ses oturumu başlatılamadı. Yeniden deneyebilirsiniz.')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.live_tests.discard(ws)
            self.server.control.voice_test_active = bool(self.tests or self.live_tests)
            if self.server.control.owner is ws:
                self.server.control.command(ws, {'type': 'mode', 'mode': 'remote'})
            await report('error', str(exc))
        finally:
            self.live_starts.pop(ws, None)

    async def stop_test(self, ws, *, stop_local=False):
        pending = self.live_starts.pop(ws, None)
        if pending:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        local = (self.server.status().get('aiConfig') or {}).get('settings', {}).get('provider') == 'local'
        if ws in self.live_tests or stop_local and local:
            self.live_tests.discard(ws)
            self.server.control.voice_test_active = bool(self.tests or self.live_tests)
            if self.server.control.owner is ws:
                self.server.control.command(ws, {'type': 'mode', 'mode': 'remote'})
            if not ws.closed:
                await ws.send_json({'type': 'workflowEvent', 'event': {'type': 'test_session', 'test_mode': 'live', 'status': 'stopped'}})
        task = self.tests.get(ws)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self.server.control.owner is ws:
                self.server.control.stop()
                self.server.control.targets.clear()

    async def disconnect(self, ws):
        self.tokens.pop(ws, None)
        await self.stop_test(ws)
