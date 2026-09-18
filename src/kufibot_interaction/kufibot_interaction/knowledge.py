"""Local document extraction, revisioned vector indexes and source-aware search."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import numpy as np

from .workflows import identifier, atomic_json

MAX_BYTES = 20 * 1024 * 1024


def extract(path, filename, delimiter=None):
    suffix = Path(filename).suffix.lower()
    warnings, records = [], []
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('Şifreli PDF desteklenmiyor')
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or '').strip()
            if text:
                records.append((f'sayfa {number}', text))
            else:
                warnings.append(f'Sayfa {number}: metin yok; OCR gerekli')
    else:
        text = Path(path).read_text(encoding='utf-8-sig')
        if suffix == '.csv':
            if delimiter and delimiter not in (',', ';', '\t', '|'):
                raise ValueError('Geçersiz CSV ayırıcı')
            if not delimiter:
                try:
                    delimiter = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|').delimiter
                except csv.Error:
                    delimiter = ','
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            for index, row in enumerate(reader, 2):
                records.append((f'satır {index}', json.dumps(row, ensure_ascii=False)))
        elif suffix == '.json':
            def walk(value, address='$', depth=0):
                if depth > 64:
                    raise ValueError('JSON derinlik sınırı aşıldı')
                if isinstance(value, dict):
                    for key, child in value.items():
                        walk(child, address + '[' + json.dumps(key, ensure_ascii=False) + ']', depth + 1)
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        walk(child, f'{address}[{index}]', depth + 1)
                else:
                    records.append((address, address + ': ' + json.dumps(value, ensure_ascii=False)))
            walk(json.loads(text))
        else:
            raise ValueError('Yalnız PDF, CSV ve JSON destekleniyor')
    if not records:
        raise ValueError('Dosyada metin bulunamadı; tarama PDF için OCR gerekli')
    return records, warnings


class Embedder:
    def __init__(self, model_path):
        from llama_cpp import Llama, LLAMA_POOLING_TYPE_MEAN
        self.model = Llama(model_path=model_path, embedding=True, pooling_type=LLAMA_POOLING_TYPE_MEAN,
                           n_ctx=512, n_batch=512, n_ubatch=512, n_threads=2,
                           n_threads_batch=2, verbose=False)
        with open(model_path, 'rb') as stream:
            self.fingerprint = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.signature = self.fingerprint + ':mean:normalized:chunks-v2-verbatim:384:48'
        try:
            self.vector('embedding probe')
        except Exception:
            self.model.close()
            raise

    def chunks(self, text):
        # Detokenization of BERT embeddings lowercases and strips Turkish accents.
        # Find token-bounded character slices so stored evidence remains verbatim.
        def count(value):
            return len(self.model.tokenize(value.encode(), add_bos=False, special=False))
        offset = 0
        while offset < len(text):
            low, high = offset + 1, min(len(text), offset + 8192)
            while low < high:
                mid = (low + high + 1) // 2
                if count(text[offset:mid]) <= 384:
                    low = mid
                else:
                    high = mid - 1
            end = low
            yield text[offset:end]
            if end >= len(text):
                break
            low, high = offset + 1, end
            while low < high:
                mid = (low + high) // 2
                if count(text[mid:end]) <= 48:
                    high = mid
                else:
                    low = mid + 1
            offset = low

    def vector(self, text):
        tokens = self.model.tokenize(text.encode(), add_bos=False, special=False)
        if len(tokens) > 480:
            text = self.model.detokenize(tokens[:480]).decode('utf-8', errors='replace')
        vector = np.asarray(self.model.embed(text), dtype=np.float32)
        norm = np.linalg.norm(vector)
        if vector.ndim != 1 or not np.isfinite(vector).all() or not np.isfinite(norm) or norm <= 0:
            raise ValueError('Model geçerli embedding üretmiyor')
        return vector / norm

    def close(self):
        self.model.close()


class KnowledgeStore:
    def __init__(self, root=None, factory=Embedder):
        self.root = Path(root or os.environ.get('KUFIBOT_KNOWLEDGE_ROOT', '~/.local/share/kufibot/knowledge')).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.factory = factory
        self.model_lock = threading.Lock()
        self.index_slot = threading.Lock()
        self.db_lock = threading.RLock()
        self.cancel = threading.Event()
        self.busy = lambda: False
        self.jobs = {}
        self.rerun = set()
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, name TEXT, model TEXT,
                state TEXT, error TEXT, generation TEXT, signature TEXT);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, collection_id TEXT,
                name TEXT, digest TEXT, delimiter TEXT, warnings TEXT, UNIQUE(collection_id,digest));
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, generation TEXT, document_id TEXT,
                location TEXT, text TEXT, ordinal INTEGER);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(collections)')}
            if 'active_model' not in columns:
                db.execute("ALTER TABLE collections ADD COLUMN active_model TEXT NOT NULL DEFAULT ''")

    def connect(self):
        db = sqlite3.connect(self.root / 'knowledge.sqlite3', timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def collections(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM collections ORDER BY name')]

    def collection(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM collections WHERE id=?', (identifier(key),)).fetchone()
            if row is None:
                raise ValueError('Koleksiyon bulunamadı')
            return dict(row)

    def create(self, name, model):
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 120:
            raise ValueError('Koleksiyon adı gerekli')
        from .ai_settings import catalog
        match = next((m for m in catalog() if m['id'] == model and m['kind'] == 'embedding' and m['available']), None)
        if not match:
            raise ValueError('Kurulu embedding modeli seçilmeli')
        key = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO collections(id,name,model,state,error,generation,signature) VALUES(?,?,?,?,?,?,?)', (key, name, model, 'queued', '', '', ''))
        return self.collection(key)

    def set_model(self, key, model):
        self.collection(key)
        self.model_path(model)
        with self.connect() as db:
            db.execute('UPDATE collections SET model=? WHERE id=?', (model, key))
        self.queue(key)
        return self.collection(key)

    def preview(self, key, doc_id):
        document = next((d for d in self.documents(key) if d['id'] == identifier(doc_id)), None)
        if document is None:
            raise ValueError('Dosya bulunamadı')
        records, warnings = extract(self.root / doc_id, document['name'], document['delimiter'])
        return {'name': document['name'], 'records': [{'location': location, 'text': text[:4000]}
                 for location, text in records[:20]], 'warnings': warnings, 'truncated': len(records) > 20}

    def documents(self, key):
        self.collection(key)
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM documents WHERE collection_id=?', (key,))]

    def add(self, key, path, filename, delimiter=None):
        self.collection(key)
        path = Path(path)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('Dosya 20 MiB sınırını aşıyor')
        if Path(filename).suffix.lower() not in ('.pdf', '.csv', '.json'):
            raise ValueError('Yalnız PDF, CSV ve JSON destekleniyor')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with self.db_lock, self.connect() as db:
            old = db.execute('SELECT * FROM documents WHERE collection_id=? AND digest=?', (key, digest)).fetchone()
            if old:
                return dict(old)
            if db.execute('SELECT count(*) FROM documents WHERE collection_id=?', (key,)).fetchone()[0] >= 100:
                raise ValueError('Koleksiyonda en fazla 100 dosya olabilir')
            doc_id = uuid.uuid4().hex
            target = self.root / doc_id
            os.replace(path, target)
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)',
                       (doc_id, key, Path(filename).name[:200], digest, delimiter, '[]'))
        self.queue(key)
        return next(d for d in self.documents(key) if d['id'] == doc_id)

    def delete_document(self, key, doc_id):
        with self.db_lock, self.connect() as db:
            db.execute('DELETE FROM documents WHERE collection_id=? AND id=?', (identifier(key), identifier(doc_id)))
        # Retain source bytes for recoverability; deleted rows are immediately excluded from search.
        self.queue(key)

    def delete_collection(self, key):
        from .workflows import WorkflowStore
        for workflow in WorkflowStore().references():
            for node in workflow.get('nodes', []):
                data = node.get('data', {})
                if data.get('collection_id') == key or key in data.get('collection_ids', []):
                    raise ValueError('Koleksiyon bir workflow’a bağlı; önce bağlantıyı kaldırın')
        with self.db_lock, self.connect() as db:
            db.execute('DELETE FROM documents WHERE collection_id=?', (identifier(key),))
            db.execute('DELETE FROM collections WHERE id=?', (key,))

    def model_path(self, model_id):
        from .ai_settings import catalog
        model = next((m for m in catalog() if m['id'] == model_id and m['kind'] == 'embedding' and m['available']), None)
        if not model:
            raise ValueError('Embedding modeli bulunamadı')
        return model['path']

    def queue(self, key):
        identifier(key)
        with self.db_lock:
            if key in self.jobs and self.jobs[key].is_alive():
                self.rerun.add(key)
                return
            with self.connect() as db:
                db.execute("UPDATE collections SET state='queued', error='' WHERE id=?", (key,))
            job = threading.Thread(target=self.index, args=(key,), daemon=True)
            self.jobs[key] = job
            job.start()

    def index(self, key):
        with self.index_slot:
            self._index(key)

    def _index(self, key):
        embedder = None
        try:
            while self.busy() and not self.cancel.wait(.25):
                pass
            if self.cancel.is_set():
                return
            with self.model_lock:
                collection = self.collection(key)
                embedder = self.factory(self.model_path(collection['model']))
            with self.connect() as db:
                db.execute("UPDATE collections SET state='indexing' WHERE id=?", (key,))
            generation = uuid.uuid4().hex
            rows, vectors = [], []
            for document in self.documents(key):
                records, warnings = extract(self.root / document['id'], document['name'], document['delimiter'])
                with self.connect() as db:
                    db.execute('UPDATE documents SET warnings=? WHERE id=?', (json.dumps(warnings), document['id']))
                for location, text in records:
                    for chunk in embedder.chunks(text):
                        while self.busy() and not self.cancel.wait(.25):
                            pass
                        if self.cancel.is_set():
                            return
                        if len(rows) >= 20_000:
                            raise ValueError('20.000 parça sınırı aşıldı')
                        with self.model_lock:
                            vectors.append(embedder.vector(chunk))
                        rows.append((uuid.uuid4().hex, generation, document['id'], location, chunk, len(rows)))
            matrix = np.stack(vectors) if vectors else np.empty((0, 0), dtype=np.float32)
            pending = self.root / (generation + '.pending')
            with pending.open('wb') as stream:
                np.save(stream, matrix, allow_pickle=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, self.root / (generation + '.npy'))
            with self.db_lock, self.connect() as db:
                db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?)', rows)
                db.execute("UPDATE collections SET state='ready',error='',generation=?,signature=?,active_model=? WHERE id=?",
                           (generation, embedder.signature, collection['model'], key))
        except Exception as error:
            with self.connect() as db:
                db.execute("UPDATE collections SET state='failed',error=? WHERE id=?", (str(error), key))
        finally:
            if embedder:
                embedder.close()
            with self.db_lock:
                self.jobs.pop(key, None)
                if key in self.rerun and not self.cancel.is_set():
                    self.rerun.remove(key)
                    self.queue(key)

    def search(self, query, collection_ids, top_k=4):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError('Arama metni 1–4000 karakter olmalı')
        if type(top_k) is not int or not 1 <= top_k <= 8 or not isinstance(collection_ids, list) or not 1 <= len(collection_ids) <= 8:
            raise ValueError('Arama sınırları geçersiz')
        results = []
        for key in collection_ids:
            collection = self.collection(key)
            if not collection['generation']:
                raise ValueError('Koleksiyon henüz indekslenmedi')
            with self.model_lock:
                embedder = self.factory(self.model_path(collection['active_model'] or collection['model']))
                try:
                    if embedder.signature != collection['signature']:
                        raise ValueError('Embedding modeli değişti; yeniden indeksleyin')
                    query_vector = embedder.vector(query)
                finally:
                    embedder.close()
            matrix = np.load(self.root / (collection['generation'] + '.npy'), allow_pickle=False, mmap_mode='r')
            with self.connect() as db:
                rows = db.execute('''SELECT chunks.*, documents.name FROM chunks JOIN documents
                    ON documents.id=chunks.document_id WHERE generation=? AND documents.collection_id=?''',
                    (collection['generation'], key)).fetchall()
            for row in rows:
                results.append({'chunk_id': row['id'], 'collection_id': key, 'filename': row['name'],
                                'location': row['location'], 'text': row['text'],
                                'score': float(matrix[row['ordinal']] @ query_vector)})
        return {'status': 'ok', 'results': sorted(results, key=lambda r: r['score'], reverse=True)[:top_k]}
