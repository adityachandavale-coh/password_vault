import base64
import json
import os
import re
import shutil
import smtplib
import subprocess
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
import pymupdf
import httpx
from .config import DATA_DIR, OLLAMA_MODEL, OLLAMA_URL, RCLONE_FOLDER, REMOTES, TIMEZONE, REMINDER_HOUR
from .db import connection
from .security import open_bytes, seal_bytes


def today() -> str:
    return datetime.now(TIMEZONE).date().isoformat()


def valid_date(value: str | None) -> str | None:
    if not value:
        return None
    return date.fromisoformat(value).isoformat()


def make_reminder(title: str, event_date: str, lead_days: int = 30,
                  document_id: str | None = None, email_enabled: bool = True) -> str:
    event = date.fromisoformat(event_date)
    if lead_days < 0 or lead_days > 3650:
        raise ValueError('Lead days must be between 0 and 3650')
    reminder_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute('''INSERT INTO reminders
            (id,title,event_date,send_on,email_enabled,document_id,created_at)
            VALUES (?,?,?,?,?,?,?)''',
            (reminder_id, title.strip(), event.isoformat(),
             (event - timedelta(days=lead_days)).isoformat(),
             int(email_enabled), document_id, datetime.now(timezone.utc).isoformat()))
    return reminder_id


def encrypted_document_path(document_id: str) -> Path:
    folder = DATA_DIR / 'documents'
    folder.mkdir(exist_ok=True)
    return folder / f'{document_id}.bin'


def save_document(filename: str, category: str, mime_type: str, data: bytes) -> str:
    if not data or len(data) > 20 * 1024 * 1024:
        raise ValueError('Document must be between 1 byte and 20 MB')
    if mime_type not in {'application/pdf', 'image/jpeg', 'image/png', 'image/webp'}:
        raise ValueError('Upload a PDF, JPEG, PNG, or WebP image')
    document_id = str(uuid.uuid4())
    encrypted_document_path(document_id).write_bytes(seal_bytes(data))
    with connection() as conn:
        conn.execute('''INSERT INTO documents
            (id,name,category,mime_type,local_name,size_bytes,created_at)
            VALUES (?,?,?,?,?,?,?)''',
            (document_id, Path(filename).name[:150], category[:60], mime_type,
             f'{document_id}.bin', len(data), datetime.now(timezone.utc).isoformat()))
    return document_id


def read_document(document_id: str) -> bytes:
    with connection() as conn:
        row = conn.execute('SELECT id FROM documents WHERE id=?', (document_id,)).fetchone()
    if not row:
        raise FileNotFoundError(document_id)
    return open_bytes(encrypted_document_path(document_id).read_bytes())


def _ollama(messages: list[dict], schema: dict) -> dict:
    if not OLLAMA_MODEL:
        raise RuntimeError('Ollama model is not configured')
    response = httpx.post(f'{OLLAMA_URL}/api/chat', json={
        'model': OLLAMA_MODEL, 'messages': messages, 'format': schema,
        'stream': False, 'options': {'temperature': 0}
    }, timeout=120)
    response.raise_for_status()
    return json.loads(response.json()['message']['content'])


INTENT_SCHEMA = {
    'type': 'object',
    'properties': {
        'kind': {'type': 'string', 'enum': ['expense', 'reminder', 'document', 'vault', 'report', 'unknown']},
        'amount': {'type': ['number', 'null']},
        'currency': {'type': 'string'},
        'category': {'type': 'string'},
        'merchant': {'type': 'string'},
        'date': {'type': ['string', 'null']},
        'title': {'type': 'string'},
        'payment_method': {'type': 'string'},
        'note': {'type': 'string'},
        'report': {'type': 'string'}
    },
    'required': ['kind', 'amount', 'currency', 'category', 'merchant', 'date',
                 'title', 'payment_method', 'note', 'report']
}


def interpret_prompt(prompt: str) -> dict:
    if not prompt.strip() or len(prompt) > 1500:
        raise ValueError('Enter a request up to 1500 characters')
    if OLLAMA_MODEL:
        try:
            result = _ollama([
                {'role': 'system', 'content': 'Extract one HomeHub action. Use ISO dates. Today is '
                 + today() + '. Do not invent missing values. Never request or output a password. '
                 'For reports choose expenses, documents, reminders, or vault. Return only the schema.'},
                {'role': 'user', 'content': prompt}
            ], INTENT_SCHEMA)
            return validate_intent(result)
        except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError):
            pass
    return fallback_intent(prompt)


def validate_intent(value: dict) -> dict:
    if value.get('kind') not in {'expense', 'reminder', 'document', 'vault', 'report', 'unknown'}:
        raise ValueError('Unrecognized action')
    if value.get('date'):
        try:
            value['date'] = valid_date(value['date'])
        except ValueError:
            value['date'] = None
    amount = value.get('amount')
    if amount is not None and (not isinstance(amount, (int, float)) or amount <= 0 or amount > 1_000_000_000):
        value['amount'] = None
    return value


