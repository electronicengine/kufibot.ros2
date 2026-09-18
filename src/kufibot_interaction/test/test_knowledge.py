import json
import shutil

import numpy as np
import pytest

from kufibot_interaction.knowledge import KnowledgeStore, extract


class FakeEmbedding:
    signature = 'test-v1'
    def __init__(self, path):
        pass
    def chunks(self, text):
        yield text
    def vector(self, text):
        return np.array([1, 0] if 'pil' in text.lower() else [0, 1], dtype=np.float32)
    def close(self):
        pass


@pytest.fixture
def store(tmp_path, monkeypatch):
    model = tmp_path / 'model.gguf'
    model.touch()
    monkeypatch.setattr('kufibot_interaction.ai_settings.catalog', lambda: [
        {'id': 'embedding', 'kind': 'embedding', 'available': True, 'path': str(model)}])
    value = KnowledgeStore(tmp_path / 'knowledge', FakeEmbedding)
    monkeypatch.setattr(value, 'queue', lambda key: None)
    return value


def test_csv_json_sources_and_invalid_documents(tmp_path):
    csv = tmp_path / 'a.csv'
    csv.write_text('\ufeffürün;voltaj\npil;12\n', encoding='utf-8')
    records, _ = extract(csv, 'a.csv')
    assert records[0][0] == 'satır 2' and 'voltaj' in records[0][1]
    data = tmp_path / 'a.json'
    data.write_text(json.dumps({'robot': {'pil': 12}}))
    records, _ = extract(data, 'a.json')
    assert records[0][0] == '$["robot"]["pil"]'
    data.write_text('{broken')
    with pytest.raises(ValueError):
        extract(data, 'a.json')
    with pytest.raises(ValueError):
        extract(csv, 'script.exe')


def test_index_search_delete_and_model_change(store, tmp_path):
    key = store.create('Kılavuz', 'embedding')['id']
    source = tmp_path / 'manual.json'
    source.write_text('{"pil":"12 volt", "renk":"mavi"}')
    backup = tmp_path / 'copy.json'
    shutil.copy(source, backup)
    document = store.add(key, source, 'manual.json')
    assert store.add(key, backup, 'same.json')['id'] == document['id']
    store.index(key)
    assert store.collection(key)['state'] == 'ready'
    result = store.search('pil', [key], 1)['results'][0]
    assert result['filename'] == 'manual.json' and result['location'] == '$["pil"]'
    assert result['score'] == 1
    old = FakeEmbedding.signature
    try:
        FakeEmbedding.signature = 'test-v2'
        with pytest.raises(ValueError, match='değişti'):
            store.search('pil', [key])
    finally:
        FakeEmbedding.signature = old
    store.delete_document(key, document['id'])
    assert store.search('pil', [key])['results'] == []


def test_failed_reindex_keeps_previous_generation(store, tmp_path):
    key = store.create('Test', 'embedding')['id']
    source = tmp_path / 'a.json'
    source.write_text('{"pil":12}')
    store.add(key, source, 'a.json')
    store.index(key)
    generation = store.collection(key)['generation']
    bad = tmp_path / 'bad.json'
    bad.write_text('{')
    store.add(key, bad, 'bad.json')
    store.index(key)
    assert store.collection(key)['state'] == 'failed'
    assert store.collection(key)['generation'] == generation
    assert store.search('pil', [key])['results']


def test_pdf_blank_encrypted_and_text(tmp_path):
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    blank = tmp_path / 'blank.pdf'
    writer.write(blank)
    with pytest.raises(ValueError, match='OCR'):
        extract(blank, 'blank.pdf')
    writer.encrypt('secret')
    writer.write(blank)
    with pytest.raises(ValueError, match='Şifreli'):
        extract(blank, 'blank.pdf')
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=100)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 10 50 Td (Robot pili 12 volt.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(blank)
    records, _ = extract(blank, 'manual.pdf')
    assert records == [('sayfa 1', 'Robot pili 12 volt.')]


def test_chunking_preserves_turkish_source_text():
    from kufibot_interaction.knowledge import Embedder
    embedder = object.__new__(Embedder)
    class Tokenizer:
        def tokenize(self, value, **kwargs):
            return list(value.decode())
    embedder.model = Tokenizer()
    text = 'İstanbul’da Şarj süresi iki saattir. ' * 40
    chunks = list(embedder.chunks(text))
    assert len(chunks) > 1
    assert all(chunk in text and len(chunk) <= 384 for chunk in chunks)
    assert chunks[0].startswith('İstanbul’da Şarj')
