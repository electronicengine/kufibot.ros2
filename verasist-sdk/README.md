# verasist-sdk

Typed builder for Verasist voice-AI workflows. Fetches the node-spec catalog from
the Verasist backend at session start, validates every call against it at the
call site, and produces `ReactFlowDTO`-compatible JSON.

## Install

```bash
pip install verasist-sdk
```

For local development against a checked-out monorepo:

```bash
pip install -e sdk/python/
```

## Usage

```python
from verasist_sdk import VerasistClient, Workflow

with VerasistClient(base_url="http://localhost:8000", api_key="...") as client:
    wf = Workflow(client=client, name="loan_qualification")

    start = wf.add(
        type="startCall",
        name="greeting",
        prompt="You are Sarah from Acme Loans. Greet the caller warmly.",
        greeting_type="text",
        greeting="Hi {{first_name}}, this is Sarah.",
    )
    qualify = wf.add(
        type="agentNode",
        name="qualify",
        prompt="Ask about loan amount and timeline.",
    )
    done = wf.add(type="endCall", name="done", prompt="Thank the caller.")

    wf.edge(start, qualify, label="interested", condition="Caller expressed interest.")
    wf.edge(qualify, done, label="done", condition="Qualification complete.")

    client.save_workflow(workflow_id=123, workflow=wf)
```

## What gets validated at the call site

The SDK fetches the spec for each node type via `get_node_type` and raises
`ValidationError` immediately when:

- an unknown field is passed (catches typos)
- a required field is missing or empty
- a scalar type is wrong (e.g., string for a boolean)
- an `options` value isn't in the allowed list

When a spec carries an `llm_hint`, the hint is appended to the error message so
an LLM agent can self-correct on retry:

```
tool_uuids: expected tool_refs, got str
  Hint: List of tool UUIDs from `list_tools`.
```

Server-side Pydantic validators run on save and surface anything the SDK lets
through (compound invariants, cross-field rules).

## Live voice session (device tools + WebRTC)

`LiveSession` lets a device (mic/speaker) talk to a workflow assistant over
WebRTC, while exposing device-side tools the assistant can call mid-call —
in addition to any server-side built-in/agent tools already configured on
the workflow. Requires the `voice` extra:

```bash
pip install "verasist-sdk[voice]"
```

```python
import asyncio
from verasist_sdk import VerasistClient, LiveSession

async def main():
    client = VerasistClient(base_url="http://localhost:8000", api_key="...")
    session = LiveSession(client)

    @session.tool(description="Get the device's current battery level")
    def get_battery_level():
        return {"battery_percent": 87}

    await session.connect(trigger_uuid="my-trigger-uuid")

    session.pc.addTrack(my_microphone_track)  # your own aiortc MediaStreamTrack

    @session.on_track
    def on_bot_audio(track):
        ...  # feed `track` frames to your speaker output in real time

    @session.on_transcript
    def on_transcript(event):
        # {"role": "user" | "assistant", "text": "...", "final": bool}
        print(event["role"], event["text"])

    await session.wait_closed()

asyncio.run(main())
```

See `examples/python/live_voice_session.py` for a runnable example wiring
up real mic/speaker audio.

## Environment

```bash
VERASIST_API_URL=http://localhost:8000   # default
VERASIST_API_KEY=sk-...                  # sent as X-API-Key
```

## License

BSD 2-Clause — see `LICENSE`.


### Turn-bound robot camera (0.1.8)

`LiveSession.on_voice_event` receives `{type, payload}` dictionaries, including
`rtf-bot-interrupted`, speaking/mute changes and `rtf-user-turn-started`.
Use `await session.configure_camera_turns(True)` to opt in after connecting.
For each server-issued `turn_id`, send at most one image with
`await session.send_image(image_bytes=jpeg, turn_id=turn_id, trigger_response=False)`,
then `await session.complete_camera_turn(turn_id)`. Complete the turn even when
the camera is unavailable. Disable with `configure_camera_turns(False)`.

The server waits at most two seconds after the turn boundary and rejects stale
IDs. STT/LLM/TTS pipelines and opted-in device Gemini Live sessions support this
contract; unsupported realtime backends reject configuration explicitly.
For robot sessions, `connect(call_context_vars={"device_barge_in": True})` enables
full-duplex interruption during greetings and tool work without changing other
clients' mute policy. AEC belongs in the device audio path; the Python SDK does
not automatically echo-cancel raw audio tracks.
## Publish SDK tools to a workflow

Publish the same schemas your application registers at runtime:

```python
client.publish_toolset("WORKFLOW_UUID", [
    {"name": "sdk_cart", "description": "Read the shopping cart",
     "parameters": {"type": "object", "properties": {}}}
])
```

For tools registered with `@session.tool(...)`, use
`await session.publish_toolset("WORKFLOW_UUID")` to publish all registered
schemas automatically. This requires an API key belonging to the workflow's
organization. It does not connect the session or upload executable handlers.

In the workflow editor, open the **SDK tools** tab and click **Refresh**.
Click a tool card to add it to the canvas, then connect it to a start/agent
node's tool port. You can also select tools in the node's resource settings. SDK tools are executed
by the existing session transport; publishing alone does not make them callable.
Only SDK tools explicitly connected to the active node are exposed to the LLM.
Nodes with missing, null or empty selections cannot use SDK tools.

Publishing replaces the entire catalog for that workflow, without changing
its draft or published versions. Publish `[]` to clear the catalog. Removed
names remain visible as missing in node settings until you remove the selection.
The catalog is shared across workflow versions; node selections are versioned.

## Standalone audio

Use `client.audio` with a published workflow UUID to transcribe or synthesize
speech without creating a conversation session. STT audio seconds and TTS input
characters are billed using existing tariffs. Only the selected component runs.
See [the audio guide](https://docs.verasist.ai/developer/sdks/audio) for file and
live-stream examples, formats, cancellation, and request IDs.
