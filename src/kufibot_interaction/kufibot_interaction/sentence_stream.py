"""Incremental sentence boundaries without consuming transcript text."""
import re

# Require whitespace after punctuation: a '.' token alone may become a decimal.
_BOUNDARY = re.compile(r'[.!?…]+[\"”’\')\]]*(?=\s)')
_ABBREVIATIONS = {'dr', 'prof', 'doç', 'sn', 'av', 'yrd', 'vb', 'vs', 'örn', 'bkz',
                  'mr', 'mrs', 'ms', 'jr', 'sr', 'st', 'e.g', 'i.e', 'etc', 'u.s', 'u.k'}


class SentenceBuffer:
    def __init__(self):
        self.pending = ''
        self.scan = 0

    def push(self, text):
        self.pending += text
        sentences = []
        while match := _BOUNDARY.search(self.pending, self.scan):
            end = match.end()
            if self.pending[match.start():match.start()+1] == '.' and not self.pending[match.start():end].startswith('..'):
                prefix = self.pending[:match.start()]
                word = re.search(r'([\w.]+)$', prefix)
                word = word.group(1) if word else ''
                if (word.casefold() in _ABBREVIATIONS or
                        (len(word) == 1 and word.isupper()) or
                        re.fullmatch(r'\s*\d+', prefix)):
                    self.scan = end
                    continue
            sentence = self.pending[:end].strip()
            if sentence:
                sentences.append(sentence)
            self.pending = self.pending[end:].lstrip()
            self.scan = 0
        # Revisit the unfinished last token after the next delta arrives.
        return sentences

    def finish(self):
        tail = self.pending.strip()
        self.pending, self.scan = '', 0
        return [tail] if tail else []
