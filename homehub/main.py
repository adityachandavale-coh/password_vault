import asyncio
import csv
import io
import mimetypes
import json
import os
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import httpx
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import DATA_DIR, OLLAMA_MODEL, OLLAMA_URL, REMOTES, TIMEZONE, REMINDER_HOUR
from .db import connection, initialize, row_dict
from .security import (PASSWORD_HASHER, issue_session, require_same_origin, require_user,
                       revoke_session, setup_token, verify_setup_token)
from .services import (extract_document, ics_for_reminder, interpret_prompt, list_cloud_files,
                       make_reminder, read_cloud_file, read_document, save_document,
                       send_due_emails, sync_document, today, valid_date)


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=12, max_length=200)


class SetupCredentials(Credentials):
    setup_token: str = Field(min_length=20, max_length=200)


class VaultConfig(BaseModel):
    salt: str = Field(min_length=8, max_length=200)
    check: str = Field(min_length=8, max_length=1000)


class VaultItem(BaseModel):
    ciphertext: str = Field(min_length=8, max_length=100000)


class ExpenseInput(BaseModel):
    amount: str
    currency: str = 'INR'
    category: str = Field(min_length=1, max_length=60)
    merchant: str = Field(default='', max_length=150)
    spent_on: str
    payment_method: str = Field(default='', max_length=60)
    note: str = Field(default='', max_length=1000)
    receipt_document_id: str | None = None


