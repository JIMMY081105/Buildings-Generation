"""Small evidence helpers; publication never overwrites an existing artifact."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def ref(path):
    path = Path(path)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha256(path)}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(payload)


def require_phase(path, schema=None):
    value = read_json(path)
    if value.get('status') != 'PASS' or (schema and value.get('schema') != schema):
        raise RuntimeError(f'Phase prerequisite not passed: {path}')
    if not value.get('rows'):
        raise RuntimeError(f'Phase prerequisite has no evidence rows: {path}')
    return value
