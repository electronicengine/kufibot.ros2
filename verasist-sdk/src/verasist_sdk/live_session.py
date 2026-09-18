"""Live voice session: a WebRTC connection between a device (mic/speaker)
and a running Verasist workflow assistant, with support for device-side
tools the assistant can invoke mid-conversation.

Requires the `voice` extra (`pip install verasist-sdk[voice]`), which
pulls in `aiortc` and `websockets`. These are optional because most SDK
users only need the workflow-builder surface.

Typical usage::

    import asyncio
    from verasist_sdk import VerasistClient, LiveSession

    async def main():
        client = VerasistClient(base_url="...", api_key="...")
        session = LiveSession(client)

        @session.tool(description="Get the device's battery level")
        def get_battery_level():
            return {"battery_percent": 87}

        @session.on_track
        def on_bot_audio(track):
            ...  # feed `track` frames to your speaker output in real time

        @session.on_transcript
        def on_transcript(event):
            print(event["role"], event["final"], event["text"])

        # `tracks` are added to the peer connection before the offer is
        # created, so they're always included in the initial SDP.
        await session.connect(trigger_uuid="my-trigger-uuid", tracks=[my_microphone_track])

        # Optional: send an occasional camera snapshot for the assistant to
        # see and react to as part of the live conversation.
        await session.send_image(
            image_bytes=jpeg_bytes,
            mime_type="image/jpeg",
            prompt="Describe what you see.",
        )

        await session.wait_closed()

    asyncio.run(main())

Note on ICE: aiortc gathers all local/remote ICE candidates before
`setLocalDescription()`/`setRemoteDescription()` return, so the full
candidate set is always embedded directly in the exchanged SDP. Unlike
browser clients, this SDK does not need to trickle `ice-candidate`
messages over the signaling WebSocket.

Note on transcripts: the assistant's real-time feedback channel
(`rtf-user-transcription` for the caller's speech, `rtf-bot-text` for the
assistant's spoken text — mirroring
`pipecat.utils.enums.RealtimeFeedbackType` on the backend) is surfaced via
`on_transcript`. Live audio itself always arrives as WebRTC media on the
peer connection (see `on_track`), never over the signaling WebSocket.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from ._generated_models import (
    DeviceToolSchema,
    StartLiveSessionRequest,
    StartLiveSessionResponse,
)
from .errors import VerasistSdkError

if TYPE_CHECKING:
    from .client import VerasistClient

try:
    from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
except ImportError as e:  # pragma: no cover - exercised only when extra missing
    raise ImportError(
        "LiveSession requires the 'voice' extra: pip install verasist-sdk[voice]"
    ) from e

try:
    import websockets
except ImportError as e:  # pragma: no cover - exercised only when extra missing
    raise ImportError(
        "LiveSession requires the 'voice' extra: pip install verasist-sdk[voice]"
    ) from e


ToolHandler = Callable[..., Any]


@dataclass
class _RegisteredTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler


class LiveSession:
    """A live WebRTC voice session against a Verasist workflow assistant.

    Register device-side tools with `@session.tool(...)` *before* calling
    `connect()` — the tool schemas are sent to the backend when the
    session is created and merged into the assistant's function list for
    the lifetime of the session.
    """

    def __init__(
        self,
        client: "VerasistClient",
        *,
        initial_context: dict[str, Any] | None = None,
    ):
        self._client = client
        self._initial_context = initial_context
        self._tools: dict[str, _RegisteredTool] = {}

        self.session_token: str | None = None
        self.workflow_run_id: int | None = None
        self.pc: RTCPeerConnection | None = None

        self._ws: Optional[Any] = None
        self._pc_id = f"sdk-{uuid.uuid4().hex}"
        self._listener_task: asyncio.Task | None = None
        self._closed_event = asyncio.Event()
        self._close_reason: dict[str, Any] | None = None

        self._track_callbacks: list[Callable[[Any], None]] = []
        self._voice_event_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._transcript_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._connection_state_callbacks: list[Callable[[str], None]] = []
        self._ice_timeout_task: asyncio.Task | None = None
        self._pending_image_requests: dict[str, asyncio.Future] = {}

    # ── tool registration ──────────────────────────────────────────────

    def tool(
        self,
        func: ToolHandler | None = None,
        *,
        name: str | None = None,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> Any:
        """Decorator that registers a device-side tool the assistant can
        invoke over the signaling channel. Must be called before `connect()`.

        `parameters` is a JSON Schema `object` describing the function's
        arguments (defaults to a no-argument function if omitted).
        """

        def decorator(f: ToolHandler) -> ToolHandler:
            tool_name = name or f.__name__
            self._tools[tool_name] = _RegisteredTool(
                name=tool_name,
                description=description,
                parameters=parameters or {"type": "object", "properties": {}},
                handler=f,
            )
            return f

        if func is not None:
            return decorator(func)
        return decorator

    def on_track(self, callback: Callable[[Any], None]) -> Callable[[Any], None]:
        """Register a callback invoked with each inbound `MediaStreamTrack`
        (the assistant's audio) once the peer connection receives one. Can
        be used as a decorator or called directly."""
        self._track_callbacks.append(callback)
        return callback

    def on_voice_event(self, callback: Callable[[dict[str, Any]], None]) -> Callable:
        """Receive speaking, interruption, mute and camera-turn events (type/payload)."""
        self._voice_event_callbacks.append(callback)
        return callback

    async def configure_camera_turns(self, enabled: bool, *, timeout: float = 3.0):
        """Opt into turn-bound camera images; unsupported servers fail explicitly."""
        return await self._camera_command({'action': 'configure', 'enabled': enabled}, timeout)

    async def complete_camera_turn(self, turn_id: str, *, timeout: float = 2.0):
        """Finish the attachment (including unavailable images) for exactly this turn."""
        return await self._camera_command({'action': 'complete', 'turn_id': turn_id}, timeout)

    async def _camera_command(self, payload: dict, timeout: float):
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending_image_requests[request_id] = future
        try:
            await self._send({'type': 'device-camera-turn',
                              'payload': {**payload, 'request_id': request_id}})
            result = await asyncio.wait_for(future, timeout)
            if result.get('status') != 'success':
                raise VerasistSdkError(result.get('error', 'Camera configuration failed'))
            return result
        finally:
            self._pending_image_requests.pop(request_id, None)

    def on_transcript(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> Callable[[dict[str, Any]], None]:
        """Register a callback invoked with each live transcript event:
        `{"role": "user" | "assistant", "text": str, "final": bool}`. User
        events fire interim (`final=False`) then final; assistant events are
        always final. Can be used as a decorator or called directly."""
        self._transcript_callbacks.append(callback)
        return callback

    def on_connection_state(self, callback: Callable[[str], None]) -> Callable[[str], None]:
        """Register a callback invoked with each WebRTC `connectionState`
        transition (`"connecting"`, `"connected"`, `"failed"`, ...). Useful
        for surfacing ICE stalls without reaching into `session.pc` directly.
        Can be used as a decorator or called directly."""
        self._connection_state_callbacks.append(callback)
        return callback

    # ── session lifecycle ───────────────────────────────────────────────

    async def start(
        self,
        *,
        trigger_uuid: str | None = None,
        workflow_uuid: str | None = None,
    ) -> StartLiveSessionResponse:
        """Create the live session on the backend over HTTP. Pass exactly
        one of `trigger_uuid` or `workflow_uuid`."""
        if bool(trigger_uuid) == bool(workflow_uuid):
            raise ValueError("Pass exactly one of trigger_uuid or workflow_uuid")

        device_tools = [
            DeviceToolSchema(
                name=t.name, description=t.description, parameters=t.parameters
            )
            for t in self._tools.values()
        ] or None
        body = StartLiveSessionRequest(
            initial_context=self._initial_context, device_tools=device_tools
        )

        if trigger_uuid:
            resp = await asyncio.to_thread(
                self._client.start_live_session, trigger_uuid, body=body
            )
        else:
            resp = await asyncio.to_thread(
                self._client.start_live_session_by_workflow,
                workflow_uuid,
                body=body,
            )

        self.session_token = resp.session_token
        self.workflow_run_id = resp.workflow_run_id
        return resp

    async def connect(
        self,
        *,
        trigger_uuid: str | None = None,
        workflow_uuid: str | None = None,
        call_context_vars: dict[str, Any] | None = None,
        tracks: list[Any] | None = None,
        ice_timeout_secs: float | None = 30,
    ) -> None:
        """Start the session (if not already started) and establish the
        WebRTC peer connection + signaling WebSocket. Returns once the
        SDP answer has been applied — the connection continues negotiating
        ICE in the background.

        `tracks` (e.g. a microphone `MediaStreamTrack`) are added to the
        peer connection *before* the offer is created, so they're always
        part of the initial SDP.

        `ice_timeout_secs` closes the session (via `wait_closed()`, with
        `{"reason": "ice_timeout"}`) if the connection hasn't reached
        `connected`/`completed` within that many seconds — pass `None` to
        disable. This guards against ICE getting stuck in `checking`
        forever (e.g. no reachable TURN relay from behind a strict NAT),
        which would otherwise hang indefinitely."""
        if not self.session_token:
            await self.start(trigger_uuid=trigger_uuid, workflow_uuid=workflow_uuid)

        ice_servers = await self._fetch_ice_servers()
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))

        for track in tracks or []:
            self.pc.addTrack(track)

        @self.pc.on("track")
        def _on_track(track: Any) -> None:
            for callback in self._track_callbacks:
                callback(track)

        @self.pc.on("connectionstatechange")
        def _on_connection_state_change() -> None:
            assert self.pc is not None
            state = self.pc.connectionState
            for callback in self._connection_state_callbacks:
                callback(state)
            if state in ("connected", "completed"):
                self._cancel_ice_timeout()
            elif state == "failed":
                self._fail(reason="ice_failed")

        self._ws = await websockets.connect(self._build_ws_url(), max_size=None)
        self._listener_task = asyncio.create_task(self._listen())

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        await self._send(
            {
                "type": "offer",
                "payload": {
                    "sdp": self.pc.localDescription.sdp,
                    "type": "offer",
                    "pc_id": self._pc_id,
                    "call_context_vars": call_context_vars or {},
                },
            }
        )

        if ice_timeout_secs is not None:
            self._ice_timeout_task = asyncio.create_task(
                self._watch_ice_timeout(ice_timeout_secs)
            )

    async def wait_closed(self) -> dict[str, Any] | None:
        """Block until the session ends (server sends `call-ended`, the
        WebSocket closes, ICE fails/times out, or `close()` is called).
        Returns the close payload, if any — includes `{"reason":
        "ice_failed"}` / `{"reason": "ice_timeout"}` for connectivity
        failures detected client-side."""
        await self._closed_event.wait()
        return self._close_reason

    async def close(self) -> None:
        """Tear down the WebSocket and peer connection."""
        self._cancel_ice_timeout()
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self.pc is not None:
            await self.pc.close()
            self.pc = None
        for future in self._pending_image_requests.values():
            if not future.done():
                future.cancel()
        self._pending_image_requests.clear()
        self._closed_event.set()

    async def send_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        prompt: str | None = None,
        trigger_response: bool = True,
        turn_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send a snapshot image (e.g. a robot camera frame) to the assistant
        mid-session, as a `device-image` signaling message.

        The image is added to the active conversation's multimodal LLM
        context. When `trigger_response` is True (default — use this for an
        explicit "look at this" request, e.g. from a tool call), the
        assistant responds to it like a normal conversation turn, arriving
        via the usual `on_track` audio / `on_transcript` channels. Pass
        `trigger_response=False` for an image sent alongside/after a spoken
        turn the assistant is *already* generating a reply to (e.g.
        auto-attaching the latest camera frame to every utterance) — this
        silently adds the image to context for future turns WITHOUT forcing
        a second, redundant LLM generation that would race the reply
        already in flight for that turn and produce a duplicate/unsolicited
        bot response.

        Either way, this call's return value only reflects whether the
        backend *accepted* the image (or raises on validation/backend
        failure or on timeout) — it is never the assistant's answer.

        Args:
            image_bytes: Raw encoded image bytes (already JPEG/PNG/WebP —
                this does not encode raw pixels for you).
            mime_type: The image's encoding, e.g. "image/jpeg".
            prompt: Optional instruction for how the assistant should
                interpret the image (e.g. "Describe what you see.").
            trigger_response: Whether the backend should generate a fresh
                assistant reply for this image. See above.
            timeout: Seconds to wait for the backend's accept/reject before
                raising `VerasistSdkError`.
        """
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_image_requests[request_id] = future

        try:
            await self._send(
                {
                    "type": "device-image",
                    "payload": {
                        "request_id": request_id,
                        "mime_type": mime_type,
                        "encoding": "base64",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                        "prompt": prompt,
                        "trigger_response": trigger_response,
                        **({'turn_id': turn_id} if turn_id is not None else {}),
                    },
                }
            )

            try:
                result = await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError as e:
                raise VerasistSdkError(
                    "Timed out waiting for device-image-result"
                ) from e
        finally:
            self._pending_image_requests.pop(request_id, None)

        if result.get("status") != "success":
            raise VerasistSdkError(result.get("error") or "Failed to send image")
        return result

    # ── internals ────────────────────────────────────────────────────

    def _fail(self, *, reason: str) -> None:
        """Close-reason set by a client-detected failure (ICE failed/timed
        out) rather than a server message. First reason wins."""
        if self._closed_event.is_set():
            return
        self._close_reason = {"reason": reason}
        self._closed_event.set()

    def _cancel_ice_timeout(self) -> None:
        if self._ice_timeout_task:
            self._ice_timeout_task.cancel()
            self._ice_timeout_task = None

    async def _watch_ice_timeout(self, timeout_secs: float) -> None:
        await asyncio.sleep(timeout_secs)
        if self.pc is not None and self.pc.connectionState not in (
            "connected",
            "completed",
            "closed",
        ):
            self._fail(reason="ice_timeout")

    async def _fetch_ice_servers(self) -> list[RTCIceServer]:
        ice_servers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
        try:
            turn = await asyncio.to_thread(
                self._client.get_live_session_turn_credentials, self.session_token
            )
        except Exception:
            return ice_servers
        if turn.uris:
            ice_servers.append(
                RTCIceServer(
                    urls=turn.uris, username=turn.username, credential=turn.password
                )
            )
        return ice_servers

    def _build_ws_url(self) -> str:
        scheme, _, rest = self._client.base_url.partition("://")
        ws_scheme = "wss" if scheme == "https" else "ws"
        return f"{ws_scheme}://{rest}/api/v1/ws/device/signaling/{self.session_token}"

    async def _send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            raise VerasistSdkError("Signaling WebSocket is not connected")
        await self._ws.send(json.dumps(message))

    async def _listen(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if not self._closed_event.is_set():
                self._closed_event.set()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        payload = message.get("payload") or {}

        if msg_type == "answer":
            assert self.pc is not None
            await self.pc.setRemoteDescription(
                RTCSessionDescription(sdp=payload["sdp"], type="answer")
            )
        elif msg_type == "tool-call":
            asyncio.create_task(self._handle_tool_call(payload))
        elif msg_type in ("device-image-result", "device-camera-turn-result"):
            request_id = payload.get("request_id")
            future = self._pending_image_requests.get(request_id)
            if future and not future.done():
                future.set_result(payload)
        elif msg_type == "call-ended":
            self._close_reason = payload
            self._closed_event.set()
        elif msg_type == "error":
            self._close_reason = payload
            self._closed_event.set()
        elif msg_type in {
            'rtf-bot-started-speaking', 'rtf-bot-stopped-speaking', 'rtf-bot-interrupted',
            'rtf-user-mute-started', 'rtf-user-mute-stopped',
            'rtf-user-turn-started', 'rtf-user-turn-final', 'rtf-camera-attachment',
        }:
            for callback in self._voice_event_callbacks:
                try:
                    callback({'type': msg_type, 'payload': payload})
                except Exception:
                    # Application diagnostics must never stop the signaling reader.
                    import logging
                    logging.getLogger(__name__).exception('Voice event callback failed')
        elif msg_type == "rtf-user-transcription":
            self._emit_transcript(
                {
                    "role": "user",
                    "text": payload.get("text", ""),
                    "final": bool(payload.get("final")),
                }
            )
        elif msg_type == "rtf-bot-text":
            self._emit_transcript(
                {"role": "assistant", "text": payload.get("text", ""), "final": True}
            )
        # "ice-candidate" is intentionally ignored — see module docstring.

    def _emit_transcript(self, event: dict[str, Any]) -> None:
        for callback in self._transcript_callbacks:
            callback(event)

    async def _handle_tool_call(self, payload: dict[str, Any]) -> None:
        tool_call_id = payload.get("tool_call_id")
        name = payload.get("name")
        arguments = payload.get("arguments") or {}

        tool = self._tools.get(name) if name else None
        if tool is None:
            result: Any = {"status": "error", "error": f"Unknown tool: {name!r}"}
        else:
            try:
                value = tool.handler(**arguments)
                if inspect.isawaitable(value):
                    value = await value
                result = value
            except Exception as e:  # noqa: BLE001 - report to the assistant, don't crash the session
                result = {"status": "error", "error": str(e)}

        await self._send(
            {
                "type": "tool-result",
                "payload": {"tool_call_id": tool_call_id, "result": result},
            }
        )
