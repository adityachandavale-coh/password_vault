import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEMP = tempfile.TemporaryDirectory()
os.environ.setdefault('HOMEHUB_DATA_DIR', TEMP.name)

from homehub.__main__ import main


class LauncherTest(unittest.TestCase):
    """`python -m homehub` must refuse to serve the LAN without certificates and
    must only drop to plain HTTP on the loopback address."""

    def setUp(self):
        self.certs = tempfile.TemporaryDirectory()
        self.addCleanup(self.certs.cleanup)
        self.cert_dir = Path(self.certs.name)
        self.cert_patch = patch('homehub.__main__.CERT_DIR', self.cert_dir)
        self.cert_patch.start()
        self.addCleanup(self.cert_patch.stop)
        self.run_patch = patch('homehub.__main__.uvicorn.run')
        self.uvicorn_run = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def write_certificates(self):
        (self.cert_dir / 'server.crt').write_text('test-only-certificate', encoding='utf-8')
        (self.cert_dir / 'server.key').write_text('test-only-key', encoding='utf-8')

    def call(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_missing_certificates_report_guidance_and_do_not_start_the_server(self):
        status, _, err = self.call(['--host', '192.168.1.10'])
        self.assertEqual(status, 1)
        self.uvicorn_run.assert_not_called()
        self.assertIn('server.crt', err)
        self.assertIn('server.key', err)
        self.assertIn(str(self.cert_dir), err)
        self.assertIn('scripts/make_cert.py', err)
        self.assertIn('--http', err)

    def test_a_single_missing_certificate_file_is_also_refused(self):
        (self.cert_dir / 'server.crt').write_text('test-only-certificate', encoding='utf-8')
        status, _, err = self.call([])
        self.assertEqual(status, 1)
        self.uvicorn_run.assert_not_called()
        self.assertIn('server.key', err)
        self.assertNotIn('server.crt', err)

    def test_http_outside_loopback_is_an_argparse_error(self):
        self.write_certificates()
        for host in ('192.168.1.10', '0.0.0.0', '::'):
            with self.subTest(host=host):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as raised:
                        main(['--http', '--host', host])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn('HTTPS', err.getvalue())
                self.uvicorn_run.assert_not_called()

    def test_http_on_loopback_serves_without_tls(self):
        for host in ('127.0.0.1', 'localhost', '::1'):
            with self.subTest(host=host):
                self.uvicorn_run.reset_mock()
                status, out, _ = self.call(['--http', '--host', host, '--port', '8000'])
                self.assertEqual(status, 0)
                options = self.uvicorn_run.call_args.kwargs
                self.assertNotIn('ssl_certfile', options)
                self.assertNotIn('ssl_keyfile', options)
                self.assertEqual(options['host'], host)
                self.assertEqual(options['port'], 8000)
                self.assertIn(f'http://{host}:8000', out)

    def test_http_does_not_require_certificates(self):
        status, _, _ = self.call(['--http', '--host', '127.0.0.1'])
        self.assertEqual(status, 0)
        self.uvicorn_run.assert_called_once()

    def test_https_passes_both_certificate_paths_to_uvicorn(self):
        self.write_certificates()
        status, out, _ = self.call(['--host', '192.168.1.10', '--port', '8443'])
        self.assertEqual(status, 0)
        self.uvicorn_run.assert_called_once()
        target, options = self.uvicorn_run.call_args.args, self.uvicorn_run.call_args.kwargs
        self.assertEqual(target, ('homehub.main:app',))
        self.assertEqual(options['ssl_certfile'], str(self.cert_dir / 'server.crt'))
        self.assertEqual(options['ssl_keyfile'], str(self.cert_dir / 'server.key'))
        self.assertEqual(options['host'], '192.168.1.10')
        self.assertEqual(options['port'], 8443)
        self.assertIn('https://192.168.1.10:8443', out)

    def test_wildcard_bind_is_reported_as_loopback(self):
        self.write_certificates()
        status, out, _ = self.call(['--host', '0.0.0.0', '--port', '8443'])
        self.assertEqual(status, 0)
        self.assertEqual(self.uvicorn_run.call_args.kwargs['host'], '0.0.0.0')
        self.assertIn('https://127.0.0.1:8443', out)

    def test_reload_watches_the_package_directory(self):
        self.write_certificates()
        status, _, _ = self.call(['--host', '127.0.0.1', '--reload'])
        self.assertEqual(status, 0)
        options = self.uvicorn_run.call_args.kwargs
        self.assertTrue(options['reload'])
        self.assertEqual([Path(entry).name for entry in options['reload_dirs']], ['homehub'])


if __name__ == '__main__':
    unittest.main()
