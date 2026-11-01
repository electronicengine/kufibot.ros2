import json

import httpx
import pytest

from verasist_sdk import ApiError, VerasistClient


class SplitBytes(httpx.SyncByteStream):
    def __init__(self, content):
        self.content = content.encode()

    def __iter__(self):
        for byte in self.content:
            yield bytes([byte])


def event(kind, data):
    return f"event: {kind}\r\ndata: {json.dumps(data, ensure_ascii=False)}\r\n\r\n"


def client_with(handler):
    client = VerasistClient(api_key="test-key")
    client._http.close()
    client._http = httpx.Client(base_url="http://test/api/v1", headers={"X-API-Key": "test-key"},
                               transport=httpx.MockTransport(handler))
    return client


def test_stream_is_incremental_and_decodes_split_utf8():
    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(200, stream=SplitBytes(
            ": heartbeat\n\n" + event("session.started", {"run_id": 1}) +
            event("assistant.delta", {"turn_id": "a", "segment_id": "0", "sequence": 1, "delta": "İyi 🌍"}) +
            event("session.completed", {"run_id": 1, "revision": 3})))

    with client_with(handler) as client:
        events = client.text_sessions.send_stream(1, "Merhaba", 2)
        assert next(events)["event"] == "session.started"
        assert next(events)["data"]["delta"] == "İyi 🌍"
        assert next(events)["data"]["revision"] == 3
        assert list(events) == []
    assert received[0].headers["accept"] == "text/event-stream"
    assert received[0].headers["x-api-key"] == "test-key"
    assert json.loads(received[0].content) == {"text": "Merhaba", "expected_revision": 2}


@pytest.mark.parametrize("status,content", [
    (409, '{"detail":"Stale revision"}'),
    (200, event("session.error", {"message": "Turn failed"})),
    (200, event("assistant.delta", {"delta": "partial"})),
])
def test_errors_never_retry_the_post(status, content):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, stream=SplitBytes(content))
    with client_with(handler) as client:
        with pytest.raises(ApiError):
            list(client.text_sessions.execute_stream(1))
    assert len(calls) == 1


def test_json_session_methods_keep_revisions_and_paths():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"run_id": 1, "revision": 2})
    with client_with(handler) as client:
        client.text_sessions.check("workflow")
        client.text_sessions.create("workflow", request_id="request")
        client.text_sessions.execute(1)
        client.text_sessions.send(1, "hello", 2)
        assert client.text_sessions.get(1)["revision"] == 2
        client.text_sessions.close(1)
    assert calls == [("GET", "/api/v1/public/agent/workflow/workflow/check"),
                     ("POST", "/api/v1/public/agent/workflow/workflow"),
                     ("POST", "/api/v1/public/agent/runs/1/execute"),
                     ("POST", "/api/v1/public/agent/runs/1/messages"),
                     ("GET", "/api/v1/public/agent/runs/1"),
                     ("POST", "/api/v1/public/agent/runs/1/close")]


def test_tool_lifecycle_events():
    tool = {"run_id": 1, "turn_id": "t", "tool_call_id": "a", "function_name": "read_profile", "status": "running"}
    payload = event("tool.started", tool) + event("tool.completed", {**tool, "status": "completed"}) + event("session.completed", {"run_id": 1})
    client = client_with(lambda request: httpx.Response(200, text=payload))
    assert [item["event"] for item in client.text_sessions.execute_stream(1)] == ["tool.started", "tool.completed", "session.completed"]


@pytest.mark.parametrize("wire_format", ["http202", "sse"])
def test_queued_receipt_is_terminal_without_interrupted_stream_error(wire_format):
    receipt = {
        "workflow_run_id": 7, "state": "queued_org", "status": "queued_org",
        "status_url": "/api/v1/scheduling/runs/7", "deadline": "2026-10-01T22:00:00Z",
    }
    requests = []
    def handle(request):
        requests.append(request)
        if wire_format == "http202":
            return httpx.Response(202, json=receipt, headers={"Location": receipt["status_url"]})
        return httpx.Response(200, stream=SplitBytes(event("session.queued", receipt)))
    with client_with(handle) as client:
        stream = client.text_sessions.execute_stream(7)
        queued = next(stream)
        assert queued == {"event": "session.queued", "data": receipt}
        assert queued["data"]["workflow_run_id"] == 7
        assert queued["data"]["status_url"] == "/api/v1/scheduling/runs/7"
        assert list(stream) == []
    assert len(requests) == 1
    assert requests[0].method == "POST"


@pytest.mark.parametrize("run_type", ["voice", "video", "text"])
@pytest.mark.parametrize("by_workflow", [False, True])
def test_generated_run_start_uses_shared_endpoint(run_type, by_workflow):
    from verasist_sdk._generated_models import TriggerCallRequest
    import uuid
    received = []
    def handler(request):
        received.append(request)
        return httpx.Response(200, json={"run_id": 1, "revision": 0} if run_type == "text" else {"workflow_run_id": 1, "session_token": "device-token", "expires_at": "2026-10-11T00:00:00Z"})
    with client_with(handler) as client:
        body = TriggerCallRequest(run_type=run_type, request_id=uuid.uuid4() if run_type == "text" else None)
        method = client.start_run_by_workflow if by_workflow else client.start_run
        result = method("workflow" if by_workflow else "trigger", body=body)
        assert (result.run_id if run_type == "text" else result.workflow_run_id) == 1
    assert received[0].url.path == ("/api/v1/public/agent/workflow/workflow" if by_workflow else "/api/v1/public/agent/trigger")
    assert json.loads(received[0].content)["run_type"] == run_type
