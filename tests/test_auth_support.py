import base64
import unittest
from unittest.mock import Mock, patch

from tools.auth_support import (
    totp_code,
    fresh_totp,
    workspace_from_session,
    continuation_without_orgs,
    failure_advice,
)
from tools.auth_2fa_browser import _wait_for_target


class AuthSupportTest(unittest.TestCase):
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

    def test_challenge_advice_requires_official_login(self):
        self.assertIn("官方浏览器", failure_advice("启动后被 Cloudflare 拦截了"))


if __name__ == "__main__":
    unittest.main()
