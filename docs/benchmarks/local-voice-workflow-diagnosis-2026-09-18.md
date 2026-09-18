# Local voice workflow diagnosis — 2026-09-18

## Observed robot session (before these changes)

Source: `/home/kufi/.ros/log/python3_4509_1789754913174.log`, session
`80d6a3f199744315b1d1a57f42dd2bb7`, published workflow revision 22.

- Opening assistant repeated the synthetic user instruction: “Bu gün görüşme
  başladı. Etkin node talimatına göre konuşmayı başlat.”
- Opening routing inference took 3.937 s; workflow start to playback start
  took 5.383 s (model loading excluded).
- A later recognized utterance was “geliyor mu”. Speech end to playback start
  was 3.267 s, including 0.620 s endpoint silence and 0.348 s final STT.
- Several other VAD segments ended with empty recognition. No audio recordings
  are available, so these cannot be classified as missed speech vs false VAD.
- Physical camera microphone was unmuted at 63% (-12.25 dB), feeding the configured
  PipeWire AEC source. This alone does not establish inadequate microphone gain.

## Changes and semantics

- Empty opening turns no longer insert an artificial user instruction.
- A prompted start node generates its greeting, advances its sole validated
  edge, and waits for the next user turn at the destination. It does not use an
  LLM router. Agents retain model-selected transitions; unprompted start nodes
  still advance immediately and execute the destination in the same turn.
- Completed generated sentences are checked before being queued to Piper or
  published as assistant transcripts. Known internal control phrases and exact
  copies of multiword imperative system clauses raise an explanatory error.
  This is a conservative leakage check, not a guarantee of instruction following
  or detection of every paraphrased leak. It never substitutes canned speech.
- STT final metrics now include audio duration, RMS dBFS, peak, clipping count,
  and whether recognition was empty. Empty results emit a retry status and
  diagnostic rather than silently disappearing. Audio is not recorded.
- Sentence streaming remains enabled; no full-answer buffering or extra LLM
  retry was added. Assistant partial transcripts now contain checked sentences.

## Verification and limits

84 targeted interaction/remote regression tests passed. Interaction ROS package
rebuilt successfully. No robot service was running or restarted.

Real installed ufakzeka q8_0, context 2048, CPU threads 3 / batch 4, Piper
`fahrettin.onnx`, current published workflow, seed 17, temperature 0, ALSA `null`:

- Opening: “Merhaba! Sana nasıl yardımcı olabilirim?”
- First LLM token: 0.217 s; first PCM/playback-start event: 0.570 s.
- LLM completion: 0.450 s; total silent-output run: 1.020 s.
- No routing call; current node advanced to the configured agent.

These are a single unloaded, silent-output measurement, not an end-to-end
microphone/Bluetooth latency claim or a controlled comparison to the live log.
The natural opening still did not ask “nasılsın” as requested. In the subsequent
text-only conversation, ufakzeka also ignored the next node's instruction to ask
what the user did today. Installed Dolphin 1B and 3B models likewise failed simple
Turkish instruction-following probes; switching to them is not a verified fix.

Model selection, stored workflow, microphone gain and AEC settings were not
changed. A real user audio sample is still needed to diagnose recognition quality.
The local path remains half-duplex: speech over robot playback is not captured.
Alternative model evaluation needs an agreed download; no new model was installed.
