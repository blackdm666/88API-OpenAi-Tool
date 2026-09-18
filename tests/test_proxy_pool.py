import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from token_manager.config import default_config
from token_manager.store import TokenStore
from token_manager.sub2api_policy import assigned_proxy, proxy_candidates
from token_manager.integrations import sub2api_upload_payload, upload_to_sub2api
from token_manager.services import upload_record
from test_sub2api import response


class ProxyPoolTest(unittest.TestCase):
    def setUp(self):
        self.settings = default_config()
        self.cfg = self.settings["integrations"]["sub2api"]
        self.cfg.update(api_url="https://example.test", proxy_id="8,9,10")
        self.record = {
            "email": "owner@example.test",
            "access_token": "access",
            "refresh_token": "refresh",
        }

    def test_legacy_and_validation(self):
        for raw, expected in [
            (8, [8]),
            ("8", [8]),
            (None, []),
            ("", []),
            ("0", []),
            ("8,9,8", [8, 9]),
            ([8, 9], [8, 9]),
            ("8，9", [8, 9]),
        ]:
            self.assertEqual(proxy_candidates({"proxy_id": raw}), expected)
        for raw in ["8,0", "0,8", "8,", "8,a", "-1", "1.2", True]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                proxy_candidates({"proxy_id": raw})

    def test_each_account_draws_once_and_retries_keep_assignment(self):
        with patch("token_manager.sub2api_policy.SystemRandom") as generator:
            generator.return_value.choice.side_effect = [9, 10]
            first = sub2api_upload_payload(self.record, self.settings)
            retry = sub2api_upload_payload(self.record, self.settings)
            second = sub2api_upload_payload(
                {"email": "second@example.test"}, self.settings
            )
            self.assertEqual(
                [first["proxy_id"], retry["proxy_id"], second["proxy_id"]], [9, 9, 10]
            )
            self.assertEqual(generator.return_value.choice.call_count, 2)
            self.assertIsInstance(first["proxy_id"], int)
            self.assertNotIn("proxy_ids", first)

    def test_existing_proxy_in_pool_wins_and_removed_proxy_reassigned(self):
        with patch("token_manager.sub2api_policy.SystemRandom") as generator:
            self.assertEqual(assigned_proxy(self.record, self.cfg, 8), 8)
            generator.assert_not_called()
            self.cfg["proxy_id"] = "9,10"
            generator.return_value.choice.return_value = 10
            self.assertEqual(assigned_proxy(self.record, self.cfg, 8), 10)

    def test_direct_connection_clears_assignment(self):
        assigned_proxy(self.record, self.cfg, 8)
        self.cfg["proxy_id"] = ""
        self.assertIsNone(
            sub2api_upload_payload(self.record, self.settings)["proxy_id"]
        )

    def test_existing_upload_uses_remote_proxy(self):
        remote = {
            "id": 42,
            "platform": "openai",
            "type": "oauth",
            "name": self.record["email"],
            "proxy_id": 9,
            "status": "active",
        }
        with (
            patch(
                "token_manager.integrations.fetch_sub2api_accounts",
                return_value=[remote],
            ),
            patch(
                "token_manager.integrations.apply_sub2api_credentials",
                return_value=remote,
            ),
            patch(
                "token_manager.integrations._sub2api_request", return_value=response({})
            ) as request,
            patch("token_manager.sub2api_policy.SystemRandom") as generator,
        ):
            self.assertTrue(upload_to_sub2api(self.record, self.settings)[0])
            self.assertEqual(request.call_args.kwargs["json"]["proxy_id"], 9)
            generator.assert_not_called()

    def test_export_and_local_record_match_created_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.settings.update(
                tokens_dir=tmp + "/tokens", outputs_dir=tmp + "/outputs"
            )
            store = TokenStore(self.settings)
            store.save_record(self.record)
            with (
                patch(
                    "token_manager.integrations.fetch_sub2api_accounts", return_value=[]
                ),
                patch(
                    "token_manager.integrations._sub2api_request",
                    return_value=response({}, 201),
                ) as request,
                patch("token_manager.sub2api_policy.SystemRandom") as generator,
            ):
                generator.return_value.choice.return_value = 9
                self.assertTrue(
                    upload_record(
                        store, store.load_all()[0], self.settings, target="sub2api"
                    )[0]
                )
                self.assertEqual(request.call_args.kwargs["json"]["proxy_id"], 9)
                generator.return_value.choice.assert_called_once()
            saved = store.load_all()[0]
            self.assertEqual(saved["sub2api_proxy_assignment"]["proxy_id"], 9)
            exported = json.loads(
                next(Path(tmp + "/outputs/Sub2API").glob("*.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(exported["proxy_id"], 9)


if __name__ == "__main__":
    unittest.main()
