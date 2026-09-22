import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from token_manager.updater import (
    UpdateError,
    UpdateInfo,
    check_for_update,
    download_update_package,
    parse_manifest,
    version_key,
)


class UpdaterTest(unittest.TestCase):
    def test_version_order_handles_release_suffix(self):
        self.assertGreater(
            version_key("2.2.0-88api.57"),
            version_key("2.2.0-88api.56"),
        )
        self.assertGreater(version_key("v3.0.0"), version_key("2.99.99"))

    def test_manifest_requires_https_and_sha256(self):
        with self.assertRaises(UpdateError):
            parse_manifest(
                {
                    "version": "2.2.0-88api.58",
                    "download_url": "http://example.test/app.exe",
                    "sha256": "0" * 64,
                }
            )
        with self.assertRaises(UpdateError):
            parse_manifest(
                {
                    "version": "2.2.0-88api.58",
                    "download_url": "https://example.test/app.exe",
                    "sha256": "bad",
                }
            )

    def test_check_for_update_returns_only_newer_release(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "version": "2.2.0-88api.58",
            "download_url": "https://example.test/app.exe",
            "sha256": "a" * 64,
            "size": 3,
        }
        with patch("token_manager.updater.requests.get", return_value=response):
            info = check_for_update(
                "https://example.test/manifest.json",
                current_version="2.2.0-88api.57",
            )
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "2.2.0-88api.58")

    def test_download_verifies_size_and_sha256(self):
        payload = b"new executable bytes"
        info = UpdateInfo(
            version="2.2.0-88api.58",
            download_url="https://example.test/app.exe",
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
        )
        response = Mock()
        response.raise_for_status = Mock()
        response.headers = {"Content-Length": str(len(payload))}
        response.iter_content.return_value = [payload]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        with tempfile.TemporaryDirectory() as folder:
            with patch("token_manager.updater.requests.get", return_value=response):
                path = download_update_package(info, destination_dir=folder)
            self.assertTrue(Path(path).is_file())
            self.assertEqual(Path(path).read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
