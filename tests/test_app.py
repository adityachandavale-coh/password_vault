import os
import smtplib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


TEMP = tempfile.TemporaryDirectory()
os.environ['HOMEHUB_DATA_DIR'] = TEMP.name
os.environ.pop('HOMEHUB_OLLAMA_MODEL', None)

from fastapi.testclient import TestClient
from homehub.main import app, LOGIN_FAILURES, _disposition
from homehub.config import DB_PATH
from homehub.security import setup_token
from homehub import db, services


class AppFlowTest(unittest.TestCase):
    def setUp(self):
        LOGIN_FAILURES.clear()
        self.worker_patch = patch('homehub.main._worker', lambda stop: None)
        self.worker_patch.start()
        for path in Path(TEMP.name).glob('*'):
            if path.is_file():
                path.unlink()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.client.headers.update({'X-HomeHub-Request': '1'})

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.worker_patch.stop()

    def test_setup_expense_document_reminder_and_reports(self):
        created = self.client.post('/api/setup', json={'email': 'owner@example.com',
            'password': 'a-long-account-password', 'setup_token': setup_token()})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.client.get('/api/me').json()['email'], 'owner@example.com')

        document = self.client.post('/api/documents', files={'file': ('bill.png', b'fake-image', 'image/png')},
            data={'category': 'Receipts'})
        self.assertEqual(document.status_code, 200, document.text)
        document_id = document.json()['id']
        self.assertEqual(self.client.get(f'/api/documents/{document_id}/file').content, b'fake-image')
        reviewed = self.client.post(f'/api/documents/{document_id}/review', json={
            'category': 'Vehicle', 'issue_date': '2026-01-01', 'expiry_date': '2027-01-01',
            'remind_days_before': 30})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertTrue(reviewed.json()['reminder_id'])

        expense = self.client.post('/api/expenses', json={'amount': '850.50', 'currency': 'INR',
            'category': 'Fuel', 'merchant': 'Petrol pump', 'spent_on': services.today(),
            'payment_method': 'UPI', 'note': '', 'receipt_document_id': document_id})
        self.assertEqual(expense.status_code, 200, expense.text)
        budget = self.client.post('/api/budgets', json={'category': 'Fuel',
            'monthly_limit': '2000', 'currency': 'INR'})
        self.assertEqual(budget.status_code, 200)
        report = self.client.get('/api/reports').json()
        self.assertEqual(report['expense_by_category'][0]['total_minor'], 85050)
        self.assertEqual(report['budgets'][0]['spent_minor'], 85050)
        self.assertEqual(report['reminders']['total'], 1)
        self.assertEqual(len(report['documents_due']), 1)
        exported = self.client.get('/api/expenses/export.csv')
        self.assertIn('850.50', exported.text)

    def test_authentication_and_origin_guard(self):
        self.assertEqual(self.client.get('/api/expenses').status_code, 401)
        self.assertEqual(self.client.post('/api/setup', headers={'X-HomeHub-Request': ''},
            json={'email': 'owner@example.com', 'password': 'a-long-account-password',
                  'setup_token': setup_token()}).status_code, 403)
        self.assertEqual(self.client.post('/api/setup', json={'email': 'owner@example.com',
            'password': 'a-long-account-password', 'setup_token': setup_token()}).status_code, 200)
        self.assertEqual(self.client.post('/api/login', json={'email': 'owner@example.com',
            'password': 'incorrect-password'}).status_code, 401)
        self.assertEqual(self.client.get('/.env').status_code, 404)
        self.assertEqual(self.client.get('/data/homehub.db').status_code, 404)
        self.assertEqual(self.client.get('/manifest.webmanifest').status_code, 200)
        self.assertEqual(self.client.get('/icon-192.png').status_code, 200)

    def test_prompt_and_both_cloud_destinations(self):
        draft = services.fallback_intent('Paid ₹850 for petrol yesterday by UPI')
        self.assertEqual(draft['kind'], 'expense')
        self.assertEqual(draft['category'], 'Fuel')
        self.assertEqual(draft['payment_method'], 'UPI')
        document_id = services.save_document('receipt.png', 'Receipts', 'image/png', b'fake-image')
        with patch.dict(services.REMOTES, {'google': 'google', 'onedrive': 'onedrive'}), \
             patch('homehub.services.subprocess.run') as command:
            command.return_value.returncode = 0
            result = services.sync_document(document_id)
        self.assertEqual(command.call_count, 2)
        self.assertEqual(result['google']['status'], 'synced')
        self.assertEqual(result['onedrive']['status'], 'synced')
        with self.assertRaises(ValueError):
            services.safe_cloud_path('../outside.txt')

    def test_login_rate_limit(self):
        self.client.post('/api/setup', json={'email': 'owner@example.com',
            'password': 'a-long-account-password', 'setup_token': setup_token()})
        for _ in range(5):
            response = self.client.post('/api/login', json={'email': 'owner@example.com',
                'password': 'incorrect-password'})
            self.assertEqual(response.status_code, 401)
        blocked = self.client.post('/api/login', json={'email': 'owner@example.com',
            'password': 'a-long-account-password'})
        self.assertEqual(blocked.status_code, 429)

    def test_due_reminder_email_worker(self):
        services.make_reminder('Renew PUC', services.today(), 0)
        settings = {'HOMEHUB_SMTP_HOST': 'smtp.example.invalid',
                    'HOMEHUB_SMTP_FROM': 'homehub@example.invalid',
                    'HOMEHUB_REMINDER_TO': 'owner@example.invalid'}
        with patch.dict(os.environ, settings), patch('homehub.services.REMINDER_HOUR', 0), \
             patch('homehub.services.smtplib.SMTP') as smtp:
            self.assertEqual(services.send_due_emails(), 1)
            smtp.return_value.__enter__.return_value.send_message.assert_called_once()
        self.client.post('/api/setup', json={'email': 'owner@example.com',
            'password': 'a-long-account-password', 'setup_token': setup_token()})
        self.assertIsNotNone(self.client.get('/api/reminders').json()[0]['sent_at'])

    def test_ocr_candidate_requires_confirmation(self):
        document_id = services.save_document('puc.png', 'Vehicle', 'image/png', b'fake-image')
        extracted = {'document_type': 'PUC', 'issue_date': '2026-01-01',
                     'expiry_date': '2027-01-01', 'merchant': '', 'total': None,
                     'evidence': 'Valid until 01/01/2027'}
        with patch('homehub.services.OLLAMA_MODEL', 'test-vision'), \
             patch('homehub.services._ollama', return_value=extracted):
            result = services.extract_document(document_id)
        self.assertEqual(result['expiry_date'], '2027-01-01')
        self.client.post('/api/setup', json={'email': 'owner@example.com',
            'password': 'a-long-account-password', 'setup_token': setup_token()})
        self.assertEqual(len(self.client.get('/api/reminders').json()), 0)
        self.assertEqual(self.client.get('/api/documents').json()[0]['processing_status'], 'review')

    def _set_last_attempt(self, reminder_id, seconds_ago):
        attempted = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
        with services.connection() as conn:
            conn.execute('UPDATE reminders SET last_attempt_at=?,last_error=? WHERE id=?',
                         (attempted, 'Connection refused', reminder_id))

    def _send_due_emails(self):
        """Run the reminder sweep with SMTP stubbed out, returning (sent, smtp)."""
        settings = {'HOMEHUB_SMTP_HOST': 'smtp.example.invalid',
                    'HOMEHUB_SMTP_FROM': 'homehub@example.invalid',
                    'HOMEHUB_REMINDER_TO': 'owner@example.invalid'}
        with patch.dict(os.environ, settings), patch('homehub.services.REMINDER_HOUR', 0), \
             patch('homehub.services.smtplib.SMTP') as smtp:
            return services.send_due_emails(), smtp

    def test_failed_reminder_waits_for_the_retry_window(self):
        reminder_id = services.make_reminder('Renew PUC', services.today(), 0)

        # A failure a minute ago is inside the backoff window: leave it alone.
        self._set_last_attempt(reminder_id, 60)
        sent, smtp = self._send_due_emails()
        self.assertEqual(sent, 0)
        smtp.assert_not_called()

        # Still inside it one second before the window closes.
        self._set_last_attempt(reminder_id, services.RETRY_BACKOFF_SECONDS - 1)
        sent, smtp = self._send_due_emails()
        self.assertEqual(sent, 0)
        smtp.assert_not_called()

        # Once the window has passed the reminder is retried and clears.
        self._set_last_attempt(reminder_id, services.RETRY_BACKOFF_SECONDS + 60)
        sent, smtp = self._send_due_emails()
        self.assertEqual(sent, 1)
        smtp.return_value.__enter__.return_value.send_message.assert_called_once()
        with services.connection() as conn:
            row = conn.execute('SELECT sent_at,last_error,last_attempt_at FROM reminders WHERE id=?',
                               (reminder_id,)).fetchone()
        self.assertIsNotNone(row['sent_at'])
        self.assertIsNotNone(row['last_attempt_at'])
        self.assertIsNone(row['last_error'])

    def test_a_send_failure_records_the_attempt_time(self):
        reminder_id = services.make_reminder('Renew PUC', services.today(), 0)
        settings = {'HOMEHUB_SMTP_HOST': 'smtp.example.invalid',
                    'HOMEHUB_SMTP_FROM': 'homehub@example.invalid',
                    'HOMEHUB_REMINDER_TO': 'owner@example.invalid'}
        with patch.dict(os.environ, settings), patch('homehub.services.REMINDER_HOUR', 0), \
             patch('homehub.services._send_message',
                   side_effect=smtplib.SMTPException('mailbox unavailable')):
            self.assertEqual(services.send_due_emails(), 0)
        with services.connection() as conn:
            row = conn.execute('SELECT sent_at,last_error,last_attempt_at FROM reminders WHERE id=?',
                               (reminder_id,)).fetchone()
        self.assertIsNone(row['sent_at'])
        self.assertIn('mailbox unavailable', row['last_error'])
        self.assertIsNotNone(row['last_attempt_at'])

        # The very next sweep must not hammer the same address again.
        sent, smtp = self._send_due_emails()
        self.assertEqual(sent, 0)
        smtp.assert_not_called()


