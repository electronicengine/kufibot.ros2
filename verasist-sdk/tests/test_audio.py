import json
from uuid import uuid4

import httpx
import pytest
from websockets.asyncio.server import serve

from verasist_sdk import ApiError, VerasistClient


def client(handler):
    result = VerasistClient(api_key="test-key")
    result._http.close()
    result._http = httpx.Client(
        base_url="http://test/api/v1",
        headers={"X-API-Key": "test-key"},
        transport=httpx.MockTransport(handler),
    )
    return result


def test_http_audio_upload_and_binary_synthesis():
    requests = []
    request_id = str(uuid4())

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("transcriptions"):
            assert request_id.encode() in request.content
            assert b"RIFF" in request.content
            return httpx.Response(
                200,
                json={"request_id": request_id, "text": "hello", "duration_seconds": 1},
            )
        return httpx.Response(200, content=b"RIFF-audio")

    with client(handler) as sdk:
        assert (
            sdk.audio.transcribe("wf", b"RIFF", request_id=request_id)["text"]
            == "hello"
        )
        assert sdk.audio.synthesize("wf", "hello") == b"RIFF-audio"
        assert b"".join(sdk.audio.synthesize_chunks("wf", "hello")) == b"RIFF-audio"
    assert all(r.headers["x-api-key"] == "test-key" for r in requests)
    assert all("sessions" not in r.url.path for r in requests)
    assert json.loads(requests[-1].content)["stream"] is True


def test_errors_do_not_retry_billable_requests():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(402, json={"detail": "Insufficient credits"})

    with client(handler) as sdk:
        with pytest.raises(ApiError):
            sdk.audio.synthesize("wf", "hello")
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stt", "tts"])
async def test_socket_contract_and_order(operation):
    received = []

    async def handler(socket):
        assert socket.request.headers["X-API-Key"] == "test-key"
        received.append(json.loads(await socket.recv()))
        await socket.send('{"type":"started"}')
        while True:
            message = await socket.recv()
            received.append(message)
            if isinstance(message, str) and json.loads(message)["type"] == "end":
                break
        if operation == "stt":
            await socket.send('{"type":"transcript","text":"hello","final":true}')
        else:
            await socket.send(b"\0\0" * 8)
        await socket.send('{"type":"completed"}')

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with VerasistClient(
            base_url=f"http://127.0.0.1:{port}", api_key="test-key"
        ) as sdk:

            async def source():
                yield b"\0\0" * 8 if operation == "stt" else "hello"
                yield None

            stream = (
                sdk.audio.transcribe_stream
                if operation == "stt"
                else sdk.audio.synthesize_stream
            )
            events = [event async for event in stream("wf", source())]
    assert events[0]["type"] == "started"
    assert events[-1]["type"] == "completed"
    assert received[0]["type"] == "start"
    assert any(
        isinstance(m, str) and json.loads(m).get("type") == "flush"
        for m in received[1:]
    )


@pytest.mark.asyncio
async def test_socket_provider_error_and_input_iterator_error_propagate():
    async def handler(socket):
        await socket.recv()
        await socket.send('{"type":"started"}')
        await socket.wait_closed()

    async def source():
        raise ValueError("source failed")
        yield b""

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with VerasistClient(base_url=f"http://127.0.0.1:{port}") as sdk:
            with pytest.raises(ValueError, match="source failed"):
                async for _ in sdk.audio.transcribe_stream("wf", source()):
                    pass
