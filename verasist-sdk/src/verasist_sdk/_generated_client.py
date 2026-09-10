"""GENERATED — do not edit. Source: filtered OpenAPI from `api.app`.

Regenerate with `./scripts/generate_sdk.sh`.

`VerasistClient` mixes in this class to get HTTP methods for every route
decorated with `sdk_expose(...)` on the backend. Request/response types
come from `_generated_models` (datamodel-codegen output).
"""

from __future__ import annotations

from typing import Any

from verasist_sdk._generated_models import (
    CalendarEntryResponse,
    CampaignRangeResponse,
    ChannelOutboundInitiateRequest,
    CreateCalendarEntryRequest,
    CreateToolRequest,
    CreateWorkflowRequest,
    CredentialResponse,
    DocumentListResponseSchema,
    InitiateCallRequest,
    NodeSpec,
    NodeTypesResponse,
    RecordingListResponseSchema,
    StartLiveSessionRequest,
    StartLiveSessionResponse,
    ToolResponse,
    TurnCredentialsResponse,
    UpdateCalendarEntryRequest,
    UpdateWorkflowRequest,
    WorkflowListResponse,
    WorkflowResponse,
)


class _GeneratedClient:
    # `VerasistClient.__init__` installs `self._request` (see client.py).

    def create_calendar_entry(self, *, body: CreateCalendarEntryRequest) -> CalendarEntryResponse:
        """Manually create a calendar entry."""
        data = self._request("POST", "/calendar/entries", json=body.model_dump(mode="json", exclude_none=True))
        return CalendarEntryResponse.model_validate(data)

    def create_tool(self, *, body: CreateToolRequest) -> ToolResponse:
        """Create a reusable tool for the authenticated organization."""
        data = self._request("POST", "/tools/", json=body.model_dump(mode="json", exclude_none=True))
        return ToolResponse.model_validate(data)

    def create_workflow(self, *, body: CreateWorkflowRequest) -> WorkflowResponse:
        """Create a new workflow from a workflow definition."""
        data = self._request("POST", "/workflow/create/definition", json=body.model_dump(mode="json", exclude_none=True))
        return WorkflowResponse.model_validate(data)

    def delete_calendar_entry(self, entry_uuid: str) -> Any:
        """Delete a calendar entry."""
        return self._request("DELETE", f"/calendar/entries/{entry_uuid}")

    def delete_workflow(self, workflow_id: int) -> Any:
        """Delete a workflow and all its associated definitions, runs, and campaigns."""
        return self._request("DELETE", f"/workflow/{workflow_id}")

    def ensure_builtin_verasist_mcp_tool(self) -> ToolResponse:
        """Ensure the built-in Verasist platform-tools MCP tool exists for the authenticated organization, creating it on first use. Requires admin or supervisor privileges."""
        data = self._request("POST", "/tools/builtin/verasist-mcp")
        return ToolResponse.model_validate(data)

    def get_live_session_turn_credentials(self, session_token: str) -> TurnCredentialsResponse:
        """Fetch time-limited TURN credentials for an active device live session, identified by the session_token returned from start_live_session."""
        data = self._request("GET", f"/public/agent/live-session/{session_token}/turn-credentials")
        return TurnCredentialsResponse.model_validate(data)

    def get_node_type(self, name: str, *, lang: str | None = None) -> NodeSpec:
        """Fetch a single node spec by name."""
        params: dict[str, Any] = {}
        if lang is not None:
            params["lang"] = lang
        data = self._request("GET", f"/node-types/{name}", params=params)
        return NodeSpec.model_validate(data)

    def get_workflow(self, workflow_id: int) -> WorkflowResponse:
        """Get a single workflow by ID (returns draft if one exists, else published)."""
        data = self._request("GET", f"/workflow/fetch/{workflow_id}")
        return WorkflowResponse.model_validate(data)

    def initiate_channel_outbound(self, *, body: ChannelOutboundInitiateRequest) -> Any:
        """Initiate an outbound message or call via a configured channel."""
        return self._request("POST", "/channels/outbound/initiate", json=body.model_dump(mode="json", exclude_none=True))

    def list_calendar_entries(self, *, start_date: str | None = None, end_date: str | None = None) -> list[CalendarEntryResponse]:
        """List calendar entries for the authenticated organization in a date range."""
        params: dict[str, Any] = {}
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date
        data = self._request("GET", "/calendar/entries", params=params)
        return [CalendarEntryResponse.model_validate(x) for x in data]

    def list_campaign_date_ranges(self) -> list[CampaignRangeResponse]:
        """List active campaign date ranges for the calendar overlay."""
        data = self._request("GET", "/calendar/campaign-ranges")
        return [CampaignRangeResponse.model_validate(x) for x in data]

    def list_credentials(self) -> list[CredentialResponse]:
        """List webhook credentials available to the authenticated organization."""
        data = self._request("GET", "/credentials/")
        return [CredentialResponse.model_validate(x) for x in data]

    def list_documents(self, *, status: str | None = None, limit: int | None = None, offset: int | None = None) -> DocumentListResponseSchema:
        """List knowledge base documents available to the authenticated organization."""
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        data = self._request("GET", "/knowledge-base/documents", params=params)
        return DocumentListResponseSchema.model_validate(data)

    def list_node_types(self, *, lang: str | None = None) -> NodeTypesResponse:
        """List every registered node type with its spec. Pinned to spec_version."""
        params: dict[str, Any] = {}
        if lang is not None:
            params["lang"] = lang
        data = self._request("GET", "/node-types", params=params)
        return NodeTypesResponse.model_validate(data)

    def list_recordings(self, *, workflow_id: int | None = None, tts_provider: str | None = None, tts_model: str | None = None, tts_voice_id: str | None = None) -> RecordingListResponseSchema:
        """List workflow recordings available to the authenticated organization."""
        params: dict[str, Any] = {}
        if workflow_id is not None:
            params["workflow_id"] = workflow_id
        if tts_provider is not None:
            params["tts_provider"] = tts_provider
        if tts_model is not None:
            params["tts_model"] = tts_model
        if tts_voice_id is not None:
            params["tts_voice_id"] = tts_voice_id
        data = self._request("GET", "/workflow-recordings/", params=params)
        return RecordingListResponseSchema.model_validate(data)

    def list_tools(self, *, status: str | None = None, category: str | None = None, workflow_id: int | None = None) -> list[ToolResponse]:
        """List tools available to the authenticated organization."""
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        if category is not None:
            params["category"] = category
        if workflow_id is not None:
            params["workflow_id"] = workflow_id
        data = self._request("GET", "/tools/", params=params)
        return [ToolResponse.model_validate(x) for x in data]

    def list_workflows(self, *, status: str | None = None) -> list[WorkflowListResponse]:
        """List all workflows in the authenticated organization."""
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        data = self._request("GET", "/workflow/fetch", params=params)
        return [WorkflowListResponse.model_validate(x) for x in data]

    def start_live_session(self, uuid: str, *, body: StartLiveSessionRequest) -> StartLiveSessionResponse:
        """Start a live voice session (WebRTC) against the published agent identified by trigger UUID. Returns a session_token used to open the device signaling WebSocket."""
        data = self._request("POST", f"/public/agent/live-session/{uuid}", json=body.model_dump(mode="json", exclude_none=True))
        return StartLiveSessionResponse.model_validate(data)

    def start_live_session_by_workflow(self, workflow_uuid: str, *, body: StartLiveSessionRequest) -> StartLiveSessionResponse:
        """Start a live voice session (WebRTC) against the published workflow identified by UUID. Returns a session_token used to open the device signaling WebSocket."""
        data = self._request("POST", f"/public/agent/live-session/workflow/{workflow_uuid}", json=body.model_dump(mode="json", exclude_none=True))
        return StartLiveSessionResponse.model_validate(data)

    def test_phone_call(self, *, body: InitiateCallRequest) -> Any:
        """Place a test call from a workflow to a phone number."""
        return self._request("POST", "/telephony/initiate-call", json=body.model_dump(mode="json", exclude_none=True))

    def update_calendar_entry(self, entry_uuid: str, *, body: UpdateCalendarEntryRequest) -> CalendarEntryResponse:
        """Update a calendar entry."""
        data = self._request("PUT", f"/calendar/entries/{entry_uuid}", json=body.model_dump(mode="json", exclude_none=True))
        return CalendarEntryResponse.model_validate(data)

    def update_workflow(self, workflow_id: int, *, body: UpdateWorkflowRequest) -> WorkflowResponse:
        """Update a workflow's name and/or definition. Saves as a new draft."""
        data = self._request("PUT", f"/workflow/{workflow_id}", json=body.model_dump(mode="json", exclude_none=True))
        return WorkflowResponse.model_validate(data)
