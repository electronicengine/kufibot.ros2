"""Speak exact operator text on the robot, independently of an AI session."""
import json
import sys
import time


def main():
    from kufibot_interaction.local_voice_worker import load_voice, speak_sentences, emit
    from kufibot_interaction.sentence_stream import SentenceBuffer

    class Reporter:
        def set_phase(self, _phase):
            pass

        def mark(self, _event):
            pass

    voice = None
    try:
        config = json.loads(sys.stdin.readline())
        started = time.monotonic()
        voice = load_voice(config)
        emit('ready', load_ms=round((time.monotonic() - started) * 1000))
        for line in sys.stdin:
            request = json.loads(line)
            sentences = SentenceBuffer()
            parts = sentences.push(request['text']) + sentences.finish()
            speak_sentences({**config, 'speaker': request['speaker']}, voice, parts, Reporter())
            emit('done')
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)
    finally:
        close = getattr(voice, 'close', None)
        if close:
            close()


if __name__ == '__main__':
    main()