class BudgetInput(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    monthly_limit: str
    currency: str = 'INR'


class ReminderInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    event_date: str
    lead_days: int = Field(default=30, ge=0, le=3650)
    email_enabled: bool = True
    document_id: str | None = None


class DocumentReview(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    issue_date: str | None = None
    expiry_date: str | None = None
    remind_days_before: int | None = Field(default=30, ge=0, le=3650)


class PromptInput(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


def _worker(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            with connection() as conn:
                pending = conn.execute('''SELECT id FROM documents WHERE processing_status='pending'
                    ORDER BY created_at LIMIT 5''').fetchall()
            for row in pending:
                try:
                    extract_document(row['id'])
                except Exception:
                    with connection() as conn:
                        conn.execute("UPDATE documents SET processing_status='error' WHERE id=?", (row['id'],))
                try:
                    sync_document(row['id'])
                except Exception:
                    pass
            if any(REMOTES.values()):
                with connection() as conn:
                    rows = conn.execute("SELECT id,cloud_json FROM documents WHERE processing_status!='pending' ORDER BY created_at DESC LIMIT 50").fetchall()
                for row in rows:
                    cloud = json.loads(row['cloud_json'])
                    if any(remote and cloud.get(provider, {}).get('status') != 'synced'
                           for provider, remote in REMOTES.items()):
                        try:
                            sync_document(row['id'])
                        except Exception:
                            pass
            send_due_emails()
        except Exception:
            pass
        stop.wait(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize()
    setup_token()
    stop = threading.Event()
    thread = threading.Thread(target=_worker, args=(stop,), daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=2)


app = FastAPI(title='HomeHub', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
LOGIN_FAILURES = {}
LOGIN_LOCK = threading.Lock()


def record_login_attempt(address: str, success: bool) -> None:
    now = time.monotonic()
    with LOGIN_LOCK:
        if success:
            LOGIN_FAILURES.pop(address, None)
            return
        recent = [attempt for attempt in LOGIN_FAILURES.get(address, []) if now - attempt < 300]
        recent.append(now)
        LOGIN_FAILURES[address] = recent


def login_is_limited(address: str) -> bool:
    now = time.monotonic()
    with LOGIN_LOCK:
        return len([attempt for attempt in LOGIN_FAILURES.get(address, []) if now - attempt < 300]) >= 5


@app.middleware('http')
async def request_guard(request: Request, call_next):
    try:
        require_same_origin(request)
    except HTTPException as error:
        return JSONResponse({'detail': error.detail}, status_code=error.status_code)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if request.url.scheme == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    return response


@app.get('/api/status')
def status():
    with connection() as conn:
        setup = conn.execute('SELECT 1 FROM users WHERE id=1').fetchone() is not None
    return {'setup': setup, 'ollama_configured': bool(OLLAMA_MODEL),
            'cloud': {key: bool(value) for key, value in REMOTES.items()},
            'rclone_installed': shutil.which('rclone') is not None,
            'smtp_configured': bool(os.getenv('HOMEHUB_SMTP_HOST'))}


@app.post('/api/setup')
def setup(body: SetupCredentials, request: Request, response: Response):
    if not verify_setup_token(body.setup_token):
        raise HTTPException(403, 'Incorrect host setup code')
    with connection() as conn:
        if conn.execute('SELECT 1 FROM users WHERE id=1').fetchone():
            raise HTTPException(409, 'Account already set up')
        conn.execute('INSERT INTO users (id,email,password_hash) VALUES (1,?,?)',
                     (body.email.strip().lower(), PASSWORD_HASHER.hash(body.password)))
    token = issue_session()
    response.set_cookie('homehub_session', token, httponly=True, secure=request.url.scheme == 'https',
                        samesite='strict', max_age=604800)
    return {'ok': True}


@app.post('/api/login')
def login(body: Credentials, request: Request, response: Response):
    address = request.client.host if request.client else 'unknown'
    if login_is_limited(address):
        raise HTTPException(429, 'Too many sign-in attempts; try again in five minutes')
    with connection() as conn:
        user = conn.execute('SELECT email,password_hash FROM users WHERE id=1').fetchone()
    if not user or user['email'] != body.email.strip().lower():
        record_login_attempt(address, False)
        raise HTTPException(401, 'Incorrect email or password')
    try:
        PASSWORD_HASHER.verify(user['password_hash'], body.password)
    except VerifyMismatchError:
        record_login_attempt(address, False)
        raise HTTPException(401, 'Incorrect email or password')
    record_login_attempt(address, True)
    token = issue_session()
    response.set_cookie('homehub_session', token, httponly=True, secure=request.url.scheme == 'https',
                        samesite='strict', max_age=604800)
    return {'ok': True}


@app.post('/api/logout')
def logout(request: Request, response: Response, _=Depends(require_user)):
    revoke_session(request.cookies.get('homehub_session'))
    response.delete_cookie('homehub_session')
    return {'ok': True}


@app.get('/api/me')
def me(_=Depends(require_user)):
    with connection() as conn:
        user = conn.execute('SELECT email,vault_salt,vault_check FROM users WHERE id=1').fetchone()
    return {'email': user['email'], 'vault_salt': user['vault_salt'], 'vault_check': user['vault_check']}


@app.post('/api/vault/config')
def vault_config(body: VaultConfig, _=Depends(require_user)):
    with connection() as conn:
        current = conn.execute('SELECT vault_salt FROM users WHERE id=1').fetchone()
        if current['vault_salt']:
            raise HTTPException(409, 'Vault is already initialized')
        conn.execute('UPDATE users SET vault_salt=?,vault_check=? WHERE id=1', (body.salt, body.check))
    return {'ok': True}


@app.get('/api/vault/items')
def vault_items(_=Depends(require_user)):
    with connection() as conn:
        rows = conn.execute('SELECT * FROM vault_items ORDER BY updated_at DESC').fetchall()
    return [dict(row) for row in rows]


@app.post('/api/vault/items')
def add_vault_item(body: VaultItem, _=Depends(require_user)):
    item_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute('INSERT INTO vault_items VALUES (?,?,?)',
                     (item_id, body.ciphertext, datetime.now(timezone.utc).isoformat()))
    return {'id': item_id}


@app.put('/api/vault/items/{item_id}')
def update_vault_item(item_id: str, body: VaultItem, _=Depends(require_user)):
    with connection() as conn:
        changed = conn.execute('UPDATE vault_items SET ciphertext=?,updated_at=? WHERE id=?',
                               (body.ciphertext, datetime.now(timezone.utc).isoformat(), item_id)).rowcount
    if not changed:
        raise HTTPException(404, 'Vault item not found')
    return {'ok': True}


@app.delete('/api/vault/items/{item_id}')
def delete_vault_item(item_id: str, _=Depends(require_user)):
    with connection() as conn:
        conn.execute('DELETE FROM vault_items WHERE id=?', (item_id,))
    return {'ok': True}


@app.post('/api/documents')
async def upload_document(file: UploadFile = File(...), category: str = Form('General'),
                          _=Depends(require_user)):
    data = await file.read(20 * 1024 * 1024 + 1)
    try:
        document_id = save_document(file.filename or 'document', category,
                                    file.content_type or '', data)
    except ValueError as error:
        raise HTTPException(400, str(error))
    return {'id': document_id, 'processing_status': 'pending'}


@app.get('/api/documents')
def list_documents(_=Depends(require_user)):
    with connection() as conn:
        rows = conn.execute('SELECT * FROM documents ORDER BY created_at DESC').fetchall()
    return [{**dict(row), 'extracted': json.loads(row['extracted_json']),
             'cloud': json.loads(row['cloud_json'])} for row in rows]


@app.get('/api/cloud/{provider}/files')
def cloud_files(provider: str, _=Depends(require_user)):
    if provider not in REMOTES:
        raise HTTPException(404, 'Cloud provider not found')
    try:
        return list_cloud_files(provider)
    except (ValueError, OSError, RuntimeError) as error:
        raise HTTPException(503, str(error))


@app.get('/api/cloud/{provider}/file')
def cloud_file(provider: str, path: str, _=Depends(require_user)):
    if provider not in REMOTES:
        raise HTTPException(404, 'Cloud provider not found')
    try:
        data = read_cloud_file(provider, path)
    except FileNotFoundError:
        raise HTTPException(404, 'Cloud file not found')
    except (ValueError, OSError, RuntimeError) as error:
        raise HTTPException(400, str(error))
    mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    return Response(data, media_type=mime, headers={
        'Content-Disposition': 'inline; filename="cloud-file"'})


@app.get('/api/documents/{document_id}/file')
def document_file(document_id: str, _=Depends(require_user)):
    with connection() as conn:
        row = conn.execute('SELECT mime_type,name FROM documents WHERE id=?', (document_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Document not found')
    return Response(read_document(document_id), media_type=row['mime_type'], headers={
        'Content-Disposition': 'inline; filename="document"'})


@app.post('/api/documents/{document_id}/review')
def review_document(document_id: str, body: DocumentReview, _=Depends(require_user)):
    try:
        issue = valid_date(body.issue_date)
        expiry = valid_date(body.expiry_date)
    except ValueError:
        raise HTTPException(400, 'Use YYYY-MM-DD dates')
    with connection() as conn:
        row = conn.execute('SELECT name FROM documents WHERE id=?', (document_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Document not found')
        conn.execute('UPDATE documents SET category=?,issue_date=?,expiry_date=?,processing_status=? WHERE id=?',
                     (body.category, issue, expiry, 'confirmed', document_id))
        conn.execute('DELETE FROM reminders WHERE document_id=? AND sent_at IS NULL', (document_id,))
    reminder_id = None
    if expiry and body.remind_days_before is not None:
        reminder_id = make_reminder(f'Renew {row["name"]}', expiry,
                                    body.remind_days_before, document_id)
    return {'ok': True, 'reminder_id': reminder_id}


@app.post('/api/documents/{document_id}/retry')
def retry_document(document_id: str, _=Depends(require_user)):
    with connection() as conn:
        changed = conn.execute("UPDATE documents SET processing_status='pending' WHERE id=?",
                               (document_id,)).rowcount
    if not changed:
        raise HTTPException(404, 'Document not found')
    return {'ok': True}


@app.post('/api/expenses')
def add_expense(body: ExpenseInput, _=Depends(require_user)):
    try:
        amount_minor = int((Decimal(body.amount) * 100).quantize(Decimal('1')))
        spent_on = valid_date(body.spent_on)
        if not spent_on:
            raise ValueError('Date is required')
    except (InvalidOperation, ValueError):
        raise HTTPException(400, 'Use a valid amount and YYYY-MM-DD date')
    if amount_minor <= 0 or amount_minor > 100_000_000_000:
        raise HTTPException(400, 'Amount is out of range')
    if body.receipt_document_id:
        with connection() as conn:
            if not conn.execute('SELECT 1 FROM documents WHERE id=?', (body.receipt_document_id,)).fetchone():
                raise HTTPException(400, 'Receipt document not found')
    expense_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute('''INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?,?,?)''',
                     (expense_id, amount_minor, body.currency.upper()[:3], body.category,
                      body.merchant, spent_on, body.payment_method, body.note,
                      body.receipt_document_id, datetime.now(timezone.utc).isoformat()))
    return {'id': expense_id}


@app.get('/api/expenses')
def list_expenses(_=Depends(require_user)):
    with connection() as conn:
        rows = conn.execute('SELECT * FROM expenses ORDER BY spent_on DESC, created_at DESC LIMIT 500').fetchall()
    return [dict(row) for row in rows]


@app.get('/api/expenses/export.csv')
def export_expenses(_=Depends(require_user)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['date', 'merchant', 'category', 'amount', 'currency', 'payment_method', 'note', 'receipt_document_id'])
    with connection() as conn:
        rows = conn.execute('SELECT * FROM expenses ORDER BY spent_on DESC').fetchall()
    for row in rows:
        writer.writerow([row['spent_on'], row['merchant'], row['category'],
                         f'{Decimal(row["amount_minor"]) / 100:.2f}', row['currency'],
                         row['payment_method'], row['note'], row['receipt_document_id'] or ''])
    return Response(output.getvalue(), media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': 'attachment; filename="homehub-expenses.csv"'})


@app.get('/api/budgets')
def list_budgets(_=Depends(require_user)):
    with connection() as conn:
        rows = conn.execute('SELECT * FROM budgets ORDER BY category').fetchall()
    return [dict(row) for row in rows]


@app.post('/api/budgets')
def save_budget(body: BudgetInput, _=Depends(require_user)):
    try:
        minor = int((Decimal(body.monthly_limit) * 100).quantize(Decimal('1')))
    except InvalidOperation:
        raise HTTPException(400, 'Enter a valid monthly limit')
    if minor <= 0 or minor > 100_000_000_000:
        raise HTTPException(400, 'Monthly limit is out of range')
    with connection() as conn:
        conn.execute('''INSERT INTO budgets VALUES (?,?,?) ON CONFLICT(category)
            DO UPDATE SET monthly_limit_minor=excluded.monthly_limit_minor,currency=excluded.currency''',
            (body.category, minor, body.currency.upper()[:3]))
    return {'ok': True}


@app.delete('/api/expenses/{expense_id}')
def delete_expense(expense_id: str, _=Depends(require_user)):
    with connection() as conn:
        conn.execute('DELETE FROM expenses WHERE id=?', (expense_id,))
    return {'ok': True}


@app.post('/api/reminders')
def add_reminder(body: ReminderInput, _=Depends(require_user)):
    try:
        reminder_id = make_reminder(body.title, body.event_date, body.lead_days,
                                    body.document_id, body.email_enabled)
    except ValueError as error:
        raise HTTPException(400, str(error))
    return {'id': reminder_id}


@app.get('/api/reminders')
def list_reminders(_=Depends(require_user)):
    with connection() as conn:
        rows = conn.execute('SELECT * FROM reminders ORDER BY event_date LIMIT 500').fetchall()
    return [dict(row) for row in rows]


@app.delete('/api/reminders/{reminder_id}')
def delete_reminder(reminder_id: str, _=Depends(require_user)):
    with connection() as conn:
        conn.execute('DELETE FROM reminders WHERE id=?', (reminder_id,))
    return {'ok': True}


@app.get('/api/reminders/{reminder_id}/calendar.ics')
def reminder_ics(reminder_id: str, _=Depends(require_user)):
    with connection() as conn:
        row = conn.execute('SELECT * FROM reminders WHERE id=?', (reminder_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Reminder not found')
    return Response(ics_for_reminder(dict(row)), media_type='text/calendar', headers={
        'Content-Disposition': f'attachment; filename="reminder-{reminder_id}.ics"'})


@app.get('/api/reports')
def reports(_=Depends(require_user)):
    with connection() as conn:
        by_category = conn.execute('''SELECT category,currency,SUM(amount_minor) total_minor,COUNT(*) count
            FROM expenses GROUP BY category,currency ORDER BY total_minor DESC''').fetchall()
        by_month = conn.execute('''SELECT substr(spent_on,1,7) month,currency,
            SUM(amount_minor) total_minor FROM expenses GROUP BY month,currency ORDER BY month DESC LIMIT 24''').fetchall()
        by_merchant = conn.execute('''SELECT merchant,currency,SUM(amount_minor) total_minor
            FROM expenses WHERE merchant!='' GROUP BY merchant,currency ORDER BY total_minor DESC LIMIT 15''').fetchall()
        by_payment = conn.execute('''SELECT payment_method,currency,SUM(amount_minor) total_minor
            FROM expenses GROUP BY payment_method,currency ORDER BY total_minor DESC''').fetchall()
        budgets = conn.execute('''SELECT b.category,b.currency,b.monthly_limit_minor,
            COALESCE(SUM(e.amount_minor),0) spent_minor FROM budgets b LEFT JOIN expenses e
            ON e.category=b.category AND e.currency=b.currency AND substr(e.spent_on,1,7)=?
            GROUP BY b.category,b.currency,b.monthly_limit_minor ORDER BY b.category''',
            (today()[:7],)).fetchall()
        docs_due = conn.execute('''SELECT id,name,category,expiry_date FROM documents
            WHERE expiry_date IS NOT NULL ORDER BY expiry_date LIMIT 50''').fetchall()
        docs_missing_expiry = conn.execute('SELECT COUNT(*) count FROM documents WHERE expiry_date IS NULL').fetchone()['count']
        reminder_summary = conn.execute('''SELECT COUNT(*) total,
            SUM(CASE WHEN event_date < ? THEN 1 ELSE 0 END) overdue,
            SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) failed
            FROM reminders''', (today(),)).fetchone()
        vault_count = conn.execute('SELECT COUNT(*) count FROM vault_items').fetchone()['count']
    return {'expense_by_category': [dict(row) for row in by_category],
            'expense_by_month': [dict(row) for row in by_month],
            'expense_by_merchant': [dict(row) for row in by_merchant],
            'expense_by_payment': [dict(row) for row in by_payment],
            'budgets': [dict(row) for row in budgets],
            'documents_due': [dict(row) for row in docs_due],
            'documents_missing_expiry': docs_missing_expiry,
            'reminders': dict(reminder_summary), 'vault_count': vault_count}


@app.post('/api/prompt')
def prompt(body: PromptInput, _=Depends(require_user)):
    if any(word in body.text.lower() for word in ('password is', 'password:', 'passcode is')):
        raise HTTPException(400, 'Enter secrets only in the protected vault form')
    try:
        return interpret_prompt(body.text)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.get('/api/settings')
def settings(_=Depends(require_user)):
    ai_online = False
    try:
        ai_online = httpx.get(f'{OLLAMA_URL}/api/tags', timeout=2).status_code == 200
    except httpx.HTTPError:
        pass
    return {'ollama_model': OLLAMA_MODEL, 'ollama_online': ai_online,
            'timezone': str(TIMEZONE), 'reminder_hour': REMINDER_HOUR,
            'rclone_installed': bool(shutil.which('rclone')),
            'cloud': {key: {'configured': bool(remote), 'remote': remote} for key, remote in REMOTES.items()},
            'smtp_configured': bool(os.getenv('HOMEHUB_SMTP_HOST'))}


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
WEB_ROOT = DIST if DIST.exists() else ROOT
if DIST.exists() and (DIST / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')
if (WEB_ROOT / 'src').exists():
    app.mount('/src', StaticFiles(directory=WEB_ROOT / 'src'), name='src')


@app.get('/{path:path}')
def web_app(path: str):
    if path.startswith('api/'):
        raise HTTPException(404, 'Not found')
    if path == 'manifest.webmanifest':
        return FileResponse((DIST / path) if DIST.exists() else (ROOT / 'public' / path))
    if path == 'sw.js':
        return FileResponse((DIST / path) if DIST.exists() else (ROOT / 'public' / path))
    if path in {'icon-192.png', 'icon-512.png'}:
        return FileResponse((DIST / path) if DIST.exists() else (ROOT / 'public' / path))
    if '.' in Path(path).name and path not in {'', 'index.html'}:
        raise HTTPException(404, 'Not found')
    return FileResponse(WEB_ROOT / 'index.html')
