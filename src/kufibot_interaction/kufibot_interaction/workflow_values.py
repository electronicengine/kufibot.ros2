"""Safe workflow data references and output projection (no evaluation)."""
import copy
import re

MISSING = object()
ROOTS = {'user', 'result', 'last_result', 'results', 'last_output', 'outputs'}


def validate_reference(path):
    if not isinstance(path, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*', path) or path.split('.')[0] not in ROOTS:
        raise ValueError('Geçersiz veri alanı: ' + str(path))


def read_value(context, path, *, strict=True, fresh=True):
    validate_reference(path)
    parts = path.split('.')
    if fresh:
        origin = parts[1] if parts[0] == 'outputs' and len(parts) > 1 else context.get('_last_output_node') if parts[0] == 'last_output' else None
        if origin and context.get('_output_status', {}).get(origin) in ('stale', 'unavailable', 'error', 'cancelled') and parts[-1] not in ('status', 'error'):
            raise ValueError('Veri alanı güncel değil: ' + path)
    value = context
    for part in parts:
        if fresh and isinstance(value, dict) and value.get('status') in ('stale', 'unavailable', 'error', 'cancelled') and part not in ('status', 'error'):
            raise ValueError('Veri alanı güncel değil: ' + path)
        if isinstance(value, dict):
            value = value.get(part, MISSING)
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            value = MISSING
        if value is MISSING:
            if strict:
                raise ValueError('Veri alanı bulunamadı: ' + path)
            return None
    return copy.deepcopy(value)


def validate_expression(value):
    if isinstance(value, dict):
        if '$ref' in value:
            if set(value) != {'$ref'}:
                raise ValueError('Alan başvurusu yalnız $ref içermeli')
            validate_reference(value['$ref'])
        else:
            for item in value.values():
                validate_expression(item)
    elif isinstance(value, list):
        for item in value:
            validate_expression(item)


def resolve(value, context):
    if isinstance(value, dict):
        if '$ref' in value:
            validate_expression(value)
            return read_value(context, value['$ref'])
        return {key: resolve(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, context) for item in value]
    return value


def validate_output(output):
    if not isinstance(output, dict) or output.get('mode', 'raw') not in ('raw', 'user', 'text', 'json'):
        raise ValueError('Geçersiz node çıkış sonucu')
    if output.get('mode', 'raw') not in ('raw', 'user'):
        if 'value' not in output:
            raise ValueError('Node çıkış değeri gerekli')
        validate_expression(output['value'])
        if output['mode'] == 'text' and not isinstance(output['value'], (str, dict)):
            raise ValueError('Metin çıkışı metin veya alan başvurusu olmalı')


def project(output, result, context):
    validate_output(output)
    if output.get('mode', 'raw') == 'raw' or isinstance(result, dict) and result.get('status') in ('error', 'cancelled'):
        return copy.deepcopy(result)
    if output['mode'] == 'user':
        return {'text': read_value(context, 'user')}
    value = resolve(output['value'], {**context, 'result':result})
    if output['mode'] == 'text' and not isinstance(value, str):
        raise ValueError('Node metin çıkışı metin olmalı')
    return value
