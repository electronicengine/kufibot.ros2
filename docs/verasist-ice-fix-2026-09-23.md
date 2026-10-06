# Verasist ICE connection failure — 2026-09-23

The wake phrase matched successfully. Session signaling completed, but media failed with `ice_timeout`.

Two server configuration problems were verified:

- `/root/verasist/.env` advertised `app.verasist.ai` as TURN_HOST. Its proxied DNS records did not reach coturn. Changed to `turn.verasist.ai` (DNS only).
- `scripts/lib/setup_common.sh`, function `verasist_render_remote_turn_conf`, passed TURN_SECRET through `awk -v` and `gsub`. Backslashes in the operator's secret were interpreted during rendering, producing a different coturn secret and TURN allocation failures (401). The renderer now reads the secret from ENVIRON and inserts it literally with substr concatenation. The secret itself was not rotated.

Server files were backed up beside the originals using `.before-turn-fix-<timestamp>` names. Ran the existing verasist-init renderer, recreated api/coturn to apply settings, then restarted coturn after the renderer correction. No server SDK replacement was needed: its connection implementation matched the robot SDK.

Robot SDK now exposes `wait_connected`; the voice node awaits actual transport connectivity before marking session startup complete. Signaling completion alone no longer produces a premature connected state.

Validation: 42 SDK/control tests passed; interaction package rebuilt. API and coturn secrets compared equal without disclosure. Renderer preserved synthetic backslash/ampersand inputs. A robot-to-server session using a synthetic silent audio track gathered relay candidates on both sides, reached connected, and received a 960-sample 48 kHz remote audio frame. No robot motion or physical microphone/speaker was used in this probe.