class ServiceUnitTest(unittest.TestCase):
    """Behaviour that needs no database or HTTP client."""

    def test_port_465_uses_implicit_tls_without_starttls(self):
        message = services.EmailMessage()
        with patch.dict(os.environ, {'HOMEHUB_SMTP_PORT': '465'}), \
             patch('homehub.services.smtplib.SMTP_SSL') as secure, \
             patch('homehub.services.smtplib.SMTP') as plain:
            services._send_message('smtp.example.invalid', message)
        plain.assert_not_called()
        secure.assert_called_once_with('smtp.example.invalid', 465, timeout=20)
        session = secure.return_value.__enter__.return_value
        session.starttls.assert_not_called()
        session.send_message.assert_called_once_with(message)

    def test_port_587_upgrades_with_starttls(self):
        message = services.EmailMessage()
        with patch.dict(os.environ, {'HOMEHUB_SMTP_PORT': '587'}), \
             patch('homehub.services.smtplib.SMTP_SSL') as secure, \
             patch('homehub.services.smtplib.SMTP') as plain:
            services._send_message('smtp.example.invalid', message)
        secure.assert_not_called()
        plain.assert_called_once_with('smtp.example.invalid', 587, timeout=20)
        session = plain.return_value.__enter__.return_value
        session.starttls.assert_called_once()
        session.send_message.assert_called_once_with(message)

    def test_credentials_are_only_sent_when_a_user_is_configured(self):
        with patch.dict(os.environ, {'HOMEHUB_SMTP_PORT': '587', 'HOMEHUB_SMTP_USER': 'owner',
                                     'HOMEHUB_SMTP_PASSWORD': 'app-password'}), \
             patch('homehub.services.smtplib.SMTP') as plain:
            services._send_message('smtp.example.invalid', services.EmailMessage())
        plain.return_value.__enter__.return_value.login.assert_called_once_with('owner', 'app-password')
        with patch.dict(os.environ, {'HOMEHUB_SMTP_PORT': '587', 'HOMEHUB_SMTP_USER': ''}), \
             patch('homehub.services.smtplib.SMTP') as anonymous:
            services._send_message('smtp.example.invalid', services.EmailMessage())
        anonymous.return_value.__enter__.return_value.login.assert_not_called()

    def test_missing_rclone_binary_becomes_a_readable_error(self):
        with patch('homehub.services.subprocess.run', side_effect=FileNotFoundError(2, 'rclone')):
            with self.assertRaises(RuntimeError) as raised:
                services._rclone(['copyto', 'a', 'b'])
        self.assertIn('rclone', str(raised.exception))
        self.assertIn('PATH', str(raised.exception))

    def test_other_rclone_errors_are_not_swallowed(self):
        with patch('homehub.services.subprocess.run', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                services._rclone(['copyto', 'a', 'b'])

    def test_known_preview_types_render_inline(self):
        for mime in ('application/pdf', 'image/jpeg', 'image/png', 'image/webp',
                     'image/gif', 'text/plain'):
            with self.subTest(mime=mime):
                self.assertTrue(_disposition(mime, 'bill.pdf')['Content-Disposition']
                                .startswith('inline; '))
        # A charset parameter must not defeat the lookup.
        self.assertTrue(_disposition('text/plain; charset=utf-8', 'notes.txt')
                        ['Content-Disposition'].startswith('inline; '))

    def test_active_and_unknown_types_are_downloaded(self):
        for mime in ('text/html', 'image/svg+xml', 'application/octet-stream',
                     'application/javascript', 'text/xml', ''):
            with self.subTest(mime=mime):
                self.assertTrue(_disposition(mime, 'page.html')['Content-Disposition']
                                .startswith('attachment; '))

    def test_the_filename_is_reduced_to_header_safe_characters(self):
        cases = {
            'say "hello".pdf': 'say _hello_.pdf',
            'inject\r\nX-Evil: 1.pdf': 'inject__X-Evil_ 1.pdf',
            # Separators become underscores, then leading dots are stripped.
            '../../etc/passwd': '_.._etc_passwd',
            'C:\\Windows\\notes.txt': 'C__Windows_notes.txt',
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                header = _disposition('application/pdf', filename)['Content-Disposition']
                self.assertEqual(header, f'inline; filename="{expected}"')
                for forbidden in ('"', '\r', '\n', '/', '\\'):
                    self.assertNotIn(forbidden, header[len('inline; filename="'):-1])

    def test_an_unusable_filename_falls_back_and_is_truncated(self):
        self.assertEqual(_disposition('text/html', '???')['Content-Disposition'],
                         'attachment; filename="___"')
        self.assertEqual(_disposition('text/html', '')['Content-Disposition'],
                         'attachment; filename="file"')
        self.assertEqual(_disposition('text/html', '  ..  ')['Content-Disposition'],
                         'attachment; filename="file"')
        long_name = _disposition('application/pdf', 'a' * 300)['Content-Disposition']
        self.assertEqual(long_name, 'inline; filename="' + 'a' * 100 + '"')


class MigrationTest(unittest.TestCase):
    """CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so columns
    added after the first release need the explicit ADDED_COLUMNS step."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / 'homehub.db'

    def build_pre_migration_database(self):
        """Create the database as it looked before last_attempt_at existed."""
        schema = db.SCHEMA.replace('    last_attempt_at TEXT,\n', '')
        self.assertNotIn('last_attempt_at', schema)
        conn = sqlite3.connect(self.database)
        try:
            conn.executescript(schema)
            conn.commit()
        finally:
            conn.close()

    def columns(self, table='reminders'):
        conn = sqlite3.connect(self.database)
        try:
            return [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
        finally:
            conn.close()

    def test_last_attempt_at_is_added_to_an_existing_database(self):
        self.build_pre_migration_database()
        self.assertNotIn('last_attempt_at', self.columns())
        with patch('homehub.db.DB_PATH', self.database):
            db.initialize()
        self.assertIn('last_attempt_at', self.columns())

    def test_existing_reminder_rows_survive_the_migration(self):
        self.build_pre_migration_database()
        conn = sqlite3.connect(self.database)
        try:
            conn.execute('''INSERT INTO reminders (id,title,event_date,send_on,created_at)
                VALUES (?,?,?,?,?)''', ('old-1', 'Renew PUC', '2027-01-01', '2026-12-02',
                                        '2026-01-01T00:00:00+00:00'))
            conn.commit()
        finally:
            conn.close()
        with patch('homehub.db.DB_PATH', self.database):
            db.initialize()
        conn = sqlite3.connect(self.database)
        try:
            row = conn.execute('SELECT title,last_attempt_at FROM reminders WHERE id=?',
                               ('old-1',)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], 'Renew PUC')
        self.assertIsNone(row[1])

    def test_running_the_migration_twice_is_a_no_op(self):
        self.build_pre_migration_database()
        with patch('homehub.db.DB_PATH', self.database):
            db.initialize()
            first = self.columns()
            db.initialize()
        self.assertEqual(self.columns(), first)
        self.assertEqual(first.count('last_attempt_at'), 1)

    def test_a_fresh_database_already_has_the_column(self):
        with patch('homehub.db.DB_PATH', self.database):
            db.initialize()
        self.assertIn('last_attempt_at', self.columns())


if __name__ == '__main__':
    unittest.main()
