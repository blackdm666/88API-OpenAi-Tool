import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build


class DesktopInstallTest(unittest.TestCase):
    def test_new_build_replaces_only_versioned_desktop_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dist = root / "dist"
            desktop = root / "Desktop"
            dist.mkdir()
            desktop.mkdir()
            app_name = "OpenAI-Token-Manager-88API"
            (dist / f"{app_name}.exe").write_bytes(b"new-build")
            old_51 = desktop / "88API-号池自动维护工具-v2.2.0-88api.51.exe"
            old_52 = desktop / "88API-号池自动维护工具-v2.2.0-88api.52.exe"
            unrelated = desktop / "other-tool-v1.exe"
            old_51.write_bytes(b"old-51")
            old_52.write_bytes(b"old-52")
            unrelated.write_bytes(b"keep")

            with (
                patch.object(build, "DIST_DIR", dist),
                patch.object(build, "APP_VERSION", "2.2.0-88api.53"),
            ):
                target = build.install_desktop_release(
                    app_name,
                    desktop_dir=desktop,
                )

            self.assertEqual(
                target.name,
                "88API-号池自动维护工具-v2.2.0-88api.53.exe",
            )
            self.assertEqual(target.read_bytes(), b"new-build")
            self.assertFalse(old_51.exists())
            self.assertFalse(old_52.exists())
            self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_locked_old_release_requests_safe_close_then_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dist = root / "dist"
            desktop = root / "Desktop"
            dist.mkdir()
            desktop.mkdir()
            app_name = "OpenAI-Token-Manager-88API"
            (dist / f"{app_name}.exe").write_bytes(b"new-build")
            old = desktop / "88API-号池自动维护工具-v2.2.0-88api.52.exe"
            old.write_bytes(b"old")
            real_unlink = Path.unlink
            attempts = {"old": 0}

            def locked_once(path, *args, **kwargs):
                if path == old and attempts["old"] == 0:
                    attempts["old"] += 1
                    raise PermissionError("locked")
                return real_unlink(path, *args, **kwargs)

            with (
                patch.object(build, "DIST_DIR", dist),
                patch.object(build, "APP_VERSION", "2.2.0-88api.53"),
                patch.object(Path, "unlink", new=locked_once),
                patch.object(build, "close_running_release", return_value=True) as close,
            ):
                build.install_desktop_release(app_name, desktop_dir=desktop)

            close.assert_called_once_with(old)
            self.assertFalse(old.exists())
