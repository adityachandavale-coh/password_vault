import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEMP = tempfile.TemporaryDirectory()
os.environ.setdefault('HOMEHUB_DATA_DIR', TEMP.name)

from homehub.config import PROJECT_ROOT, load_env, resolve_path


class ResolvePathTest(unittest.TestCase):
    """The service is started from shells, Task Scheduler, systemd, and Docker,
    each with its own working directory, so a relative path must land in the
    same place every time."""

    def test_relative_path_anchors_to_project_root_not_cwd(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as elsewhere:
            os.chdir(elsewhere)
            try:
                self.assertEqual(resolve_path('./data'), (PROJECT_ROOT / 'data').resolve())
                self.assertEqual(resolve_path('data'), (PROJECT_ROOT / 'data').resolve())
                self.assertEqual(resolve_path('var/homehub'), (PROJECT_ROOT / 'var' / 'homehub').resolve())
                self.assertNotEqual(resolve_path('./data').parent, Path(elsewhere).resolve())
            finally:
                os.chdir(original)

    def test_absolute_path_is_left_alone(self):
        with tempfile.TemporaryDirectory() as absolute:
            target = Path(absolute).resolve()
            self.assertEqual(resolve_path(str(target)), target)
            self.assertEqual(resolve_path(str(target / 'certs')), target / 'certs')

    def test_tilde_expands_to_the_home_directory(self):
        resolved = resolve_path('~/homehub-data')
        self.assertEqual(resolved, (Path.home() / 'homehub-data').resolve())
        self.assertNotIn('~', str(resolved))


class LoadEnvTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env_file = Path(self.temporary.name) / '.env'

    def test_matching_surrounding_quotes_are_stripped(self):
        self.env_file.write_text(
            'HOMEHUB_TEST_DOUBLE="quoted value"\n'
            "HOMEHUB_TEST_SINGLE='quoted value'\n"
            'HOMEHUB_TEST_BARE=bare value\n'
            'HOMEHUB_TEST_INNER=say "hello"\n'
            'HOMEHUB_TEST_MISMATCHED="unbalanced\n'
            "HOMEHUB_TEST_APOSTROPHE=it's fine\n",
            encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True):
            load_env(self.env_file)
            self.assertEqual(os.environ['HOMEHUB_TEST_DOUBLE'], 'quoted value')
            self.assertEqual(os.environ['HOMEHUB_TEST_SINGLE'], 'quoted value')
            self.assertEqual(os.environ['HOMEHUB_TEST_BARE'], 'bare value')
            # Only a matching pair wrapping the whole value is a quote.
            self.assertEqual(os.environ['HOMEHUB_TEST_INNER'], 'say "hello"')
            self.assertEqual(os.environ['HOMEHUB_TEST_MISMATCHED'], '"unbalanced')
            self.assertEqual(os.environ['HOMEHUB_TEST_APOSTROPHE'], "it's fine")

    def test_existing_environment_variables_win(self):
        self.env_file.write_text('HOMEHUB_TEST_PORT=8443\nHOMEHUB_TEST_HOST=from-file\n',
                                 encoding='utf-8')
        with patch.dict(os.environ, {'HOMEHUB_TEST_PORT': '9000'}, clear=True):
            load_env(self.env_file)
            self.assertEqual(os.environ['HOMEHUB_TEST_PORT'], '9000')
            self.assertEqual(os.environ['HOMEHUB_TEST_HOST'], 'from-file')

    def test_comments_blank_lines_and_malformed_entries_are_skipped(self):
        self.env_file.write_text('# a comment\n\n   \nHOMEHUB_TEST_NO_EQUALS\n'
                                 '  HOMEHUB_TEST_PADDED  =  padded  \n', encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True):
            load_env(self.env_file)
            self.assertNotIn('HOMEHUB_TEST_NO_EQUALS', os.environ)
            self.assertEqual(os.environ['HOMEHUB_TEST_PADDED'], 'padded')

    def test_missing_file_is_not_an_error(self):
        with patch.dict(os.environ, {}, clear=True):
            load_env(Path(self.temporary.name) / 'absent.env')

    def test_default_path_is_the_project_root_env_file(self):
        """Called with no argument, load_env() reads the project root's .env
        whatever the process working directory happens to be."""
        root = Path(self.temporary.name)
        (root / '.env').write_text('HOMEHUB_TEST_FROM_ROOT=yes\n', encoding='utf-8')
        decoy = root / 'cwd'
        decoy.mkdir()
        (decoy / '.env').write_text('HOMEHUB_TEST_FROM_CWD=yes\n', encoding='utf-8')
        original = Path.cwd()
        os.chdir(decoy)
        try:
            with patch.dict(os.environ, {}, clear=True), \
                 patch('homehub.config.PROJECT_ROOT', root):
                load_env()
                self.assertEqual(os.environ['HOMEHUB_TEST_FROM_ROOT'], 'yes')
                self.assertNotIn('HOMEHUB_TEST_FROM_CWD', os.environ)
        finally:
            os.chdir(original)


if __name__ == '__main__':
    unittest.main()
