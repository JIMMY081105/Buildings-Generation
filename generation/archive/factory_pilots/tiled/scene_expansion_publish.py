"""Verify task-owned metadata bytes and publish atomically; CPU-only."""
import base64
import hashlib
import json
import os
import pathlib
import sys

ROOT = pathlib.Path('/cephfs/shared/yixing/outputs/factory80_20261011')
WORK = ROOT / 'work/scene_expansion'
expected = json.loads(base64.b64decode(sys.argv[1]))
source = pathlib.Path(expected['temporary'])
destination = pathlib.Path(expected['destination'])
deletion_journal = WORK / 'whole3440_actual_deletion_receipts.jsonl'
assert destination in {ROOT / 'SCENE_EXPANSION_600.json', WORK / 'scene_expansion_open_issues.json', WORK / 'verified_source_industrial_diversity.json', WORK / 'scene_expansion_failure_history.json', WORK / 'source630_scope_actual_metadata_audit.json', WORK / 'whole3440_cleanup_receipt.json', WORK / 'whole3440_windows_actual_file_sha_verified.json', WORK / 'whole3440_cleanup_attempt1_failed.json', WORK / 'whole3440_windows_resume_actual_sha_verified.json', deletion_journal}
assert source.parent == WORK / 'publish'
content = source.read_bytes()
measured_sha = hashlib.sha256(content).hexdigest()
assert len(content) == expected['size'] and measured_sha == expected['sha256']
if destination == deletion_journal:
    rows = [json.loads(line) for line in content.decode('utf-8').splitlines() if line.strip()]
    assert len(rows) == 3440 and len({(row['source'], row['package']) for row in rows}) == 3440
    assert all(row['status'] == 'ACTUAL_UNLINK_RETURNED_AND_PATH_ABSENT' for row in rows)
    assert sum(row['actual_deleted_bytes'] for row in rows) == 38764058592
else:
    assert isinstance(json.loads(content), dict)
os.replace(source, destination)
final = destination.read_bytes()
final_sha = hashlib.sha256(final).hexdigest()
assert len(final) == expected['size'] and final_sha == expected['sha256']
print(json.dumps({'status': 'PASS', 'publication': 'ATOMIC_REPLACE', 'destination': str(destination), 'final_size_actually_measured': len(final), 'final_sha256_actually_measured': final_sha}))
