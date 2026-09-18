import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch, Mock

from token_manager.config import default_config
from token_manager.store import TokenStore
from token_manager import integrations as api
from token_manager.integrations import fetch_sub2api_concurrency_snapshot
from token_manager.maintenance import recovery_cycle
from token_manager.services import set_sub2api_remote_records_schedulable
from token_manager.sub2api_policy import (
    normalize_server_url,
    upload_options,
    match_remote,
    auth_failure_kind,
)


def response(data, status=200):
    r = Mock(status_code=status, text="server error")
    r.json.return_value = {"code": 0, "data": data}
    return r


class Fixture:
    def setUp(self):
        self.settings = default_config()
        self.settings["integrations"]["sub2api"].update(
            api_url="sub.example.test", api_key="admin-test", auth_mode="api_key"
        )
        self.local = {
            "email": "owner@example.test",
            "account_id": "workspace",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
        }
        self.remote = {
            "id": 42,
            "platform": "openai",
            "type": "oauth",
            "name": self.local["email"],
            "status": "error",
            "schedulable": True,
            "error_message": "OAuth 401: expired",
            "credentials": {
                "email": self.local["email"],
                "chatgpt_account_id": "workspace",
                "refresh_token": "server-newer",
                "access_token": "old-access",
                "custom_setting": True,
            },
            "group_ids": [24],
            "concurrency": 19,
            "proxy_id": 8,
            "extra": {"codex_fingerprint_mode": "full"},
        }


