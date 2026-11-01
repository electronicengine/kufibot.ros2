"""Speak exact operator text on the robot, independently of an AI session."""
import json
import sys


def main():
    from kufibot_interaction.local_voice_worker import load_voice, speak, emit

    class Reporter:
        def set_phase(self, _phase):
            pass

        def mark(self, _event):
            pass

    try:
        config = json.loads(sys.stdin.readline())
        voice = load_voice(config)
        speak(config, voice, config['text'], Reporter())
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)


if __name__ == '__main__':
    main()
