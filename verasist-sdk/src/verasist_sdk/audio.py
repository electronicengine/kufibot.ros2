"""Standalone billed speech. No voice/WebRTC installation or session needed."""

import asyncio
import json
from contextlib import suppress
from typing import AsyncIterable, AsyncIterator, BinaryIO
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from .errors import ApiError
from ._generated_models import AudioCapabilities


class Audio:
    def __init__(self, client):
        self._client = client

    @staticmethod
    def _path(workflow_uuid: str, operation: str) -> str:
        return f"/audio/workflows/{quote(workflow_uuid, safe='')}/{operation}"

    def capabilities(self, workflow_uuid: str) -> AudioCapabilities:
        return AudioCapabilities.model_validate(
            self._client._request("GET", self._path(workflow_uuid, "capabilities"))
        )

    def transcribe(
        self,
        workflow_uuid: str,
        audio: bytes | BinaryIO,
        *,
        request_id: str | None = None,
        filename: str = "audio.wav",
    ) -> dict:
        response = self._client._http.post(
            self._path(workflow_uuid, "transcriptions"),
            data={"request_id": request_id or str(uuid4())},
            files={"file": (filename, audio)},
            timeout=180,
        )
        self._check(response)
        return response.json()

    def synthesize(
        self, workflow_uuid: str, text: str, *, request_id: str | None = None
    ) -> bytes:
        """Return a complete mono WAV file. Provider submissions are billed."""
        response = self._client._http.post(
            self._path(workflow_uuid, "speech"),
            json={"request_id": request_id or str(uuid4()), "text": text},
            timeout=180,
        )
        self._check(response)
        return response.content

    def synthesize_chunks(
        self, workflow_uuid: str, text: str, *, request_id: str | None = None
    ):
        """Stream mono PCM16 at 16 kHz for a complete input text."""
        with self._client._http.stream(
            "POST",
            self._path(workflow_uuid, "speech"),
            json={
                "request_id": request_id or str(uuid4()),
                "text": text,
                "stream": True,
            },
            timeout=180,
        ) as response:
            if response.is_error:
                response.read()
            self._check(response)
            yield from response.iter_bytes()

    def transcribe_stream(
        self,
        workflow_uuid: str,
        audio: AsyncIterable[bytes | None],
        *,
        request_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Yield transcript/control events. Input None flushes a segment."""
        return self._stream(workflow_uuid, "transcriptions", audio, request_id)

    def synthesize_stream(
        self,
        workflow_uuid: str,
        text: AsyncIterable[str | None],
        *,
        request_id: str | None = None,
    ) -> AsyncIterator[dict | bytes]:
        """Yield control events and PCM16 bytes. Input None explicitly flushes text."""
        return self._stream(workflow_uuid, "speech", text, request_id)

    async def _stream(self, workflow_uuid, operation, source, request_id):
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed
        except ImportError as exc:
            raise ImportError(
                "Audio streaming requires pip install verasist-sdk[audio]"
            ) from exc
        url = urlsplit(self._client.base_url)
        url = urlunsplit(
            (
                "wss" if url.scheme == "https" else "ws",
                url.netloc,
                url.path + "/api/v1" + self._path(workflow_uuid, operation + "/stream"),
                "",
                "",
            )
        )
        headers = {"X-API-Key": self._client.api_key} if self._client.api_key else {}
        async with connect(
            url,
            additional_headers=headers,
            max_size=1024 * 1024,
            max_queue=8,
            open_timeout=30,
            close_timeout=5,
        ) as socket:
            await socket.send(
                json.dumps({"type": "start", "request_id": request_id or str(uuid4())})
            )
            first = json.loads(await asyncio.wait_for(socket.recv(), 45))
            self._event(first)
            if first.get("type") != "started":
                raise ApiError(502, "Audio stream did not start")
            yield first

            async def send():
                async for chunk in source:
                    if chunk is None:
                        await socket.send('{"type":"flush"}')
                    elif operation == "transcriptions":
                        if not isinstance(chunk, bytes) or len(chunk) % 2:
                            raise ValueError("Expected aligned PCM16 bytes")
                        for offset in range(0, len(chunk), 32000):
                            await socket.send(chunk[offset : offset + 32000])
                    else:
                        if not isinstance(chunk, str):
                            raise ValueError("Expected text chunks")
                        await socket.send(json.dumps({"type": "text", "text": chunk}))
                await socket.send('{"type":"end"}')

            sender = asyncio.create_task(send())
            receiver = None
            try:
                while True:
                    if sender.done():
                        await sender
                    receiver = asyncio.create_task(socket.recv())
                    watched = (receiver,) if sender.done() else (receiver, sender)
                    done, _ = await asyncio.wait(
                        watched, timeout=150, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not done:
                        raise ApiError(504, "Audio stream timed out")
                    if sender in done:
                        await sender  # Surface iterator/transport errors immediately.
                    raw = await asyncio.wait_for(receiver, 150)
                    if isinstance(raw, bytes):
                        yield raw
                        continue
                    event = json.loads(raw)
                    self._event(event)
                    yield event
                    if event.get("type") == "completed":
                        return
            except ConnectionClosed as exc:
                raise ApiError(502, "Audio stream interrupted") from exc
            finally:
                sender.cancel()
                if receiver is not None:
                    receiver.cancel()
                await asyncio.gather(
                    sender, *([receiver] if receiver else []), return_exceptions=True
                )
                with suppress(Exception):
                    await socket.send('{"type":"cancel"}')

    @staticmethod
    def _event(event):
        if event.get("type") == "error":
            raise ApiError(
                event.get("status", 502),
                event.get("message", "Audio operation failed"),
                body=event,
            )

    @staticmethod
    def _check(response):
        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise ApiError(response.status_code, str(body), body=body)
