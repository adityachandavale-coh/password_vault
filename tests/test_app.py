import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEMP = tempfile.TemporaryDirectory()
os.environ['HOMEHUB_DATA_DIR'] = TEMP.name
os.environ.pop('HOMEHUB_OLLAMA_MODEL', None)

from fastapi.testclient import TestClient
from homehub.main import app, LOGIN_FAILURES
from homehub.config import DB_PATH
from homehub.security import setup_token
from homehub import services


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


if __name__ == '__main__':
    unittest.main()
