import asyncio
from aiohttp.test_utils import TestClient, TestServer
from kufibot_remote.server import Server
from kufibot_remote.control import Control


def test_api_defaults_update_conflict_and_assets(tmp_path,monkeypatch):
    monkeypatch.setenv('KUFIBOT_MIMICS_FILE',str(tmp_path/'mimics.json'))
    async def scenario():
        server=Server(Control(),lambda:{})
        async with TestClient(TestServer(server.app)) as client:
            response=await client.get('/api/mimics');records=await response.json()
            assert len(records)>=5
            item=records[0];original=item['revision'];name=item['id']
            response=await client.put('/api/mimics/'+name,json=item)
            assert response.status==200
            assert (await response.json())['revision']==original+1
            response=await client.put('/api/mimics/'+name,json=item)
            assert response.status==409
            response=await client.put('/api/mimics/'+name,json=item,headers={'Origin':'http://unrelated.test'})
            assert response.status==403
            item['keyframes'][0]['joints']['rightArm']=1000
            assert (await client.put('/api/mimics/'+name,json=item)).status==400
            assert (await client.get('/api/mimics/missing')).status==404
            assert (await client.get('/model/rig.json')).status==200
            response=await client.get('/model/robot.glb')
            assert (await response.read())[:4]==b'glTF'
            assert (await client.get('/model/secrets')).status==404
            assert (await client.get('/assets/mimics.js')).status==200
    asyncio.run(scenario())