def fallback_intent(prompt: str) -> dict:
    lower = prompt.lower()
    amount = re.search(r'(?:₹|rs\.?|inr\s*)\s*([\d,]+(?:\.\d{1,2})?)', lower)
    kind = ('report' if any(w in lower for w in ('report', 'show', 'how much', 'summary')) else
            'expense' if amount or any(w in lower for w in ('paid', 'spent', 'expense')) else
            'reminder' if any(w in lower for w in ('remind', 'renew', 'expires')) else
            'vault' if 'password' in lower or 'login' in lower else 'unknown')
    categories = {'Fuel': ('fuel', 'petrol', 'diesel'), 'Groceries': ('groceries', 'grocery'),
                  'Food': ('restaurant', 'lunch', 'dinner', 'breakfast'),
                  'Transport': ('taxi', 'uber', 'ola', 'train', 'bus'),
                  'Bills': ('electricity', 'internet', 'phone bill'),
                  'Health': ('doctor', 'medicine', 'hospital'),
                  'Travel': ('flight', 'hotel', 'trip')}
    category = next((name for name, words in categories.items() if any(word in lower for word in words)), 'Other')
    payment = next((name for name in ('UPI', 'card', 'cash') if name.lower() in lower), '')
    mentioned_date = re.search(r'\b\d{4}-\d{2}-\d{2}\b', lower)
    spent_on = (date.fromisoformat(today()) - timedelta(days=1)).isoformat() if 'yesterday' in lower else today()
    if mentioned_date:
        try:
            spent_on = valid_date(mentioned_date.group())
        except ValueError:
            pass
    return {'kind': kind, 'amount': float(amount.group(1).replace(',', '')) if amount else None,
            'currency': 'INR', 'category': category, 'merchant': '', 'date': spent_on,
            'title': prompt.strip()[:100], 'payment_method': payment, 'note': '',
            'report': 'expenses' if kind == 'report' else ''}


EXTRACTION_SCHEMA = {
    'type': 'object',
    'properties': {
        'document_type': {'type': 'string'},
        'issue_date': {'type': ['string', 'null']},
        'expiry_date': {'type': ['string', 'null']},
        'merchant': {'type': 'string'},
        'total': {'type': ['number', 'null']},
        'evidence': {'type': 'string'}
    },
    'required': ['document_type', 'issue_date', 'expiry_date', 'merchant', 'total', 'evidence']
}


def extract_document(document_id: str) -> dict:
    with connection() as conn:
        row = conn.execute('SELECT mime_type FROM documents WHERE id=?', (document_id,)).fetchone()
    if not row:
        raise FileNotFoundError(document_id)
    raw = read_document(document_id)
    images = []
    extracted_text = ''
    if row['mime_type'] == 'application/pdf':
        pdf = pymupdf.open(stream=raw, filetype='pdf')
        for page in list(pdf)[:2]:
            extracted_text += page.get_text()[:6000] + '\n'
            if len(extracted_text.strip()) < 60:
                images.append(base64.b64encode(page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes('png')).decode())
    else:
        images.append(base64.b64encode(raw).decode())
    if not OLLAMA_MODEL:
        result = {'document_type': '', 'issue_date': None, 'expiry_date': None,
                  'merchant': '', 'total': None, 'evidence': extracted_text[:1000]}
    else:
        result = _ollama([
            {'role': 'system', 'content': 'Extract fields exactly from this personal document or receipt. '
             'Use ISO dates; distinguish issue and expiry dates. If unclear return null. '
             'Evidence should quote the labels near extracted dates. Do not invent fields.'},
            {'role': 'user', 'content': extracted_text[:6000] or 'Read the attached document.',
             'images': images[:2]}
        ], EXTRACTION_SCHEMA)
    for key in ('issue_date', 'expiry_date'):
        try:
            result[key] = valid_date(result.get(key))
        except (ValueError, TypeError):
            result[key] = None
    with connection() as conn:
        conn.execute('UPDATE documents SET extracted_json=?, processing_status=? WHERE id=?',
                     (json.dumps(result), 'review', document_id))
    return result


