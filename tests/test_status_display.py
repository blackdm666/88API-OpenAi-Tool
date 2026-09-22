import unittest
from unittest.mock import Mock

from token_manager.gui_records import GUIRecordsMixin


class LocalStatusDisplayTest(unittest.TestCase):
    def test_upload_status_is_short_and_uses_remote_id(self):
        app = GUIRecordsMixin()
        app.local_remote_record = Mock(
            return_value={
                "id": 432,
                "credentials": {},
            }
        )

        self.assertEqual(
            app.upload_summary({"email": "account@example.test"}),
            "已上传 #432",
        )

    def test_upload_status_without_remote_match_is_not_a_diagnostic_sentence(self):
        app = GUIRecordsMixin()
        app.local_remote_record = Mock(return_value=None)
        app.sub2api_snapshot_server = "https://sub2api.example.test"

        self.assertEqual(
            app.upload_summary({"email": "account@example.test"}),
            "未上传",
        )

    def test_monitoring_status_only_exposes_enrollment(self):
        app = GUIRecordsMixin()

        self.assertEqual(
            app.recovery_summary(
                {"sub2api_recovery": {"enabled": True, "status": "需要重新授权"}}
            ),
            "监控中",
        )
        self.assertEqual(
            app.recovery_summary(
                {"sub2api_recovery": {"enabled": False, "status": "测试未通过"}}
            ),
            "未监控",
        )


if __name__ == "__main__":
    unittest.main()
