"""Offline Hailo-8L Whisper InferModel backend (no GenAI/Hailo-10 dependency).

Tensor layout follows Hailo's MIT-licensed speech_recognition example; see
third_party/HAILO_NOTICE.txt. CPU preprocessing uses NumPy/SciPy, without torch.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np

REQUIRED = ('encoder.hef', 'decoder.hef', 'token_embedding.npy',
            'position_embedding.npy', 'tokenizer.json', 'mel_filters.npz')


def package_manifest(folder, verify=False):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text())
    if (manifest.get('backend') != 'hailo_whisper' or manifest.get('arch') != 'hailo8l'
            or manifest.get('variant') not in ('tiny', 'base')
            or not {'tr', 'en'}.issubset(manifest.get('languages', []))):
        raise ValueError('Invalid Hailo-8L Whisper package manifest')
    for name in REQUIRED:
        path = folder / name
        checksum = manifest.get('files', {}).get(name, '')
        if not path.is_file() or not re.fullmatch('[a-f0-9]{64}', checksum):
            raise ValueError(f'Incomplete Whisper package: {name}')
        if verify:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != checksum:
                raise ValueError(f'Whisper checksum mismatch: {name}')
    return manifest


def decoder_prefix(tokenizer, language):
    names = ['<|startoftranscript|>', f'<|{language}|>', '<|transcribe|>', '<|notimestamps|>']
    ids = [tokenizer.token_to_id(name) for name in names]
    if None in ids:
        raise ValueError('Whisper tokenizer is missing selected language/task tokens')
    return ids


def merge_overlap(previous, following, max_words=3):
    """Only remove a matching suffix/prefix at an actual overlapping window."""
    old, new = previous.split(), following.split()
    normalize = lambda w: re.sub(r'[^\w]', '', w.casefold())
    for length in range(min(max_words, len(old), len(new)), 0, -1):
        if ([normalize(w) for w in old[-length:]] == [normalize(w) for w in new[:length]]):
            return ' '.join(old + new[length:])
    return ' '.join(old + new)


def mel_spectrogram(audio, samples, filters):
    from scipy.fft import rfft
    audio = np.asarray(audio, dtype=np.float32)
    audio = np.pad(audio[:samples], (0, max(0, samples - len(audio))))
    padded = np.pad(audio, (200, 200), mode='reflect')
    frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160][:-1]
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(400) / 400)).astype(np.float32)
    power = np.abs(rfft(frames * window, axis=-1, workers=1)) ** 2
    # einsum avoids dispatching a small matrix product to a large BLAS pool.
    mel = np.einsum('mf,tf->tm', filters, power, optimize=False)
    log = np.log10(np.maximum(mel, 1e-10))
    log = (np.maximum(log, log.max() - 8) + 4) / 4
    return np.ascontiguousarray(log[None, None, :, :], dtype=np.float32)


class DecoderLimitError(RuntimeError):
    pass


class HailoWhisper:
    def __init__(self, folder, language, timeout=30):
        from hailo_platform import HEF, VDevice, HailoSchedulingAlgorithm, FormatType
        from tokenizers import Tokenizer
        self.folder = Path(folder)
        manifest = package_manifest(folder, verify=True)
        if language not in manifest['languages']:
            raise ValueError('Selected language is not supported by Whisper package')
        self.timeout = timeout
        self.stack = ExitStack()
        try:
            self.tokenizer = Tokenizer.from_file(str(self.folder / 'tokenizer.json'))
            self.prefix = decoder_prefix(self.tokenizer, language)
            self.eos = self.tokenizer.token_to_id('<|endoftext|>')
            if None in self.prefix or self.eos is None:
                raise ValueError('Whisper tokenizer is missing language/task tokens')
            self.embedding = np.load(self.folder / 'token_embedding.npy', mmap_mode='r', allow_pickle=False)
            self.position = np.load(self.folder / 'position_embedding.npy', allow_pickle=False)
            with np.load(self.folder / 'mel_filters.npz', allow_pickle=False) as filters:
                self.filters = filters['mel_80'].copy()
            encoder_hef = HEF(str(self.folder / 'encoder.hef'))
            decoder_hef = HEF(str(self.folder / 'decoder.hef'))
            # HailoRT 4.20 has no HEF.get_arch(); pinned package hashes and
            # configure() enforce compatibility with the physical device.
            self.samples = int(encoder_hef.get_input_vstream_infos()[0].shape[1]) * 160
            self.sequence = int(decoder_hef.get_output_vstream_infos()[0].shape[1])
            self.outputs = [name for name in decoder_hef.get_sorted_output_names() if 'conv' in name]
            if not self.outputs or self.samples < 16000 or self.sequence < 8:
                raise ValueError('Unexpected Whisper HEF tensor layout')
            decoder_name = decoder_hef.get_network_group_names()[0]
            self.decoder_inputs = [decoder_name + '/input_layer1', decoder_name + '/input_layer2']
            params = VDevice.create_params()
            params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
            self.device = self.stack.enter_context(VDevice(params))
            self.encoder = self.device.create_infer_model(str(self.folder / 'encoder.hef'))
            self.decoder = self.device.create_infer_model(str(self.folder / 'decoder.hef'))
            for model in (self.encoder, self.decoder):
                for name in model.input_names:
                    model.input(name).set_format_type(FormatType.FLOAT32)
                for name in model.output_names:
                    model.output(name).set_format_type(FormatType.FLOAT32)
            self.enc = self.stack.enter_context(self.encoder.configure())
            self.dec = self.stack.enter_context(self.decoder.configure())
            self.enc_bind = self.enc.create_bindings()
            self.dec_bind = self.dec.create_bindings()
            self.encoded = np.empty(self.encoder.output().shape, dtype=np.float32)
            self.enc_bind.output().set_buffer(self.encoded)
            for name in self.decoder.output_names:
                self.dec_bind.output(name).set_buffer(np.empty(self.decoder.output(name).shape, dtype=np.float32))
            self.reset()
        except BaseException:
            self.stack.close()
            raise

    def reset(self):
        self.audio = bytearray()
        self.timings = {}

    def feed(self, pcm):
        # Absolute hard bound even when used outside the live VAD worker.
        if len(self.audio) + len(pcm) > 16000 * 2 * 120:
            raise ValueError('Whisper utterance exceeds 120 seconds')
        self.audio.extend(pcm)

    def _run(self, model, binding, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Hailo Whisper window timed out')
        model.run([binding], max(1, int(min(remaining, 5.0) * 1000)))

    def _transcribe_window(self, audio, deadline=None):
        started = time.monotonic()
        deadline = started + self.timeout if deadline is None else deadline
        mel = mel_spectrogram(audio, self.samples, self.filters)
        self.timings['preprocess_sec'] = self.timings.get('preprocess_sec', 0) + time.monotonic() - started
        self.enc_bind.input().set_buffer(mel)
        enc_at = time.monotonic()
        self._run(self.enc, self.enc_bind, deadline)
        self.timings['encoder_sec'] = self.timings.get('encoder_sec', 0) + time.monotonic() - enc_at
        ids = np.zeros((1, self.sequence), dtype=np.int64)
        ids[0, :len(self.prefix)] = self.prefix
        tokens = []
        self.dec_bind.input(self.decoder_inputs[0]).set_buffer(self.encoded)
        dec_at = time.monotonic()
        for index in range(len(self.prefix) - 1, self.sequence - 1):
            embedded = self.embedding[ids] + self.position
            embedded = np.ascontiguousarray(embedded[:, None, :, :].transpose(0, 2, 1, 3), dtype=np.float32)
            self.dec_bind.input(self.decoder_inputs[1]).set_buffer(embedded)
            self._run(self.dec, self.dec_bind, deadline)
            logits = np.concatenate([self.dec_bind.output(n).get_buffer()[:, index] for n in self.outputs], axis=-1).reshape(-1)
            # Standard sign-aware repetition penalty, excluding punctuation.
            for token in set(tokens[-8:]) - {11, 13}:
                logits[token] = logits[token] / 1.5 if logits[token] > 0 else logits[token] * 1.5
            token = int(np.argmax(logits))
            if token == self.eos:
                break
            tokens.append(token)
            ids[0, index + 1] = token
        else:
            raise DecoderLimitError('Whisper decoder token limit reached; try Vosk for this utterance')
        self.timings['decode_sec'] = self.timings.get('decode_sec', 0) + time.monotonic() - dec_at
        return self.tokenizer.decode(tokens, skip_special_tokens=True).strip()

    def _bounded_window(self, audio, depth=0, deadline=None):
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        try:
            return self._transcribe_window(audio, deadline)
        except DecoderLimitError:
            if depth >= 2 or len(audio) <= 16000:
                raise
            # Retrying shorter pieces is bounded and preserves the entire input.
            middle = len(audio) // 2
            left = self._bounded_window(audio[:middle + 2000], depth + 1, deadline)
            right = self._bounded_window(audio[middle - 2000:], depth + 1, deadline)
            return merge_overlap(left, right)

    def finish(self, trailing_silence_ms=0):
        audio = np.frombuffer(self.audio, dtype='<i2').astype(np.float32) / 32768
        trim = max(0, int((trailing_silence_ms - 160) * 16))
        if trim:
            audio = audio[:max(0, len(audio) - trim)]
        if not len(audio):
            return ''
        # Preserve recording gain; VAD already rejects silence before this call.
        answer = ''
        # HEFs allow 5/10 seconds, but their decoder has only 24/32 token slots.
        # Shorter overlapping speech windows avoid silent truncation of long turns.
        window = min(self.samples, 48000)  # up to 3 seconds, padded to HEF shape
        overlap = 4000  # 250 ms, at most 3 boundary words removed
        for start in range(0, len(audio), window - overlap):
            part = self._bounded_window(audio[start:start + window])
            answer = merge_overlap(answer, part) if start else part
            if start + window >= len(audio):
                break
        return answer

    def close(self):
        self.stack.close()
