#!/usr/bin/env python3
"""Verified libSQL snapshots, with gzip copies sent by Telegram."""
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKUP_NAME = re.compile(r'waku-api-\d{8}T\d{6}Z\.sqlite3\.gz')


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value) + '\n', encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def snapshot(root):
    source = root / 'data' / 'iku.db' / 'dbs' / 'default' / 'data'
    if not source.is_file():
        raise RuntimeError('Waku libSQL database not found.')
    backups = root / 'backups'
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    destination = backups / f'waku-api-{stamp}.sqlite3.gz'
    deadline = time.monotonic() + 90

    def progress(*_):
        if time.monotonic() > deadline:
            raise TimeoutError('Database snapshot exceeded 90 seconds.')

    with tempfile.TemporaryDirectory(prefix='.waku-backup-', dir=backups) as temp:
        copy_path = Path(temp) / 'snapshot.sqlite3'
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30) as live:
            with sqlite3.connect(copy_path) as copy:
                live.backup(copy, pages=256, progress=progress, sleep=0.1)
                if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed.')
        compressed = Path(temp) / 'snapshot.sqlite3.gz'
        with copy_path.open('rb') as reader, gzip.open(compressed, 'wb', compresslevel=6) as writer:
            shutil.copyfileobj(reader, writer)
        os.chmod(compressed, 0o600)
        os.replace(compressed, destination)
    return destination


def send_backup(config, path):
    """Send the gzip archive directly to its owner's chat, not the upload API."""
    size = path.stat().st_size
    # ponytail: one document below 50 MB; add multipart archives if this ceiling is reached.
    if size > 49_000_000:
        raise RuntimeError('Backup exceeds the Telegram document limit; split it before sending.')
    with path.open('rb') as reader:
        checksum = hashlib.file_digest(reader, 'sha256').hexdigest()
    stamp = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m/%Y %H:%M')
    caption = (f'Waku API · Backup database\n{stamp} (UTC+7)\n'
               f'SQLite đã kiểm tra · gzip\nSHA-256: {checksum}')
    print('Sending Waku API backup:', path.name, 'bytes:', size, flush=True)
    # Token stays on stdin, outside process arguments and error logs.
    response = subprocess.run([
        'curl', '--disable', '--silent', '--show-error', '--fail-with-body',
        '--connect-timeout', '15', '--max-time', '600',
        '--form-string', f'chat_id={config["chat_id"]}',
        '--form-string', f'caption={caption}',
        '--form-string', 'disable_content_type_detection=true',
        '--form', f'document=@{path};type=application/gzip',
        '--config', '-',
    ], input=f'url = "https://api.telegram.org/bot{config["bot_token"]}/sendDocument"\n'.encode(),
        capture_output=True, timeout=610)
    if response.returncode:
        raise RuntimeError(f'Telegram transfer failed (curl exit {response.returncode}).')
    result = json.loads(response.stdout)
    if result.get('ok') is not True:
        raise RuntimeError('Telegram sendDocument was not acknowledged.')
    message = result['result']
    if (message['chat']['id'] != config['chat_id']
            or not message['document'].get('file_id')
            or message['document'].get('file_size') != size):
        raise RuntimeError('Telegram document acknowledgement is incomplete.')
    return {'backup': path.name, 'message_id': message['message_id'],
            'file_id': message['document']['file_id'], 'chat_id': config['chat_id'],
            'sha256': checksum, 'sent_at': datetime.now(UTC).isoformat()}


def run(root):
    config = json.loads((root / 'backup-telegram.json').read_text(encoding='utf-8'))
    if (not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,}', config['bot_token'])
            or type(config['chat_id']) is not int or config['chat_id'] == 0):
        raise ValueError('Invalid Telegram backup configuration.')
    backups = root / 'backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    os.chmod(backups, 0o700)
    pending = root / 'backup-pending.json'
    if pending.exists():
        name = json.loads(pending.read_text(encoding='utf-8'))['backup']
        if not isinstance(name, str) or not BACKUP_NAME.fullmatch(name):
            raise ValueError('Invalid pending backup name.')
        path = backups / name
        if not path.is_file():
            raise RuntimeError('Pending backup is missing; do not discard its state.')
    else:
        path = snapshot(root)
        write_json(pending, {'backup': path.name})
    receipt = send_backup(config, path)
    write_json(root / 'backup-last-sent.json', receipt)
    # Prune only after a verified snapshot and a complete Telegram acknowledgement.
    files = sorted((p for p in backups.iterdir() if p.is_file() and BACKUP_NAME.fullmatch(p.name)), reverse=True)
    for old in files[2:]:
        old.unlink()
    pending.unlink()
    print('Waku API database backup sent:', path.name, 'message_id:', receipt['message_id'])


def main():
    root = Path(__file__).resolve().parent
    os.umask(0o077)
    with (root / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(root)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # HTTP exceptions can contain request URLs with the bot token.
        detail = f': {error}' if isinstance(error, RuntimeError) else ''
        print(f'Waku API database backup failed ({type(error).__name__}){detail}; pending files retained.', file=sys.stderr)
        sys.exit(1)
