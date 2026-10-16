import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.backup import decrypt_file, encrypt_file, make_zip, restore


class BackupTest(unittest.TestCase):
    def test_encrypted_archive_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / 'data'
            remotes = root / 'rclone'
            (data / 'documents').mkdir(parents=True)
            remotes.mkdir()
            (data / 'host.key').write_bytes(b'host-key-test')
            (data / 'documents' / 'file.bin').write_bytes(b'encrypted-document-test')
            (remotes / 'rclone.conf').write_text('test-only', encoding='utf-8')
            plain = root / 'archive.zip'
            encrypted = root / 'archive.hhb'
            restored = root / 'restored.zip'
            make_zip(plain, data, remotes)
            encrypt_file(plain, encrypted, 'a-long-test-backup-password')
            self.assertNotIn(b'host-key-test', encrypted.read_bytes())
            decrypt_file(encrypted, restored, 'a-long-test-backup-password')
            self.assertEqual(plain.read_bytes(), restored.read_bytes())
            with patch('scripts.backup.getpass.getpass', return_value='a-long-test-backup-password'):
                restore(encrypted, root / 'new-data', root / 'new-rclone')
            self.assertEqual((root / 'new-data' / 'host.key').read_bytes(), b'host-key-test')
            self.assertEqual((root / 'new-rclone' / 'rclone.conf').read_text(), 'test-only')
            with self.assertRaises(ValueError):
                decrypt_file(encrypted, root / 'wrong.zip', 'incorrect-password')


if __name__ == '__main__':
    unittest.main()
