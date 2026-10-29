#!/usr/bin/env python3
"""Back up only the Waku image library's live libSQL database."""
from datetime import datetime, timezone
import gzip
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

root = Path(__file__).resolve().parent
source = root / 'data' / 'iku.db' / 'dbs' / 'default' / 'data'
backups = root / 'backups'
backups.mkdir(mode=0o700, exist_ok=True)
os.chmod(backups, 0o700)
if not source.is_file():
    raise SystemExit('Waku libSQL database was not found; no backup was made.')

stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
with tempfile.TemporaryDirectory(prefix='.waku-backup-', dir=backups) as temp:
    snapshot = Path(temp) / 'snapshot.sqlite3'
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30) as live:
        with sqlite3.connect(snapshot) as copy:
            live.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed.')
    compressed = Path(temp) / 'snapshot.sqlite3.gz'
    with snapshot.open('rb') as reader, gzip.open(compressed, 'wb', compresslevel=6) as writer:
        shutil.copyfileobj(reader, writer)
    os.chmod(compressed, 0o600)
    destination = backups / f'waku-api-{stamp}.sqlite3.gz'
    os.replace(compressed, destination)

# Only prune this service's own backup files, after a verified backup succeeds.
for old in sorted(backups.glob('waku-api-????????T??????Z.sqlite3.gz'), reverse=True)[14:]:
    old.unlink()
print('Waku image database backup completed:', destination.name)
