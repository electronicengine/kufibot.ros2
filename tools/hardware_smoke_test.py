#!/usr/bin/env python3
"""Interactive hardware smoke tests for Kufibot camera and ALSA audio."""

import argparse
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave


def camera_test(args):
    import cv2
    capture = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f'camera cannot be opened: {args.device}')
    frames = 0
    started = time.monotonic()
    last = None
    while time.monotonic() - started < args.seconds:
        ok, last = capture.read()
        if ok:
            frames += 1
    capture.release()
    if frames == 0 or last is None:
        raise RuntimeError('camera opened but returned no frames')
    output = Path(args.output)
    if not cv2.imwrite(str(output), last):
        raise RuntimeError(f'could not write test frame: {output}')
    print(f'PASS camera: {frames} frames, {frames / args.seconds:.1f} FPS')
    print(f'Last frame: {last.shape[1]}x{last.shape[0]}, saved to {output}')


def microphone_test(args):
    with tempfile.NamedTemporaryFile(suffix='.wav') as recording:
        command = [
            'arecord', '-D', args.device, '-f', 'S16_LE', '-r', '16000',
            '-c', '1', '-d', str(args.seconds), '-t', 'wav', recording.name]
        print(f'Recording for {args.seconds}s; speak now...')
        subprocess.run(command, check=True)
        with wave.open(recording.name, 'rb') as wav_file:
            pcm = wav_file.readframes(wav_file.getnframes())
    samples = [
        int.from_bytes(pcm[i:i + 2], 'little', signed=True)
        for i in range(0, len(pcm) - 1, 2)]
    if not samples:
        raise RuntimeError('microphone recording contains no samples')
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    peak = max(abs(value) for value in samples)
    print(f'Microphone RMS={rms:.0f}, peak={peak} (int16 full scale=32767)')
    if peak < args.min_peak:
        raise RuntimeError(
            f'microphone signal is too quiet; expected peak >= {args.min_peak}')
    print('PASS microphone: non-silent audio captured')


def speaker_test(args):
    sample_rate = 48000
    samples = int(sample_rate * args.seconds)
    with tempfile.NamedTemporaryFile(suffix='.wav') as tone_file:
        with wave.open(tone_file.name, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            amplitude = 5000
            pcm = bytearray()
            for index in range(samples):
                value = int(amplitude * math.sin(
                    2.0 * math.pi * args.frequency * index / sample_rate))
                pcm.extend(value.to_bytes(2, 'little', signed=True))
            wav_file.writeframes(pcm)
        subprocess.run(['aplay', '-D', args.device, tone_file.name], check=True)
    answer = input('Did you hear the tone? [y/N] ').strip().lower()
    if answer not in ('y', 'yes', 'e', 'evet'):
        raise RuntimeError('speaker playback was not confirmed')
    print('PASS speaker: tone playback confirmed')


def mediapipe_test(args):
    # MediaPipe audio Tasks are irrelevant and may block probing PortAudio.
    sys.modules['sounddevice'] = None
    import cv2
    import mediapipe as mp
    capture = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f'camera cannot be opened: {args.device}')
    faces_model = mp.solutions.face_detection.FaceDetection(
        model_selection=0, min_detection_confidence=args.confidence)
    hands_model = mp.solutions.hands.Hands(
        max_num_hands=2, min_detection_confidence=args.confidence,
        min_tracking_confidence=args.confidence)
    max_faces = max_hands = processed = 0
    started = time.monotonic()
    print('Show your face and one hand to the camera...')
    while time.monotonic() - started < args.seconds:
        ok, frame = capture.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        faces = faces_model.process(rgb)
        hands = hands_model.process(rgb)
        max_faces = max(max_faces, len(faces.detections or []))
        max_hands = max(max_hands, len(hands.multi_hand_landmarks or []))
        processed += 1
    capture.release()
    faces_model.close()
    hands_model.close()
    print(f'Processed={processed}, max faces={max_faces}, max hands={max_hands}')
    if not processed or not max_faces or not max_hands:
        raise RuntimeError('face and hand were not both detected during the test')
    print('PASS MediaPipe: face and hand detected')


def build_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='test', required=True)
    camera = subparsers.add_parser('camera')
    camera.add_argument('--device', default='/dev/video0')
    camera.add_argument('--seconds', type=int, default=5)
    camera.add_argument('--output', default='/tmp/kufibot_camera_test.jpg')
    camera.set_defaults(run=camera_test)
    microphone = subparsers.add_parser('microphone')
    microphone.add_argument('--device', default='default')
    microphone.add_argument('--seconds', type=int, default=5)
    microphone.add_argument('--min-peak', type=int, default=300)
    microphone.set_defaults(run=microphone_test)
    speaker = subparsers.add_parser('speaker')
    speaker.add_argument('--device', default='default')
    speaker.add_argument('--seconds', type=int, default=2)
    speaker.add_argument('--frequency', type=int, default=440)
    speaker.set_defaults(run=speaker_test)
    mediapipe = subparsers.add_parser('mediapipe')
    mediapipe.add_argument('--device', default='/dev/video0')
    mediapipe.add_argument('--seconds', type=int, default=12)
    mediapipe.add_argument('--confidence', type=float, default=0.6)
    mediapipe.set_defaults(run=mediapipe_test)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.run(args)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
