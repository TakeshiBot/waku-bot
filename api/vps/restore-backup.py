#!/usr/bin/env python3
"""Restore a Telegram backup into a NEW SQLite file; never change the live DB."""
import argparse
import base64
import gzip
import os
import shutil
import sqlite3
import tempfile
from io import BytesIO
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('backup', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--key-file', type=Path, help='Only needed for older encrypted backups.')
args = parser.parse_args()
os.umask(0o077)
output = args.output.resolve()
if output.exists():
    parser.error('Output already exists; choose a new path.')
compressed = args.backup.read_bytes()
if not compressed.startswith(b'\x1f\x8b'):
    if not args.key_file:
        parser.error('Older encrypted backups need --key-file.')
    from cryptography.fernet import Fernet

    if compressed.startswith(b'\x80'):
        compressed = base64.urlsafe_b64encode(compressed)
    compressed = Fernet(args.key_file.read_bytes().strip()).decrypt(compressed)
with tempfile.TemporaryDirectory(prefix='.waku-restore-', dir=output.parent) as temp:
    restored = Path(temp) / 'restored.sqlite3'
    with gzip.GzipFile(fileobj=BytesIO(compressed)) as reader, restored.open('wb') as writer:
        shutil.copyfileobj(reader, writer)
    with sqlite3.connect(restored.as_uri() + '?mode=ro', uri=True) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Restored database failed integrity check.')
    os.chmod(restored, 0o600)
    os.link(restored, output)
print('Verified SQLite database restored:', output)
