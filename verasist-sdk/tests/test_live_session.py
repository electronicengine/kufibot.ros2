"""Pure-logic tests for `LiveSession` (tool dispatch, message routing).

No real network I/O — `_ws`/`pc` are mocked so these run without an
aiortc peer connection or a live signaling server.
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from verasist_sdk import VerasistClient
from verasist_sdk.errors import VerasistSdkError
from verasist_sdk.live_session import LiveSession


def _make_session() -> LiveSession:
    client = VerasistClient(base_url="http://localhost:8000", api_key="test-key")
    return LiveSession(client)


def test_tool_decorator_defaults_name_and_parameters():
    session = _make_session()

    @session.tool(description="Get device battery level")
    def get_battery_level():
        return {"battery_percent": 87}

    assert "get_battery_level" in session._tools
    tool = session._tools["get_battery_level"]
    assert tool.description == "Get device battery level"
    assert tool.parameters == {"type": "object", "properties": {}}


def test_tool_decorator_explicit_name_and_parameters():
    session = _make_session()

    @session.tool(name="custom_name", parameters={"type": "object", "properties": {"x": {"type": "number"}}})
    def handler(x: float):
        return {"doubled": x * 2}

    assert "custom_name" in session._tools
    assert "handler" not in session._tools


def test_build_ws_url_http_and_https():
    session = _make_session()
    session.session_token = "dev_abc123"
    assert session._build_ws_url() == "ws://localhost:8000/api/v1/ws/device/signaling/dev_abc123"

    session._client.base_url = "https://api.verasist.ai"
    assert session._build_ws_url() == "wss://api.verasist.ai/api/v1/ws/device/signaling/dev_abc123"


@pytest.mark.asyncio
async def test_handle_tool_call_dispatches_sync_handler_and_sends_result():
    session = _make_session()
    session._send = AsyncMock()

    @session.tool(name="get_status")
    def get_status():
        return {"ok": True}

    await session._handle_tool_call({"tool_call_id": "call-1", "name": "get_status", "arguments": {}})

    session._send.assert_awaited_once_with(
        {"type": "tool-result", "payload": {"tool_call_id": "call-1", "result": {"ok": True}}}
    )


@pytest.mark.asyncio
async def test_handle_tool_call_dispatches_async_handler():
    session = _make_session()
    session._send = AsyncMock()

    @session.tool(name="get_status")
    async def get_status():
        return {"ok": True}

    await session._handle_tool_call({"tool_call_id": "call-2", "name": "get_status", "arguments": {}})

    session._send.assert_awaited_once_with(
        {"type": "tool-result", "payload": {"tool_call_id": "call-2", "result": {"ok": True}}}
    )


@pytest.mark.asyncio
async def test_handle_tool_call_passes_arguments():
    session = _make_session()
    session._send = AsyncMock()

    @session.tool(name="add")
    def add(a: int, b: int):
        return {"sum": a + b}

    await session._handle_tool_call({"tool_call_id": "call-3", "name": "add", "arguments": {"a": 2, "b": 3}})

    session._send.assert_awaited_once_with(
        {"type": "tool-result", "payload": {"tool_call_id": "call-3", "result": {"sum": 5}}}
    )


@pytest.mark.asyncio
async def test_handle_tool_call_unknown_tool_returns_error():
    session = _make_session()
    session._send = AsyncMock()

    await session._handle_tool_call({"tool_call_id": "call-4", "name": "missing", "arguments": {}})

    sent = session._send.await_args.args[0]
    assert sent["payload"]["tool_call_id"] == "call-4"
    assert sent["payload"]["result"]["status"] == "error"


@pytest.mark.asyncio
async def test_handle_tool_call_handler_exception_returns_error():
    session = _make_session()
    session._send = AsyncMock()

    @session.tool(name="boom")
    def boom():
        raise ValueError("kaboom")

    await session._handle_tool_call({"tool_call_id": "call-5", "name": "boom", "arguments": {}})

    sent = session._send.await_args.args[0]
    assert sent["payload"]["result"] == {"status": "error", "error": "kaboom"}


@pytest.mark.asyncio
async def test_handle_message_call_ended_sets_closed_event():
    session = _make_session()

    await session._handle_message({"type": "call-ended", "payload": {"reason": "done"}})

    assert session._closed_event.is_set()
    assert session._close_reason == {"reason": "done"}


@pytest.mark.asyncio
async def test_handle_message_answer_sets_remote_description():
    session = _make_session()
    session.pc = MagicMock()
    session.pc.setRemoteDescription = AsyncMock()

    await session._handle_message({"type": "answer", "payload": {"sdp": "v=0..."}})

    session.pc.setRemoteDescription.assert_awaited_once()
    called_desc = session.pc.setRemoteDescription.await_args.args[0]
    assert called_desc.sdp == "v=0..."
    assert called_desc.type == "answer"


@pytest.mark.asyncio
async def test_handle_message_user_transcription_emits_transcript_event():
    session = _make_session()
    events = []
    session.on_transcript(events.append)

    await session._handle_message(
        {"type": "rtf-user-transcription", "payload": {"text": "hello", "final": False}}
    )

    assert events == [{"role": "user", "text": "hello", "final": False}]


@pytest.mark.asyncio
async def test_handle_message_bot_text_emits_transcript_event():
    session = _make_session()
    events = []
    session.on_transcript(events.append)

    await session._handle_message({"type": "rtf-bot-text", "payload": {"text": "hi there"}})

    assert events == [{"role": "assistant", "text": "hi there", "final": True}]


def test_on_connection_state_registers_callback():
    session = _make_session()
    states = []

    @session.on_connection_state
    def _on_state(state):
        states.append(state)

    assert _on_state in session._connection_state_callbacks
    session._connection_state_callbacks[0]("connecting")
    assert states == ["connecting"]


def test_fail_sets_closed_event_and_reason():
    session = _make_session()

    session._fail(reason="ice_failed")

    assert session._closed_event.is_set()
    assert session._close_reason == {"reason": "ice_failed"}


def test_fail_does_not_overwrite_existing_reason():
    session = _make_session()

    session._fail(reason="ice_failed")
    session._fail(reason="ice_timeout")

    assert session._close_reason == {"reason": "ice_failed"}


@pytest.mark.asyncio
async def test_watch_ice_timeout_fails_when_never_connected():
    session = _make_session()
    session.pc = MagicMock(connectionState="checking")

    await session._watch_ice_timeout(0)

    assert session._closed_event.is_set()
    assert session._close_reason == {"reason": "ice_timeout"}


@pytest.mark.asyncio
async def test_watch_ice_timeout_noop_when_already_connected():
    session = _make_session()
    session.pc = MagicMock(connectionState="connected")

    await session._watch_ice_timeout(0)

    assert not session._closed_event.is_set()
    assert session._close_reason is None


def test_cancel_ice_timeout_cancels_and_clears_task():
    session = _make_session()
    task = MagicMock()
    session._ice_timeout_task = task

    session._cancel_ice_timeout()

    task.cancel.assert_called_once()
    assert session._ice_timeout_task is None


@pytest.mark.asyncio
async def test_send_image_success_flow():
    session = _make_session()
    session._send = AsyncMock()

    task = asyncio.get_event_loop().create_task(
        session.send_image(image_bytes=b"\xff\xd8\xff", mime_type="image/jpeg", prompt="What is this?")
    )
    await asyncio.sleep(0)  # let send_image register the pending future and call _send

    sent_payload = session._send.await_args.args[0]["payload"]
    await session._handle_message(
        {
            "type": "device-image-result",
            "payload": {"request_id": sent_payload["request_id"], "status": "success"},
        }
    )
    result = await task

    assert result == {"request_id": sent_payload["request_id"], "status": "success"}
    assert sent_payload["mime_type"] == "image/jpeg"
    assert sent_payload["encoding"] == "base64"
    assert sent_payload["prompt"] == "What is this?"
    assert sent_payload["trigger_response"] is True
    assert base64.b64decode(sent_payload["data"]) == b"\xff\xd8\xff"
    assert session._pending_image_requests == {}


@pytest.mark.asyncio
async def test_send_image_trigger_response_false_is_forwarded():
    session = _make_session()
    session._send = AsyncMock()

    task = asyncio.get_event_loop().create_task(
        session.send_image(
            image_bytes=b"\xff\xd8\xff",
            mime_type="image/jpeg",
            trigger_response=False,
        )
    )
    await asyncio.sleep(0)

    sent_payload = session._send.await_args.args[0]["payload"]
    await session._handle_message(
        {
            "type": "device-image-result",
            "payload": {"request_id": sent_payload["request_id"], "status": "success"},
        }
    )
    await task

    assert sent_payload["trigger_response"] is False


@pytest.mark.asyncio
async def test_send_image_raises_on_error_result():
    session = _make_session()
    session._send = AsyncMock()

    task = asyncio.get_event_loop().create_task(
        session.send_image(image_bytes=b"data", mime_type="image/jpeg")
    )
    await asyncio.sleep(0)
    request_id = session._send.await_args.args[0]["payload"]["request_id"]
    await session._handle_message(
        {
            "type": "device-image-result",
            "payload": {"request_id": request_id, "status": "error", "error": "too big"},
        }
    )

    with pytest.raises(VerasistSdkError, match="too big"):
        await task
    assert session._pending_image_requests == {}


@pytest.mark.asyncio
async def test_send_image_times_out_when_no_result_arrives():
    session = _make_session()
    session._send = AsyncMock()

    with pytest.raises(VerasistSdkError, match="Timed out"):
        await session.send_image(image_bytes=b"data", mime_type="image/jpeg", timeout=0.01)

    assert session._pending_image_requests == {}


@pytest.mark.asyncio
async def test_close_cancels_pending_image_futures():
    session = _make_session()
    session._ws = AsyncMock()

    future = asyncio.get_event_loop().create_future()
    session._pending_image_requests["req-1"] = future

    await session.close()

    assert future.cancelled()
    assert session._pending_image_requests == {}