def _rclone_target(remote: str, category: str, filename: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', remote):
        raise ValueError('Invalid rclone remote name')
    folder = re.sub(r'[^A-Za-z0-9 _-]', '', RCLONE_FOLDER).strip() or 'HomeHub'
    safe_category = re.sub(r'[^A-Za-z0-9 _-]', '', category).strip() or 'General'
    return f'{remote}:{folder}/{safe_category}/{filename}'


def sync_document(document_id: str) -> dict:
    with connection() as conn:
        row = conn.execute('SELECT name,category,mime_type,cloud_json FROM documents WHERE id=?',
                           (document_id,)).fetchone()
    if not row:
        raise FileNotFoundError(document_id)
    results = json.loads(row['cloud_json'])
    suffix = Path(row['name']).suffix.lower() or ('.pdf' if row['mime_type'] == 'application/pdf' else '.jpg')
    destination_name = f'{document_id}{suffix}'
    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / destination_name
        source.write_bytes(read_document(document_id))
        for provider, remote in REMOTES.items():
            if not remote or results.get(provider, {}).get('status') == 'synced':
                continue
            target = _rclone_target(remote, row['category'], destination_name)
            try:
                completed = subprocess.run(['rclone', 'copyto', str(source), target],
                                           capture_output=True, text=True, timeout=180)
                if completed.returncode:
                    raise RuntimeError(completed.stderr[-250:] or 'rclone upload failed')
                results[provider] = {'status': 'synced', 'path': target}
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                results[provider] = {'status': 'error', 'message': str(error)[:250]}
    with connection() as conn:
        conn.execute('UPDATE documents SET cloud_json=? WHERE id=?', (json.dumps(results), document_id))
    return results


def cloud_root(provider: str) -> str:
    remote = REMOTES.get(provider)
    if not remote:
        raise ValueError(f'{provider} is not configured')
    return _rclone_target(remote, 'General', 'file').rsplit('/General/file', 1)[0]


def safe_cloud_path(path: str) -> str:
    parts = path.replace('\\', '/').split('/')
    if not path or any(part in {'', '.', '..'} for part in parts) or path.startswith('/'):
        raise ValueError('Invalid cloud file path')
    return '/'.join(parts)


def list_cloud_files(provider: str) -> list[dict]:
    target = cloud_root(provider)
    result = subprocess.run(['rclone', 'lsjson', target, '--recursive', '--max-depth', '5'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr[-250:] or 'Cloud listing failed')
    files = json.loads(result.stdout)
    return [{'name': item.get('Name', ''), 'path': item.get('Path', ''),
             'size': item.get('Size', 0), 'modified': item.get('ModTime', '')}
            for item in files if not item.get('IsDir')][:500]


def read_cloud_file(provider: str, path: str) -> bytes:
    relative = safe_cloud_path(path)
    target = f'{cloud_root(provider)}/{relative}'
    stat = subprocess.run(['rclone', 'lsjson', target, '--stat'],
                          capture_output=True, text=True, timeout=30)
    if stat.returncode:
        raise FileNotFoundError(path)
    size = json.loads(stat.stdout).get('Size', 0)
    if size > 20 * 1024 * 1024:
        raise ValueError('Cloud preview is limited to 20 MB')
    result = subprocess.run(['rclone', 'cat', target], capture_output=True, timeout=90)
    if result.returncode or len(result.stdout) > 20 * 1024 * 1024:
        raise RuntimeError('Could not open cloud file')
    return result.stdout


def send_due_emails() -> int:
    if datetime.now(TIMEZONE).hour < REMINDER_HOUR:
        return 0
    host = os.getenv('HOMEHUB_SMTP_HOST', '')
    sender = os.getenv('HOMEHUB_SMTP_FROM', '')
    recipient = os.getenv('HOMEHUB_REMINDER_TO', '')
    if not host or not sender or not recipient:
        return 0
    with connection() as conn:
        rows = conn.execute('''SELECT * FROM reminders WHERE send_on<=? AND event_date>=?
            AND sent_at IS NULL AND email_enabled=1 ORDER BY send_on LIMIT 20''',
            (today(), today())).fetchall()
    sent = 0
    for row in rows:
        message = EmailMessage()
        message['Subject'] = f'HomeHub reminder: {row["title"]}'
        message['From'] = sender
        message['To'] = recipient
        message.set_content(f'{row["title"]}\nDate: {row["event_date"]}\n')
        try:
            with smtplib.SMTP(host, int(os.getenv('HOMEHUB_SMTP_PORT', '587')), timeout=20) as smtp:
                smtp.starttls()
                user = os.getenv('HOMEHUB_SMTP_USER', '')
                password = os.getenv('HOMEHUB_SMTP_PASSWORD', '')
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)
            with connection() as conn:
                conn.execute('UPDATE reminders SET sent_at=?,last_error=NULL WHERE id=?',
                             (datetime.now(timezone.utc).isoformat(), row['id']))
            sent += 1
        except (OSError, smtplib.SMTPException) as error:
            with connection() as conn:
                conn.execute('UPDATE reminders SET last_error=? WHERE id=?', (str(error)[:200], row['id']))
    return sent


def ics_for_reminder(row: dict) -> bytes:
    safe_title = row['title'].replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', ' ')
    start = date.fromisoformat(row['event_date'])
    end = start + timedelta(days=1)
    content = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//HomeHub//EN\r\n'
               'BEGIN:VEVENT\r\nUID:' + row['id'] + '@homehub.local\r\n'
               'DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '\r\n'
               'DTSTART;VALUE=DATE:' + start.strftime('%Y%m%d') + '\r\n'
               'DTEND;VALUE=DATE:' + end.strftime('%Y%m%d') + '\r\n'
               'SUMMARY:' + safe_title + '\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
    return content.encode('utf-8')
