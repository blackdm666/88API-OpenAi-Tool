import base64
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.auth_support import (
    totp_code,
    fresh_totp,
    workspace_from_session,
    continuation_without_orgs,
    failure_advice,
)
from tools.auth_2fa_browser import _cleanup_browser_profile, _wait_for_target
from tools.auth_2fa_browser import _build_browser_command
from token_manager.gui_auth import GUIAuthMixin
from token_manager.oauth import OAuthCallbackServer
from token_manager.auth_proxy import (
    authorization_proxy_pool,
    browser_proxy_url,
)
from token_manager.utils import build_requests_proxies


class AuthSupportTest(unittest.TestCase):
    def test_authorization_proxy_pool_supports_multiple_http_and_socks_endpoints(self):
        settings = {
            "auth_proxy": (
                "http://proxy-a.test:8080\n"
                "socks5://proxy-b.test:1080; socks5h://proxy-c.test:1081"
            )
        }
        self.assertEqual(
            authorization_proxy_pool(settings),
            [
                "http://proxy-a.test:8080",
                "socks5://proxy-b.test:1080",
                "socks5h://proxy-c.test:1081",
            ],
        )
        self.assertEqual(
            build_requests_proxies("socks5h://proxy-c.test:1081"),
            {
                "http": "socks5h://proxy-c.test:1081",
                "https": "socks5h://proxy-c.test:1081",
            },
        )

    def test_browser_proxy_maps_socks5h_to_chromium_supported_scheme(self):
        self.assertEqual(
            browser_proxy_url("socks5h://user:pass@proxy.test:1080"),
            "socks5://user:pass@proxy.test:1080",
        )
        command = _build_browser_command(
            browser_path="chrome.exe",
            debug_port=9333,
            profile_dir=Path("profile"),
            start_url="https://auth.openai.com/",
            proxy_url="socks5h://proxy.test:1080",
        )
        self.assertIn("--proxy-server=socks5://proxy.test:1080", command)

    def test_authorization_proxy_rejects_unknown_scheme(self):
        with self.assertRaisesRegex(ValueError, "仅支持"):
            authorization_proxy_pool({"auth_proxy": "ftp://proxy.test:21"})

    def test_auto_auth_button_stops_running_job(self):
        class Harness(GUIAuthMixin):
            def __init__(self):
                self.running = False
                self.auto_refresh_running = False
                self.auto_auth_running = False
                self.auto_auth_stop = threading.Event()
                self.auto_auth_button = Mock()
                self.log = Mock()

            def is_running(self):
                return self.running

            def current_settings(self):
                return {
                    "auto_auth_timeout_seconds": 300,
                    "open_browser_on_auto_auth": False,
                    "auth_proxy": "socks5://authorization.test:1080",
                }

            def run_background(self, _status, worker, done):
                self.running = True
                self.worker = worker
                self.done = done

            def set_running(self, running, _status=""):
                self.running = running

        app = Harness()
        with patch(
            "token_manager.gui_auth.browser_assisted_authorize",
            return_value={"cancelled": True},
        ) as authorize:
            app.start_auto_auth()
            app.worker()
        self.assertEqual(
            authorize.call_args.kwargs["proxy_url"],
            "socks5://authorization.test:1080",
        )
        self.assertTrue(app.auto_auth_running)
        self.assertFalse(app.auto_auth_stop.is_set())
        app.start_auto_auth()
        self.assertTrue(app.auto_auth_stop.is_set())
        app.auto_auth_button.config.assert_called_with(
            text="正在停止自动授权", state="disabled"
        )
        with patch("token_manager.gui_auth.messagebox.showerror") as show_error:
            app.done({"cancelled": True})
        self.assertFalse(app.auto_auth_running)
        self.assertFalse(app.running)
        show_error.assert_not_called()

    def test_smart_authorization_always_saves_and_reloads_successful_credentials(self):
        class Harness(GUIAuthMixin):
            def __init__(self):
                self.auth2fa_input = Mock()
                self.auth2fa_input.get.return_value = (
                    "a@example.test----password----JBSWY3DPEHPK3PXP"
                )
                self.store = Mock()
                self.store.load_all.return_value = [{"email": "a@example.test"}]
                self.auto_refresh_running = False
                self.auth2fa_mode_var = Mock()
                self.auth2fa_mode_var.get.return_value = "协议链"
                self.root = Mock()
                self.status_var = Mock()
                self.auth2fa_stats_var = Mock()
                self.auth2fa_output_var = Mock()
                self.auth2fa_result_button = Mock()
                self.auth2fa_save_token_var = Mock()
                self.log = Mock()
                self.set_running = Mock()
                self.reload_tokens = Mock()
                self.show_auth2fa_results = Mock()
                self.save_auth2fa_credentials = Mock(return_value=True)
                self.save_settings = Mock()
                self.run_background = self._capture

            def current_settings(self):
                return {
                    "auth_2fa_mode": "protocol",
                    "auth_2fa_live_workers": 1,
                    "auth_2fa_live_save_token": False,
                    "outputs_dir": "",
                    "browser_executable_path": "",
                    "browser_auth_start_port": 9333,
                    "auto_auth_timeout_seconds": 300,
                    "http_proxy": "",
                    "auth_proxy": "",
                    "integrations": {"sub2api": {"api_url": "https://sub.test"}},
                }

            def is_running(self):
                return False

            def _capture(self, _status, worker, done):
                self.worker = worker
                self.done = done

        app = Harness()
        with (
            patch("token_manager.gui_auth.CredentialVault") as vault,
            patch(
                "token_manager.gui_auth.run_checked_authorization",
                return_value={
                    "success_count": 1,
                    "fail_count": 0,
                    "skipped_count": 0,
                    "input_error_count": 0,
                    "results": [{"ok": True, "email": "a@example.test"}],
                },
            ) as checked,
        ):
            vault.return_value.load.return_value = {}
            app.start_auth2fa_batch()
            app.worker()
            options = checked.call_args.args[4]
            self.assertTrue(options["save_token"])
            app.done(
                {
                    "success_count": 1,
                    "fail_count": 0,
                    "skipped_count": 0,
                    "input_error_count": 0,
                    "results": [{"ok": True, "email": "a@example.test"}],
                }
            )

        app.reload_tokens.assert_called_once_with(save_first=False)

    def test_oauth_callback_wait_can_be_cancelled(self):
        server = OAuthCallbackServer('http://127.0.0.1:1455/auth/callback')
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(RuntimeError, '自动授权已停止'):
            server.wait(30, cancelled=cancelled)

    def test_rfc6238_vectors(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        for stamp, expected in [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ]:
            self.assertEqual(totp_code(secret, timestamp=stamp, digits=8), expected)

    def test_near_expiry_waits_for_new_window(self):
        with (
            patch("tools.auth_support.time.time", side_effect=[58, 60.2]),
            patch("tools.auth_support.time.sleep") as sleep,
        ):
            self.assertEqual(
                fresh_totp("JBSWY3DPEHPK3PXP"),
                totp_code("JBSWY3DPEHPK3PXP", timestamp=60.2),
            )
            sleep.assert_called_once_with(2.2)

    def test_mfa_payload_works_without_legacy_cookie(self):
        payload = {
            "oai-client-auth-session": {"workspaces": [{"id": "verified-workspace"}]}
        }
        self.assertEqual(workspace_from_session(payload), "verified-workspace")
        self.assertEqual(
            workspace_from_session({}, {"workspaces": [{"id": "cookie-workspace"}]}),
            "cookie-workspace",
        )
        with self.assertRaises(RuntimeError):
            workspace_from_session({})

    def test_no_orgs_direct_continuation(self):
        self.assertEqual(
            continuation_without_orgs({"continue_url": "/oauth2/auth"}), "/oauth2/auth"
        )
        self.assertEqual(
            continuation_without_orgs({"data": {"orgs": [{"id": "org"}]}}), ""
        )
        with self.assertRaises(RuntimeError):
            continuation_without_orgs({})

    def test_exited_browser_fails_without_long_poll(self):
        process = Mock(returncode=1)
        process.poll.return_value = 1
        with patch("tools.auth_2fa_browser.load_targets", return_value=[]) as targets:
            with self.assertRaisesRegex(RuntimeError, "浏览器进程已退出"):
                _wait_for_target(9333, 35, process)
            targets.assert_called_once()

    def test_browser_profile_cleanup_removes_only_one_child_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "profiles"
            profile = root / "account_abc"
            sibling = root / "account_other"
            (profile / "Cache").mkdir(parents=True)
            (profile / "Cache" / "data.bin").write_bytes(b"cache")
            sibling.mkdir(parents=True)
            self.assertTrue(_cleanup_browser_profile(profile, root))
            self.assertFalse(profile.exists())
            self.assertTrue(sibling.exists())
            self.assertFalse(_cleanup_browser_profile(profile, root))
            with self.assertRaises(ValueError):
                _cleanup_browser_profile(root, root)

    def test_challenge_advice_requires_official_login(self):
        self.assertIn("官方浏览器", failure_advice("启动后被 Cloudflare 拦截了"))


if __name__ == "__main__":
    unittest.main()
