import sqlite3
from contextlib import contextmanager
from .config import DB_PATH


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    vault_salt TEXT,
    vault_check TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_items (
    id TEXT PRIMARY KEY,
    ciphertext TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    local_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    issue_date TEXT,
    expiry_date TEXT,
    extracted_json TEXT NOT NULL DEFAULT '{}',
    cloud_json TEXT NOT NULL DEFAULT '{}',
    processing_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    category TEXT NOT NULL,
    merchant TEXT NOT NULL,
    spent_on TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    note TEXT NOT NULL,
    receipt_document_id TEXT REFERENCES documents(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit_minor INTEGER NOT NULL CHECK (monthly_limit_minor > 0),
    currency TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    event_date TEXT NOT NULL,
    send_on TEXT NOT NULL,
    email_enabled INTEGER NOT NULL DEFAULT 1,
    document_id TEXT REFERENCES documents(id),
    sent_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(send_on, sent_at);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(spent_on);
"""


def initialize() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_dict(row):
    return dict(row) if row is not None else None
