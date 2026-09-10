"""Serialized local embedding inference, with a bounded latest-turn mailbox."""

from collections import deque
import threading
import time

import numpy as np


class EmbeddingSelector:
    def __init__(self, library, model_path, n_threads=2, n_ctx=512,
                 n_gpu_layers=0):
        from llama_cpp import Llama, LLAMA_POOLING_TYPE_MEAN
        self.model = Llama(
            model_path=model_path, embedding=True,
            pooling_type=LLAMA_POOLING_TYPE_MEAN,
            n_threads=n_threads, n_threads_batch=n_threads,
            n_ctx=n_ctx, n_batch=n_ctx, n_ubatch=n_ctx,
            n_gpu_layers=n_gpu_layers, verbose=False)
        self.n_ctx = n_ctx
        self.library = library
        self.signature = self._catalogue_signature()
        self.names = list(library.motions)
        try:
            self.catalogue = np.stack([
                self.vector(library.descriptions.get(name, '').strip()
                            or library.motions[name]['description'].strip()
                            or name)
                for name in self.names])
        except Exception:
            self.close()
            raise

    def vector(self, text):
        # Keep special tokens added by the tokenizer (including SEP) intact.
        tokens = self.model.tokenize(text.encode('utf-8'), add_bos=True,
                                     special=False)
        if len(tokens) > self.n_ctx:
            raw = self.model.tokenize(text.encode('utf-8'), add_bos=False,
                                      special=False)
            raw = raw[:max(1, self.n_ctx - 2)]
            text = self.model.detokenize(raw).decode('utf-8', errors='ignore')
            while len(self.model.tokenize(text.encode('utf-8'), add_bos=True,
                                          special=False)) > self.n_ctx:
                raw = raw[:-1]
                text = self.model.detokenize(raw).decode('utf-8', errors='ignore')
        vector = np.asarray(self.model.embed(text), dtype=np.float32)
        norm = np.linalg.norm(vector)
        if (vector.ndim != 1 or not np.isfinite(vector).all()
                or not np.isfinite(norm) or not norm > 0):
            raise ValueError('Invalid sequence embedding')
        return vector / norm

    def _catalogue_signature(self):
        motions = self.library.motions.copy()
        descriptions = self.library.descriptions.copy()
        return tuple((name, descriptions.get(name, '').strip()
                      or motion.get('description', '').strip() or name)
                     for name, motion in motions.items())

    def select(self, text):
        # This runs exclusively on the embedding worker, including rebuilds.
        if hasattr(self, 'library'):
            signature = self._catalogue_signature()
            if signature != self.signature:
                names = [name for name, _ in signature]
                catalogue = np.stack([self.vector(description) for _, description in signature])
                self.names, self.catalogue, self.signature = names, catalogue, signature
        scores = self.catalogue @ self.vector(text)
        index = int(np.argmax(scores))
        return self.names[index], float(scores[index])

    def close(self):
        self.model.close()


class ExpressionWorker:
    """Own the model on one thread; never wait for inference in submit/poll."""

    def __init__(self, library, factory, warning):
        self.library = library
        self.factory = factory
        self.warning = warning
        self.condition = threading.Condition()
        self.ready = False
        self.closed = False
        self.current = None
        self.pending = None
        self.results = deque(maxlen=1)
        self.thread = threading.Thread(target=self._run,
                                       name='expression-embedding', daemon=True)
        self.thread.start()

    def invalidate(self, generation):
        with self.condition:
            self.current = generation
            self.pending = None
            self.results.clear()

    def submit(self, generation, text):
        with self.condition:
            if self.closed or generation != self.current:
                return
            if not self.ready:
                self.results.append((generation, self.library.classify(text),
                                     None, 0.0))
            else:
                self.pending = (generation, text)
                self.condition.notify()

    def poll(self):
        with self.condition:
            return self.results.popleft() if self.results else None

    def _run(self):
        selector = None
        try:
            try:
                selector = self.factory()
            except Exception as error:
                self.warning(f'Expression embedding unavailable; using local fallback: {error}')
                return
            with self.condition:
                self.ready = not self.closed
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.pending is not None)
                    if self.closed:
                        return
                    generation, text = self.pending
                    self.pending = None
                started = time.monotonic()
                try:
                    name, score = selector.select(text)
                except Exception as error:
                    self.warning(f'Expression embedding failed; using local fallback: {error}')
                    name, score = self.library.classify(text), None
                elapsed = time.monotonic() - started
                with self.condition:
                    if not self.closed and generation == self.current:
                        self.results.append((generation, name, score, elapsed))
        finally:
            if selector is not None:
                selector.close()

    def close(self):
        with self.condition:
            self.closed = True
            self.ready = False
            self.pending = None
            self.results.clear()
            self.condition.notify()
        # Native inference cannot be cancelled. Its owner closes the model
        # after inference returns, even if this bounded join expires.
        self.thread.join(timeout=2.0)
