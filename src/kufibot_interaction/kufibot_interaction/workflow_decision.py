"""Explicit adapter registry for future non-language decision models.

Factories accept a catalog entry and return a callable (node, context, edges).
Adapters must return {'edge_id': <allowed edge id>} or raise an error. No model
is implicitly downloaded or treated as compatible based on its name.
"""
import queue
import threading
from importlib.metadata import entry_points

_factories = {}
_discovered = False


def register_provider(name, factory):
    if not isinstance(name, str) or not name or not callable(factory):
        raise ValueError('Geçersiz karar sağlayıcısı')
    _factories[name] = factory


def providers():
    global _discovered
    if not _discovered:
        _discovered = True
        for entry in entry_points(group='kufibot.workflow_decision'):
            register_provider(entry.name, entry.load())
    return sorted(_factories)


class DecisionProvider:
    def __init__(self, entry):
        if entry.get('backend') not in providers():
            raise ValueError('Uyumlu karar sağlayıcısı kurulu değil')
        self.adapter = _factories[entry['backend']](entry)
        self.failed = False

    def __call__(self, node, context, edges):
        if self.failed:
            raise ValueError('Karar sağlayıcısı önceki çağrıda zaman aşımına uğradı')
        inbox = queue.Queue(maxsize=1)
        def run():
            try:
                inbox.put((self.adapter(node, context, edges), None))
            except Exception as error:
                inbox.put((None, error))
        threading.Thread(target=run, name='workflow-decision', daemon=True).start()
        try:
            result, error = inbox.get(timeout=10)
        except queue.Empty:
            self.failed = True
            raise ValueError('Karar modeli zaman aşımı') from None
        if error:
            raise error
        if not isinstance(result, dict) or result.get('edge_id') not in {e['id'] for e in edges}:
            raise ValueError('Karar modeli izinli bir çıkış seçmeli')
        return result

    def close(self):
        close = getattr(self.adapter, 'close', None)
        if close and not self.failed:
            close()
