"""Scheduling helpers poll existing runs and never retry creation or redial."""

from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from verasist_sdk import VerasistClient
from verasist_sdk import scheduling
from verasist_sdk.scheduling import (
    SchedulingCancelledError,
    SchedulingError,
    SchedulingTimeoutError,
)


def make_client(handler):
    client = VerasistClient(base_url="http://test", api_key="test-key")
    client._http.close()
    client._http = httpx.Client(
        base_url="http://test/api/v1",
        headers={"X-API-Key": "test-key"},
        transport=httpx.MockTransport(handler),
    )
    return client


@pytest.fixture
def fake_clock(monkeypatch):
    elapsed = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds

    monkeypatch.setattr(
        scheduling, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep)
    )
    return sleeps


def test_client_status_wait_cancel_use_configured_transport_and_auth(fake_clock):
    states = iter(["queued_org", "queued_system", "assigned", "cancelled"])
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"state": next(states), "workflow_run_id": 7})

    with make_client(handle) as client:
        assert client.scheduling.status(7)["state"] == "queued_org"
        assert client.scheduling.wait(7)["state"] == "assigned"
        assert client.scheduling.cancel(7)["state"] == "cancelled"
    assert [request.method for request in requests] == ["GET", "GET", "GET", "DELETE"]
    assert all(
        str(request.url) == "http://test/api/v1/scheduling/runs/7"
        for request in requests
    )
    assert all(request.headers["X-API-Key"] == "test-key" for request in requests)


def test_expiry_raises_typed_session_error(fake_clock):
    with make_client(
        lambda request: httpx.Response(
            200,
            json={"state": "expired", "workflow_run_id": 7, "reason": "queue_timeout"},
        )
    ) as client:
        with pytest.raises(SchedulingError) as error:
            client.scheduling.wait(7)
        assert error.value.session["reason"] == "queue_timeout"


def test_get_429_obeys_retry_after_without_recreating_session(fake_clock):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                429, headers={"Retry-After": "5"}, json={"detail": "rate_limited"}
            )
        return httpx.Response(200, json={"state": "assigned", "workflow_run_id": 7})

    with make_client(handle) as client:
        assert (
            client.scheduling.wait(7, timeout=10, poll_interval=0.1)["state"]
            == "assigned"
        )
    assert fake_clock == [5.0]
    assert [request.method for request in requests] == ["GET", "GET"]


def test_local_timeout_and_abort_never_cancel_server(fake_clock):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"state": "queued_org", "workflow_run_id": 7})

    with make_client(handle) as client:
        with pytest.raises(SchedulingTimeoutError):
            client.scheduling.wait(7, timeout=0.1, poll_interval=1)
        event = Event()
        event.set()
        with pytest.raises(SchedulingCancelledError):
            client.scheduling.wait(7, cancel_event=event)
    assert len(requests) == 1
    assert requests[0].method == "GET"


def test_generated_live_response_accepts_queued_receipt_and_legacy_success():
    from verasist_sdk._generated_models import StartLiveSessionResponse

    fields = {
        "session_token": "dev_test",
        "workflow_run_id": 7,
        "expires_at": "2026-10-01T21:00:00Z",
    }
    value = StartLiveSessionResponse.model_validate(
        {
            **fields,
            "scheduling": {
                "status": "queued_org",
                "state": "queued_org",
                "workflow_run_id": 7,
                "status_url": "/api/v1/scheduling/runs/7",
            },
        }
    )
    assert value.workflow_run_id == 7
    assert value.session_token == "dev_test"
    assert value.scheduling["state"] == "queued_org"
    assert StartLiveSessionResponse.model_validate(fields).scheduling is None
