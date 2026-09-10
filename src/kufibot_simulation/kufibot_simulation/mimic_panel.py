"""Observer controls use the same ownership-checked LAN API as web/mobile."""
import asyncio
import json
import queue
import threading
import time
from aiohttp import ClientSession, ClientTimeout
from direct.gui.DirectGui import DirectButton, DirectOptionMenu
from direct.gui.OnscreenText import OnscreenText
from panda3d.core import TextNode


class MimicPanel:
    def __init__(self, parent, url):
        self.url = url.rstrip('/')
        self.commands = queue.Queue(maxsize=20)
        self.updates = queue.Queue(maxsize=20)
        self.closed = threading.Event()
        self.records = []
        self.selection = DirectOptionMenu(parent=parent, items=['Mimikler yukleniyor'],
            scale=.045, pos=(.08,0,.40), text_align=TextNode.ALeft,
            text_fg=(.92,.96,1,1), frameColor=(.12,.18,.24,1))
        self.play = DirectButton(parent=parent, text='Calistir', scale=.045,
            pos=(.23,0,.24), command=self.run, text_fg=(1,1,1,1),
            frameColor=(.10,.45,.40,1), pad=(.2,.15))
        self.stop = DirectButton(parent=parent, text='DUR', scale=.045,
            pos=(.63,0,.24), command=lambda:self.send({'type':'stop'}),
            text_fg=(1,1,1,1), frameColor=(.55,.18,.22,1), pad=(.2,.15))
        self.status = OnscreenText(parent=parent, text='', pos=(.08,.10),
                                   align=TextNode.ALeft, fg=(.9,.95,1,1), scale=.033, mayChange=True)
        self.thread = threading.Thread(target=lambda:asyncio.run(self.worker()), daemon=True)
        self.thread.start()

    def send(self, value):
        try:
            self.commands.put_nowait(value)
        except queue.Full:
            self.status.setText('Komut kuyrugu dolu')

    def run(self):
        if self.records:
            record = self.records[self.selection.selectedIndex]
            self.send({'type':'playMimic','id':record['id'],'revision':record['revision']})

    def update(self):
        while not self.updates.empty():
            kind, value = self.updates.get_nowait()
            if kind == 'library':
                old = self.records[self.selection.selectedIndex]['id'] if self.records else None
                self.records = value
                self.selection['items'] = [item['name'] for item in value] or ['Mimik yok']
                self.selection.set(next((i for i,item in enumerate(value) if item['id']==old),0))
            else:
                self.status.setText(value)

    async def worker(self):
        while not self.closed.is_set():
            try:
                async with ClientSession(timeout=ClientTimeout(total=3)) as session:
                    async with session.ws_connect(self.url+'/control',heartbeat=5) as ws:
                        last_library = 0
                        owner = False
                        while not self.closed.is_set():
                            if time.monotonic()-last_library > 5:
                                async with session.get(self.url+'/api/mimics') as response:
                                    response.raise_for_status()
                                    self.publish('library',await response.json())
                                last_library=time.monotonic()
                            while not self.commands.empty():
                                command=self.commands.get_nowait()
                                if command['type']=='playMimic':
                                    # Claim may be rejected when another controller owns the robot.
                                    # Never switch that controller's mode after a rejected claim.
                                    if not owner:
                                        await ws.send_json({'type':'claim'})
                                    await ws.send_json({'type':'mode','mode':'remote'})
                                await ws.send_json(command)
                            if owner:
                                await ws.send_json({'type':'heartbeat'})
                            try:
                                message=await asyncio.wait_for(ws.receive(),.25)
                            except asyncio.TimeoutError:
                                continue
                            if message.type.name != 'TEXT':
                                raise ConnectionError('Baglanti kesildi')
                            data=json.loads(message.data)
                            if data.get('type')=='state':
                                owner=data.get('owner',False)
                                mimic=data.get('mimic',{})
                                self.publish('status',f"{mimic.get('state','idle')}  {mimic.get('elapsed_ms',0)/1000:.2f} s")
                            elif data.get('type')=='error':
                                self.publish('status',data.get('message','Hata'))
                        if owner:
                            await ws.send_json({'type':'stopMimic'})
            except Exception as error:
                self.publish('status',str(error))
                await asyncio.sleep(1)

    def publish(self, kind, value):
        if self.updates.full():
            self.updates.get_nowait()
        self.updates.put_nowait((kind,value))

    def close(self):
        self.closed.set()
        self.thread.join(timeout=1)
