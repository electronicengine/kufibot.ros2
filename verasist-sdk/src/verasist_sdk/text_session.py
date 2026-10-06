"""Server-side text sessions, without optional voice dependencies."""
import json
from typing import Any, Iterator, Literal, TypedDict
from urllib.parse import quote

from .errors import ApiError
from .scheduling import SessionScheduling


class TextStreamEvent(TypedDict):
    event: Literal["session.started", "assistant.delta", "tool.started", "tool.completed", "session.completed", "session.queued"]
    data: dict[str, Any] | SessionScheduling


class TextSessions:
    def __init__(self, client):
        self._client = client

    def check(self, workflow_uuid: str) -> dict:
        return self._client._request("GET", f"/apps/workflows/{quote(workflow_uuid, safe='')}/check")

    def create(self, workflow_uuid: str, *, request_id: str, initial_context: dict | None = None,
               tool_transport: dict | None = None, instructions: str = "") -> dict:
        return self._client._request("POST", f"/apps/workflows/{quote(workflow_uuid, safe='')}/sessions", json={
            "request_id": request_id, "initial_context": initial_context or {},
            "tool_transport": tool_transport, "instructions": instructions,
        })

    def get(self, run_id: int) -> dict:
        return self._client._request("GET", f"/apps/sessions/{run_id}")

    def execute(self, run_id: int) -> dict:
        return self._client._request("POST", f"/apps/sessions/{run_id}/execute", json={})

    def send(self, run_id: int, text: str, expected_revision: int) -> dict:
        return self._client._request("POST", f"/apps/sessions/{run_id}/messages", json={
            "text": text, "expected_revision": expected_revision,
        })

    def close(self, run_id: int) -> dict:
        return self._client._request("POST", f"/apps/sessions/{run_id}/close", json={})

    def execute_stream(self, run_id: int) -> Iterator[TextStreamEvent]:
        return self._stream(f"/apps/sessions/{run_id}/execute", {})

    def send_stream(self, run_id: int, text: str, expected_revision: int) -> Iterator[TextStreamEvent]:
        return self._stream(f"/apps/sessions/{run_id}/messages", {
            "text": text, "expected_revision": expected_revision,
        })

    def _stream(self, path: str, body: dict) -> Iterator[TextStreamEvent]:
        with self._client._http.stream("POST", path, json=body,
                                      headers={"Accept": "text/event-stream"}) as response:
            if response.is_error:
                response.read()
                try:
                    error = response.json()
                except ValueError:
                    error = response.text
                raise ApiError(response.status_code, str(error), body=error)
            if response.status_code == 202:
                response.read()
                yield {"event": "session.queued", "data": response.json()}
                return
            event = ""
            data = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))
                elif not line and data:
                    payload = json.loads("\n".join(data))
                    data = []
                    if event == "session.error":
                        raise ApiError(502, payload["message"], body=payload)
                    if event in {"session.started", "assistant.delta", "tool.started", "tool.completed", "session.completed", "session.queued"}:
                        yield {"event": event, "data": payload}
                        if event in {"session.completed", "session.queued"}:
                            return
                    event = ""
            raise ApiError(502, "Stream interrupted; reload the session before sending again")
