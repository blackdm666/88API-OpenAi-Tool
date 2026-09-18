import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from token_manager.usage_display import quota_cell, snapshot_usage, usage_details
from token_manager.integrations import fetch_sub2api_accounts, fetch_sub2api_usage
from token_manager.config import default_config
from test_sub2api import response


class UsageTest(unittest.TestCase):
    def test_zero_missing_error_and_expired_are_distinct(self):
        now = datetime(2026, 9, 18, tzinfo=timezone.utc)
        self.assertEqual(quota_cell({}, "five_hour", now=now), "暂无")
        self.assertEqual(
            quota_cell({"five_hour": {"utilization": 0}}, "five_hour", now=now), "0.0%"
        )
        self.assertEqual(
            quota_cell({"error": "timeout"}, "five_hour", now=now), "读取失败"
        )
        self.assertEqual(
            quota_cell(
                {"five_hour": {"utilization": 99, "resets_at": "2026-09-17T00:00:00Z"}},
                "five_hour",
                now=now,
            ),
            "待更新",
        )
        self.assertEqual(
            quota_cell({"five_hour": {"utilization": "nan"}}, "five_hour", now=now),
            "暂无",
        )

    def test_cached_snapshot_and_relative_reset(self):
        usage = snapshot_usage(
            {
                "extra": {
                    "codex_5h_used_percent": 25,
                    "codex_usage_updated_at": "2026-09-18T00:00:00Z",
                    "codex_5h_reset_after_seconds": 3600,
                }
            }
        )
        self.assertEqual(usage["five_hour"]["utilization"], 25)
        self.assertEqual(usage["five_hour"]["resets_at"], "2026-09-18T01:00:00+00:00")
        self.assertNotIn("seven_day", usage)

    def test_server_name_order_is_not_replaced_with_email_order(self):
        items = [
            {"id": 1, "name": "A", "extra": {"email": "z@example.test"}},
            {"id": 2, "name": "B", "extra": {"email": "a@example.test"}},
        ]
        with patch(
            "token_manager.integrations._sub2api_request",
            return_value=response({"items": items, "pages": 1}),
        ) as request:
            self.assertEqual(
                [r["id"] for r in fetch_sub2api_accounts(default_config())], [1, 2]
            )
            self.assertEqual(request.call_args.kwargs["params"]["sort_by"], "name")
            self.assertEqual(request.call_args.kwargs["params"]["sort_order"], "asc")

    def test_batch_usage_is_bounded_deduped_and_non_forced(self):
        with patch(
            "token_manager.integrations._sub2api_request",
            side_effect=[
                response({"usage": {"1": {"five_hour": {"utilization": 0}}}}),
                response({"errors": {"21": "timeout"}}),
            ],
        ) as request:
            result = fetch_sub2api_usage(default_config(), list(range(1, 22)) + [1])
            self.assertEqual(request.call_count, 2)
            self.assertEqual(
                request.call_args.kwargs["json"], {"account_ids": [21], "force": False}
            )
            self.assertIn("1", result["usage"])
            self.assertEqual(result["errors"]["21"], "timeout")
        with self.assertRaises(ValueError):
            fetch_sub2api_usage(default_config(), range(1, 52))

    def test_usage_details_include_actual_window_stats(self):
        text = usage_details(
            {
                "five_hour": {
                    "utilization": 10,
                    "window_stats": {"requests": 3, "tokens": 1200, "cost": 0.02},
                }
            }
        )
        self.assertIn("10.0%", text)
        self.assertIn("1200", text)
        self.assertIn("3 次", text)
        self.assertIn("7天已用：暂无", text)


if __name__ == "__main__":
    unittest.main()
