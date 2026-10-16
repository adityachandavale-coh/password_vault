"""Encrypted, portable backup of HomeHub data and rclone configuration."""
import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b'HHBK1'
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CHUNK = 1024 * 1024


def key_for(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1,
                          maxmem=128 * 1024 * 1024, dklen=32)


def make_zip(path: Path, data_dir: Path, rclone_dir: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / 'homehub.db'
        if (data_dir / 'homehub.db').exists():
            source = sqlite3.connect(data_dir / 'homehub.db')
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            if snapshot.exists():
                archive.write(snapshot, 'data/homehub.db')
            for directory, prefix in ((data_dir, 'data'), (rclone_dir, 'rclone')):
                if not directory.exists():
                    continue
                for item in directory.rglob('*'):
                    if item.is_file() and item.name not in {'homehub.db', 'homehub.db-wal', 'homehub.db-shm'} and item.suffix != '.hhb':
                        archive.write(item, f'{prefix}/{item.relative_to(directory).as_posix()}')


def encrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    salt, nonce = os.urandom(16), os.urandom(12)
    cipher = Cipher(algorithms.AES(key_for(passphrase, salt)), modes.GCM(nonce)).encryptor()
    with source.open('rb') as original, destination.open('xb') as output:
        output.write(MAGIC + salt + nonce)
        while chunk := original.read(CHUNK):
            output.write(cipher.update(chunk))
        output.write(cipher.finalize())
        output.write(cipher.tag)


def decrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    with source.open('rb') as original:
        if original.read(len(MAGIC)) != MAGIC:
            raise ValueError('This is not a HomeHub backup')
        salt, nonce = original.read(16), original.read(12)
        end = source.stat().st_size
        original.seek(end - 16)
        tag = original.read(16)
        original.seek(len(MAGIC) + 28)
        cipher = Cipher(algorithms.AES(key_for(passphrase, salt)), modes.GCM(nonce, tag)).decryptor()
        remaining = end - 16 - original.tell()
        with destination.open('wb') as output:
            while remaining:
                chunk = original.read(min(CHUNK, remaining))
                if not chunk:
                    raise ValueError('Backup is truncated')
                output.write(cipher.update(chunk))
                remaining -= len(chunk)
            try:
                output.write(cipher.finalize())
            except InvalidTag:
                raise ValueError('Incorrect passphrase or damaged backup')


def backup(destination: Path, data_dir: Path, rclone_dir: Path) -> None:
    first = getpass.getpass('Backup passphrase: ')
    if len(first) < 12 or first != getpass.getpass('Repeat passphrase: '):
        raise ValueError('Passphrase must be at least 12 characters and match')
    with tempfile.TemporaryDirectory() as temporary:
        plain = Path(temporary) / 'backup.zip'
        make_zip(plain, data_dir, rclone_dir)
        encrypt_file(plain, destination, first)
    print(f'Encrypted backup created: {destination}')


def restore(source: Path, data_dir: Path, rclone_dir: Path) -> None:
    if any(path.exists() and any(path.iterdir()) for path in (data_dir, rclone_dir)):
        raise ValueError('Restore requires empty data and rclone directories on the destination host')
    passphrase = getpass.getpass('Backup passphrase: ')
    with tempfile.TemporaryDirectory() as temporary:
        plain = Path(temporary) / 'backup.zip'
        decrypt_file(source, plain, passphrase)
        with zipfile.ZipFile(plain) as archive:
            for member in archive.infolist():
                member_path = Path(member.filename)
                if member.is_dir():
                    continue
                if member_path.parts[0] not in {'data', 'rclone'} or '..' in member_path.parts:
                    raise ValueError('Backup contains an invalid path')
                destination = (data_dir if member_path.parts[0] == 'data' else rclone_dir).joinpath(*member_path.parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as original, destination.open('xb') as output:
                    while chunk := original.read(CHUNK):
                        output.write(chunk)
    print('Backup restored. Keep the vault master password; it is not stored in the backup.')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['create', 'restore'])
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    from homehub.config import DATA_DIR
    if args.action == 'create':
        backup(args.archive, DATA_DIR, ROOT / 'rclone')
    else:
        restore(args.archive, DATA_DIR, ROOT / 'rclone')


if __name__ == '__main__':
    main()
