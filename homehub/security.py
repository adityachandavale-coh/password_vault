import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request
from .config import DATA_DIR
from .db import connection


PASSWORD_HASHER = PasswordHasher()
KEY_PATH = DATA_DIR / 'host.key'
SETUP_TOKEN_PATH = DATA_DIR / 'setup.token'


def setup_token() -> str:
    if not SETUP_TOKEN_PATH.exists():
        try:
            fd = os.open(SETUP_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                file.write(secrets.token_urlsafe(24))
        except FileExistsError:
            pass
    return SETUP_TOKEN_PATH.read_text(encoding='utf-8').strip()


def verify_setup_token(candidate: str) -> bool:
    return hmac.compare_digest(candidate, setup_token())


def host_key() -> bytes:
    if not KEY_PATH.exists():
        key = AESGCM.generate_key(bit_length=256)
        try:
            fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(key)
        except FileExistsError:
            pass
    return KEY_PATH.read_bytes()


def seal_bytes(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(host_key()).encrypt(nonce, data, None)


def open_bytes(data: bytes) -> bytes:
    return AESGCM(host_key()).decrypt(data[:12], data[12:], None)


def issue_session() -> str:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    with connection() as conn:
        conn.execute('INSERT INTO sessions VALUES (?, 1, ?)', (digest, expiry))
    return token


def revoke_session(token: str | None) -> None:
    if not token:
        return
    with connection() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))


def require_user(request: Request) -> None:
    token = request.cookies.get('homehub_session')
    if not token:
        raise HTTPException(401, 'Sign in required')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connection() as conn:
        row = conn.execute('SELECT expires_at FROM sessions WHERE token_hash=?', (digest,)).fetchone()
    if not row or datetime.fromisoformat(row['expires_at']) < datetime.now(timezone.utc):
        raise HTTPException(401, 'Session expired')


def require_same_origin(request: Request) -> None:
    if request.method in {'GET', 'HEAD', 'OPTIONS'}:
        return
    if request.headers.get('x-homehub-request') != '1':
        raise HTTPException(403, 'App request header required')
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
        raise HTTPException(403, 'Cross-origin request denied')
