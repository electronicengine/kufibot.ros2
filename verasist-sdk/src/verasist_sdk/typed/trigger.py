"""GENERATED — do not edit by hand.

Regenerate with `python -m verasist_sdk.codegen` against the target
Verasist backend. Source of truth: the backend's model-backed node-spec
catalog served from `/api/v1/node-types`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from verasist_sdk.typed._base import TypedNode


@dataclass(kw_only=True)
class Trigger(TypedNode):
    """
    Public HTTP endpoints that launch the workflow.  LLM hint: Exposes two
    public HTTP POST endpoints derived from the auto-generated
    `trigger_path`:   • Production:
    `<backend>/api/v1/public/agent/<trigger_path>` — runs the published
    agent. Use this from production systems.   • Test:
    `<backend>/api/v1/public/agent/test/<trigger_path>` — runs the latest
    draft, useful for verifying changes before publishing. Falls back to the
    published agent when no draft exists. Both require an API key in the
    `X-API-Key` header. The node itself is configured with a `channel`
    (`phone`/`whatsapp`/`telegram`/`instagram`/`text`) that determines how
    the workflow is launched; whatsapp/telegram/instagram also require a
    `channel_configuration_id` picking which saved channel credential to
    use, and phone may optionally pin a `telephony_configuration_id` (falls
    back to the org's default outbound telephony configuration). `text`
    means no external channel at all — the call just returns the workflow's
    first generated message in the response. Request body fields:   •
    `channel` (string, optional) — overrides the node's configured channel
    for this call only. Required in the request when neither the node nor
    the request specify one (e.g. workflow-uuid-based trigger routes, which
    have no node to default from).   • `phone_number` (string, required when
    channel is `phone`) — destination to dial.   • `target` (string,
    required when channel is `whatsapp`/`telegram`/`instagram`) — the
    channel-specific destination (E.164 phone/JID for whatsapp, chat id for
    telegram/instagram).   • `channel_configuration_id` (int, optional) —
    overrides the node's channel configuration for this call only.   •
    `telephony_configuration_id` (int, optional) — overrides the node's
    telephony configuration for this call only (phone channel).   •
    `initial_context` (object, optional) — merged into the run's initial
    context. Response includes a `message` field with the workflow's first
    generated reply for `whatsapp`/`telegram`/`instagram`/`text` channels
    (`null` for `phone`).
    """

    type: ClassVar[str] = 'trigger'

    name: str = 'API Trigger'
    """
    Short identifier shown in the canvas. No runtime effect.
    """

    enabled: bool = True
    """
    When false, the trigger URL returns 404.
    """

    trigger_path: Optional[str] = None
    """
    Path segment that uniquely identifies this trigger. Used in both URLs:
    • Production: `/api/v1/public/agent/<trigger_path>` — executes the
    published agent.   • Test: `/api/v1/public/agent/test/<trigger_path>` —
    executes the latest draft. Can be customized to a descriptive value up
    to 36 characters using letters, numbers, hyphens, or underscores.
    """

