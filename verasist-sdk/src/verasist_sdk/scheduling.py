"""Observe queued sessions without retrying session creation or call dialing."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from threading import Event
import time
from typing import Any, Literal, TypedDict, cast

import httpx

from .errors import ApiError, VerasistSdkError

SchedulingState = Literal[
    "queued_campaign",
    "queued_org",
    "queued_system",
    "assigned",
    "running",
    "finished",
    "cancelled",
    "expired",
    "failed",
]


class SessionScheduling(TypedDict, total=False):
    id: str
    workflow_run_id: int
    organization_id: int
    kind: Literal["inbound", "outbound", "webrtc", "chat"]
    state: SchedulingState
    status: SchedulingState
    campaign_id: int | None
    worker_id: str | None
    queued_at: str
    deadline: str
    assigned_at: str | None
    finished_at: str | None
    reason: str | None
    status_url: str
    expires_at: str
    result: Any


class SchedulingError(VerasistSdkError):
    """The session expired, was cancelled, or failed before admission."""

    def __init__(self, session: SessionScheduling):
        self.session = session
        super().__init__(
            f"Session {session.get('workflow_run_id')} {session['state']}: {session.get('reason') or session['state']}"
        )


class SchedulingTimeoutError(VerasistSdkError, TimeoutError):
    """Waiting timed out; the server-side session remains queued/running."""


class SchedulingCancelledError(VerasistSdkError):
    """Local waiting was cancelled; no server-side cancellation was sent."""


class _SchedulingApiError(ApiError):
    def __init__(
        self, status_code: int, message: str, body: object, retry_after: float | None
    ):
        super().__init__(status_code, message, body)
        self.retry_after = retry_after


def _retry_after(value: object) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(str(value))
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")


class Scheduling:
    """Uses the client's configured HTTP transport and API-key authentication."""

    def __init__(self, client):
        self._client = client

    def _request(
        self, method: str, run_id: int, timeout: float | None = None
    ) -> SessionScheduling:
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
            raise ValueError("run_id must be a positive integer")
        options = {"timeout": timeout} if timeout is not None else {}
        response = self._client._http.request(
            method, f"/scheduling/runs/{run_id}", **options
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.is_error:
            message = (
                body.get("detail", body.get("message", response.reason_phrase))
                if isinstance(body, dict)
                else body
            )
            retry = _retry_after(response.headers.get("Retry-After"))
            if retry is None and isinstance(body, dict):
                retry = _retry_after(body.get("retry_after"))
            raise _SchedulingApiError(response.status_code, str(message), body, retry)
        states = {
            "queued_campaign",
            "queued_org",
            "queued_system",
            "assigned",
            "running",
            "finished",
            "cancelled",
            "expired",
            "failed",
        }
        if not isinstance(body, dict) or body.get("state") not in states:
            raise ApiError(502, "Invalid scheduling status response", body)
        return cast(SessionScheduling, body)

    def status(self, run_id: int) -> SessionScheduling:
        """Read admission state once. HTTP failures are returned as ApiError."""
        return self._request("GET", run_id)

    def cancel(self, run_id: int) -> SessionScheduling:
        """Cancel once; an active session may require its owner to stop it."""
        return self._request("DELETE", run_id)

    def wait(
        self,
        run_id: int,
        *,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
        cancel_event: Event | None = None,
    ) -> SessionScheduling:
        """Wait until assigned/running/finished; raise for other terminal states.

        Durations are seconds. Only rate-limited GETs are retried, respecting
        Retry-After without exceeding the wait budget. An Event interrupts sleep;
        cancellation during HTTP I/O is observed as soon as that request returns.
        HTTP phase timeouts are bounded by the remaining wait budget.
        """
        _positive(timeout, "timeout")
        _positive(poll_interval, "poll_interval")
        deadline = time.monotonic() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise SchedulingCancelledError("Scheduling wait cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SchedulingTimeoutError(f"Timed out waiting for session {run_id}")
            delay = poll_interval
            try:
                session = self._request("GET", run_id, timeout=remaining)
            except _SchedulingApiError as error:
                if error.status_code != 429:
                    raise
                delay = max(poll_interval, error.retry_after or 0)
            except httpx.TimeoutException as error:
                raise SchedulingTimeoutError(
                    f"Timed out waiting for session {run_id}"
                ) from error
            else:
                if cancel_event is not None and cancel_event.is_set():
                    raise SchedulingCancelledError("Scheduling wait cancelled")
                if time.monotonic() >= deadline:
                    raise SchedulingTimeoutError(
                        f"Timed out waiting for session {run_id}"
                    )
                if session["state"] in {"failed", "expired", "cancelled"}:
                    raise SchedulingError(session)
                if session["state"] in {"assigned", "running", "finished"}:
                    return session
            delay = min(delay, max(0, deadline - time.monotonic()))
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise SchedulingCancelledError("Scheduling wait cancelled")
            else:
                time.sleep(delay)
