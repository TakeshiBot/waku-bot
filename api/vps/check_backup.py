#!/usr/bin/env python3
"""Run with python3 check_backup.py; temporary DB only, no Telegram requests."""
import gzip
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import backup

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / 'data/iku.db/dbs/default/data'
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE items (name TEXT)')
        db.execute('INSERT INTO items VALUES (?)', ('test',))
    config = {'bot_token': '123456:' + 'x' * 32, 'chat_id': 123}
    backup.write_json(root / 'backup-telegram.json', config)
    backups = root / 'backups'
    backups.mkdir()
    for day in (1, 2, 3):
        (backups / f'waku-api-2020010{day}T000000Z.sqlite3.gz').write_bytes(b'old')
    untouched = backups / 'unrelated.txt'
    untouched.write_text('keep')
    with patch.object(backup, 'send_backup', side_effect=TimeoutError):
        try:
            backup.run(root)
            raise AssertionError('Send failure was ignored')
        except TimeoutError:
            pass
    assert len(list(backups.glob('*.gz'))) == 4
    pending = json.loads((root / 'backup-pending.json').read_text())['backup']

    def transfer(command, *, input, capture_output, timeout):
        assert command[0] == 'curl' and capture_output and timeout == 610
        assert config['bot_token'] not in ' '.join(command)
        assert config['bot_token'].encode() in input
        assert 'disable_content_type_detection=true' in command
        document = Path(command[command.index('--form') + 1].split('@', 1)[1].split(';', 1)[0])
        assert document.stat().st_mode & 0o777 == 0o600
        compressed = document.read_bytes()
        assert gzip.decompress(compressed).startswith(b'SQLite format 3\x00')
        (root / 'sent.sqlite3.gz').write_bytes(compressed)
        result = {'ok': True, 'result': {'message_id': 1, 'chat': {'id': 123},
                  'document': {'file_id': 'test-file', 'file_size': len(compressed)}}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result).encode(), stderr=b'')

    with patch.object(backup.subprocess, 'run', side_effect=transfer), patch.object(backup, 'snapshot', side_effect=AssertionError('Retry created another snapshot')):
        backup.run(root)
    assert not (root / 'backup-pending.json').exists()
    assert len(list(backups.glob('*.gz'))) == 2
    assert untouched.read_text() == 'keep'
    assert json.loads((root / 'backup-last-sent.json').read_text())['backup'] == pending
    output = root / 'restored.db'
    command = [sys.executable, str(Path(__file__).with_name('restore-backup.py')),
               str(root / 'sent.sqlite3.gz'), str(output)]
    subprocess.run(command, check=True, capture_output=True)
    with sqlite3.connect(output) as db:
        assert db.execute('SELECT name FROM items').fetchone() == ('test',)
    assert subprocess.run(command, capture_output=True).returncode != 0
print('Backup checks passed: failed sends retain files; retry reuses snapshot; gzip delivery; keep 2; safe restore.')