class Sub2APITest(Fixture, unittest.TestCase):
    def test_concurrency_snapshot_uses_lite_list_fields(self):
        payload = {'items': [{'id': 333, 'status': 'active', 'schedulable': True, 'concurrency': 100, 'current_concurrency': 4, 'active_sessions': None}], 'total': 1, 'pages': 1}
        with patch('token_manager.integrations._sub2api_request', return_value=response(payload)) as request:
            rows = fetch_sub2api_concurrency_snapshot(self.settings)
        self.assertEqual(rows[0]['current_concurrency'], 4)
        self.assertEqual(request.call_args.kwargs['params']['lite'], '1')
    def test_schedulable_action_does_not_update_account_status(self):
        with patch('token_manager.services.set_sub2api_schedulable', side_effect=lambda settings, account_id, enabled, proxy_url='': {"id": account_id, "status": "active", "schedulable": enabled}) as setter:
            result = set_sub2api_remote_records_schedulable(
                [{"id": 333}, {"id": 342}], self.settings, enabled=True
            )
        self.assertEqual(result['success_count'], 2)
        self.assertEqual(setter.call_count, 2)
        self.assertTrue(all(call.args[2] is True for call in setter.call_args_list))
    def test_url_normalization_and_rejection(self):
        for raw in [
            " sub.example.test/ ",
            "https://sub.example.test/api/v1/auth/login",
            "https://sub.example.test/api/v1",
        ]:
            self.assertEqual(normalize_server_url(raw), "https://sub.example.test")
        for raw in [
            "",
            "https://user:password@sub.example.test",
            "file:///tmp",
            "https://sub.example.test?key=secret",
            "https://bad host",
        ]:
            with self.assertRaises(ValueError):
                normalize_server_url(raw)

    def test_upload_parameters_zero_and_fingerprint(self):
        cfg = self.settings["integrations"]["sub2api"]
        cfg.update(
            group_ids="2,24,2",
            concurrency="17",
            priority="0",
            rate_multiplier="0",
            proxy_id="8",
            codex_fingerprint_mode="session",
        )
        body = api.sub2api_upload_payload(self.local, self.settings)
        self.assertEqual(
            (
                body["group_ids"],
                body["concurrency"],
                body["priority"],
                body["rate_multiplier"],
                body["proxy_id"],
            ),
            ([2, 24], 17, 0, 0, 8),
        )
        self.assertEqual(
            body["extra"],
            {"email": self.local["email"], "codex_fingerprint_mode": "session", "openai_oauth_responses_websockets_v2_mode": "ctx_pool", "openai_oauth_responses_websockets_v2_enabled": True},
        )
        for key, value in [
            ("group_ids", "bad"),
            ("concurrency", "-1"),
            ("rate_multiplier", "nan"),
            ("codex_fingerprint_mode", "arbitrary"),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                upload_options({**cfg, key: value})

    def test_key_auth_never_tries_password_on_401(self):
        self.settings["integrations"]["sub2api"].update(
            admin_email="not-an-email",
            admin_password="irrelevant",
            access_token="stale",
        )
        with (
            patch.object(
                api.requests, "request", return_value=response({}, 401)
            ) as send,
            patch.object(api, "login_sub2api_admin") as login,
        ):
            api._sub2api_request(self.settings, "GET", "/api/v1/admin/accounts")
            self.assertEqual(send.call_count, 1)
            self.assertEqual(
                send.call_args.kwargs["headers"]["x-api-key"], "admin-test"
            )
            self.assertNotIn("Authorization", send.call_args.kwargs["headers"])
            self.assertTrue(send.call_args.kwargs["verify"])
            login.assert_not_called()

    def test_pagination_uses_total_when_pages_omitted(self):
        with patch.object(
            api,
            "_sub2api_request",
            side_effect=[
                response({"items": [self.remote], "total": 2}),
                response({"items": [{**self.remote, "id": 43}], "total": 2}),
            ],
        ) as send:
            rows = api.fetch_sub2api_accounts(self.settings, page_size=1)
            self.assertEqual([r["id"] for r in rows], [42, 43])
            self.assertEqual(send.call_args.kwargs["params"]["page"], 2)

    def test_identity_ambiguity_and_wrong_workspace(self):
        with self.assertRaises(ValueError):
            match_remote(self.local, [self.remote, {**self.remote, "id": 43}])
        wrong = deepcopy(self.remote)
        wrong["credentials"]["chatgpt_account_id"] = "other"
        with self.assertRaises(ValueError):
            match_remote(self.local, [wrong])
        self.assertIsNone(
            match_remote(self.local, [{**self.remote, "parent_account_id": 1}])
        )

    def test_429_and_temporary_401_are_not_permanent_errors(self):
        self.assertEqual(
            auth_failure_kind({**self.remote, "error_message": "429 rate limited"}), ""
        )
        self.assertEqual(
            auth_failure_kind(
                {**self.remote, "status": "active", "temp_unschedulable_reason": "401"}
            ),
            "",
        )
        self.assertEqual(
            auth_failure_kind({**self.remote, "error_message": "token_revoked (401)"}),
            "permanent",
        )

    def test_apply_preserves_credentials_and_does_not_send_operational_settings(self):
        fresh = {
            **self.local,
            "access_token": "new-access",
            "refresh_token": "new-refresh",
        }
        verified = deepcopy(self.remote)
        verified["status"] = "active"
        verified["credentials"]["access_token"] = "new-access"
        with (
            patch.object(
                api, "get_sub2api_account", side_effect=[self.remote, verified]
            ),
            patch.object(
                api, "_sub2api_request", return_value=response(verified)
            ) as send,
        ):
            api.apply_sub2api_credentials(fresh, self.remote, self.settings)
            payload = send.call_args.kwargs["json"]
            self.assertEqual(set(payload), {"type", "credentials"})
            self.assertTrue(payload["credentials"]["custom_setting"])
            self.assertEqual(payload["credentials"]["refresh_token"], "new-refresh")

    def test_concurrent_rotation_prevents_overwrite(self):
        changed = deepcopy(self.remote)
        changed["credentials"]["refresh_token"] = "rotated-again"
        with (
            patch.object(api, "get_sub2api_account", return_value=changed),
            patch.object(api, "_sub2api_request") as send,
        ):
            with self.assertRaises(ValueError):
                api.apply_sub2api_credentials(self.local, self.remote, self.settings)
            send.assert_not_called()

    def test_upload_updates_matching_record_instead_of_creating_duplicate(self):
        with (
            patch.object(api, "fetch_sub2api_accounts", return_value=[self.remote]),
            patch.object(
                api,
                "apply_sub2api_credentials",
                return_value={**self.remote, "extra": {"existing_policy": 42}},
            ) as apply,
            patch.object(api, "_sub2api_request", return_value=response({})) as send,
        ):
            ok, _ = api.upload_to_sub2api(self.local, self.settings)
            self.assertTrue(ok)
            apply.assert_called_once()
            self.assertEqual(
                send.call_args.args[1:3], ("PUT", "/api/v1/admin/accounts/42")
            )
            self.assertEqual(
                send.call_args.kwargs["json"]["extra"]["existing_policy"], 42
            )


class RecoveryTest(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings["integrations"]["sub2api"]["auto_reauthorize_401"] = False
        self.settings["integrations"]["sub2api"]["recovery_test_enabled"] = False
        self.local["expired"] = "2099-01-01T00:00:00Z"
        self.settings.update(
            tokens_dir=str(Path(self.temp.name) / "tokens"),
            outputs_dir=str(Path(self.temp.name) / "outputs"),
        )
        self.store = TokenStore(self.settings)
        self.local["sub2api_recovery"] = {"enabled": True}
        self.store.save_record(self.local)

    def refresh(self, store, record, settings, **kwargs):
        self.assertEqual(record["refresh_token"], "server-newer")
        record.update(access_token="new-access", refresh_token="new-refresh")
        store.save_record(record, filename=record.get("_filename"))
        return True, "ok"

    def test_recovery_and_upload_retry_do_not_rotate_twice(self):
        with (
            patch(
                "token_manager.maintenance.fetch_sub2api_accounts",
                return_value=[self.remote],
            ),
            patch(
                "token_manager.maintenance.refresh_record", side_effect=self.refresh
            ) as refresh,
            patch(
                "token_manager.maintenance.apply_sub2api_credentials",
                side_effect=[RuntimeError("network down"), {**self.remote, "status":"active", "credentials":{"access_token":"new-access"}}],
            ) as apply,
        ):
            recovery_cycle(self.store, self.settings)
            saved = self.store.load_all()[0]
            self.assertTrue(saved["sub2api_recovery"]["pending_upload"])
            self.assertEqual(saved["refresh_token"], "new-refresh")
            saved["sub2api_recovery"]["next_attempt_at"] = 0
            self.store.save_record(saved)
            result = recovery_cycle(self.store, self.settings)
            self.assertEqual(result["recovered"], 1)
            self.assertEqual(refresh.call_count, 1)
            self.assertEqual(apply.call_count, 2)

    def test_revoked_token_waits_for_new_authorization(self):
        revoked = {**self.remote, "error_message": "token_revoked (401)"}
        with (
            patch(
                "token_manager.maintenance.fetch_sub2api_accounts",
                return_value=[revoked],
            ),
            patch("token_manager.maintenance.refresh_record") as refresh,
            patch(
                "token_manager.maintenance.apply_sub2api_credentials", return_value={**self.remote, "status":"active", "credentials":{"access_token":"manual-new"}}
            ) as apply,
        ):
            recovery_cycle(self.store, self.settings)
            recovery_cycle(self.store, self.settings)
            refresh.assert_not_called()
            apply.assert_not_called()
            saved = self.store.load_all()[0]
            self.assertEqual(saved["sub2api_recovery"]["status"], "需要重新授权")
            saved.update(access_token="manual-new", refresh_token="manual-rt")
            self.store.save_record(saved)
            recovery_cycle(self.store, self.settings)
            apply.assert_called_once()
            refresh.assert_not_called()

    def test_unselected_and_429_never_refresh(self):
        for enabled, message in [(False, "401"), (True, "429 rate limited")]:
            self.local["sub2api_recovery"] = {"enabled": enabled}
            self.store.save_record(self.local)
            with (
                patch(
                    "token_manager.maintenance.fetch_sub2api_accounts",
                    return_value=[{**self.remote, "error_message": message}],
                ),
                patch("token_manager.maintenance.refresh_record") as refresh,
                patch("token_manager.maintenance.apply_sub2api_credentials") as apply,
            ):
                recovery_cycle(self.store, self.settings)
                refresh.assert_not_called()
                apply.assert_not_called()

    def test_wrong_server_and_duplicate_never_write_remote(self):
        local = self.store.load_all()[0]
        local["sub2api_recovery"]["server"] = "https://different.test"
        self.store.save_record(local)
        with (
            patch(
                "token_manager.maintenance.fetch_sub2api_accounts",
                return_value=[self.remote],
            ),
            patch("token_manager.maintenance.apply_sub2api_credentials") as apply,
        ):
            recovery_cycle(self.store, self.settings)
            apply.assert_not_called()
            self.assertIn(
                "服务器", self.store.load_all()[0]["sub2api_recovery"]["message"]
            )

    def test_new_manual_authorization_preserves_enrollment(self):
        self.store.save_token_response(
            {
                "email": self.local["email"],
                "access_token": "manual-new",
                "refresh_token": "manual-rt",
                "account_id": "workspace",
            }
        )
        saved = self.store.load_all()[0]
        self.assertTrue(saved["sub2api_recovery"]["enabled"])
        self.assertEqual(saved["refresh_token"], "manual-rt")

    def test_refresh_identity_mismatch_never_overwrites_local_record(self):
        from token_manager.services import refresh_record

        original = self.store.load_all()[0]
        with patch(
            "token_manager.services.refresh_oauth_token",
            return_value={"email": "other@example.test", "access_token": "other-token"},
        ):
            with self.assertRaises(ValueError):
                refresh_record(self.store, original, self.settings, sync_plan=False)
        self.assertEqual(self.store.load_all()[0]["access_token"], "old-access")

    def test_pending_upload_does_not_overwrite_later_remote_rotation(self):
        with (
            patch(
                "token_manager.maintenance.fetch_sub2api_accounts",
                return_value=[self.remote],
            ),
            patch("token_manager.maintenance.refresh_record", side_effect=self.refresh),
            patch(
                "token_manager.maintenance.apply_sub2api_credentials",
                side_effect=RuntimeError("network down"),
            ),
        ):
            recovery_cycle(self.store, self.settings)
        saved = self.store.load_all()[0]
        saved["sub2api_recovery"]["next_attempt_at"] = 0
        self.store.save_record(saved)
        changed = deepcopy(self.remote)
        changed["credentials"]["refresh_token"] = "independently-rotated"
        with (
            patch(
                "token_manager.maintenance.fetch_sub2api_accounts",
                return_value=[changed],
            ),
            patch("token_manager.maintenance.apply_sub2api_credentials") as apply,
        ):
            recovery_cycle(self.store, self.settings)
            apply.assert_not_called()
        self.assertEqual(
            self.store.load_all()[0]["sub2api_recovery"]["status"], "凭据冲突"
        )

    def test_stop_after_refresh_keeps_token_for_later_upload(self):
        stopped = False

        def refresh(*args, **kwargs):
            nonlocal stopped
            result = self.refresh(*args, **kwargs)
            stopped = True
            return result

        with (
            patch(
                "token_manager.maintenance.fetch_sub2api_accounts",
                return_value=[self.remote],
            ),
            patch("token_manager.maintenance.refresh_record", side_effect=refresh),
            patch("token_manager.maintenance.apply_sub2api_credentials") as apply,
        ):
            recovery_cycle(self.store, self.settings, cancelled=lambda: stopped)
            apply.assert_not_called()
        saved = self.store.load_all()[0]
        self.assertEqual(saved["refresh_token"], "new-refresh")
        self.assertTrue(saved["sub2api_recovery"]["pending_upload"])

    def test_attempt_limit_persists(self):
        with (
            patch(
                "token_manager.maintenance.fetch_sub2api_accounts",
                return_value=[self.remote],
            ),
            patch(
                "token_manager.maintenance.refresh_record",
                side_effect=RuntimeError("refresh unavailable"),
            ) as refresh,
        ):
            for _ in range(4):
                recovery_cycle(self.store, self.settings)
                saved = self.store.load_all()[0]
                saved["sub2api_recovery"]["next_attempt_at"] = 0
                self.store.save_record(saved)
            self.assertEqual(refresh.call_count, 3)
        self.assertEqual(
            self.store.load_all()[0]["sub2api_recovery"]["status"], "需要重新授权"
        )


if __name__ == "__main__":
    unittest.main()
