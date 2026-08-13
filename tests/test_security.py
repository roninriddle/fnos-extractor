import io
import json
import tarfile
import tempfile
import unittest
import zipfile
import os
from pathlib import Path

import app as app_module


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        app_module.ALLOWED_ROOT = self.root
        app_module.DEFAULT_MOUNT_PATH = str(self.root)
        app_module.PASSWORD_DICT_FILE = self.root / 'data' / 'passwords.enc'
        app_module.PASSWORD_CACHE_FILE = self.root / 'data' / 'password_cache.enc'
        app_module.PASSWORD_ENCRYPTION_KEY_FILE = self.root / 'data' / 'passwords.key'
        app_module.PASSWORD_DICT = ['secret-one']
        app_module.PASSWORD_SUCCESS_CACHE = {'archive.zip': 'secret-two'}
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_api_is_available_without_token(self):
        response = self.client.get('/api/config')
        self.assertEqual(response.status_code, 200)

    def test_scan_rejects_path_outside_mount_root(self):
        response = self.client.post(
            '/api/scan',
            json={'path': '/', 'include_subdirs': False, 'scan_mode': 'all'},
        )
        self.assertEqual(response.status_code, 403)

    def test_symlink_escape_is_rejected(self):
        outside = Path(self.temp_dir.name).parent
        link = self.root / 'outside-link'
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest('当前平台不支持符号链接')
        with self.assertRaises(app_module.APIError):
            app_module._resolve_allowed_path(link, must_exist=True, kind='dir')

    def test_password_endpoints_never_return_plaintext(self):
        response = self.client.get('/api/passwords')
        payload = response.get_json()
        self.assertEqual(payload, {'passwords': [], 'count': 1})
        self.assertNotIn('secret-one', response.get_data(as_text=True))

        response = self.client.get('/api/password-cache')
        payload = response.get_json()
        self.assertEqual(payload, {'cache': {}, 'count': 1})
        self.assertNotIn('secret-two', response.get_data(as_text=True))

    def test_password_files_are_encrypted(self):
        app_module._save_encrypted_json(app_module.PASSWORD_DICT_FILE, ['secret-one'])
        raw = app_module.PASSWORD_DICT_FILE.read_bytes()
        self.assertNotIn(b'secret-one', raw)
        self.assertEqual(app_module._load_encrypted_json(app_module.PASSWORD_DICT_FILE, []), ['secret-one'])

    def test_password_command_logging_is_redacted(self):
        rendered = app_module._redact_command(['unzip', '-P', 'secret-one', 'archive.zip'])
        self.assertNotIn('secret-one', rendered)
        rendered = app_module._redact_command(['7z', 'x', '-psecret-two', 'archive.7z'])
        self.assertNotIn('secret-two', rendered)

    def test_zip_slip_is_rejected(self):
        archive_path = self.root / 'evil.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('../escaped.txt', 'blocked')
        safe, message = app_module.validate_archive_entries(str(archive_path))
        self.assertFalse(safe)
        self.assertIn('不安全条目', message)

    def test_normal_zip_is_accepted(self):
        archive_path = self.root / 'safe.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('folder/file.txt', 'safe')
        safe, message = app_module.validate_archive_entries(str(archive_path))
        self.assertTrue(safe, message)

    def test_windows_absolute_zip_path_is_rejected(self):
        archive_path = self.root / 'windows-evil.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('C:\\Windows\\escaped.txt', 'blocked')
        safe, message = app_module.validate_archive_entries(str(archive_path))
        self.assertFalse(safe)
        self.assertIn('绝对路径', message)

    def test_tar_link_is_rejected(self):
        archive_path = self.root / 'evil.tar'
        with tarfile.open(archive_path, 'w') as archive:
            info = tarfile.TarInfo('link')
            info.type = tarfile.SYMTYPE
            info.linkname = '../../escaped.txt'
            archive.addfile(info)
        safe, message = app_module.validate_archive_entries(str(archive_path))
        self.assertFalse(safe)
        self.assertIn('链接', message)


if __name__ == '__main__':
    unittest.main()
